#!/usr/bin/env python3
"""
glove_monitor.py — Live sensor display + interactive servo control over BLE

Layout:
  ┌─────────────────────────────────────────┐
  │  GLOVE SENSOR DATA (live)               │
  │  Thumb  ████████░░░░  raw:3200  75%     │
  │  ...                                    │
  ├─────────────────────────────────────────│
  │  SERVO POSITIONS                        │
  │  Thumb  ████████████  1000              │
  │  ...                                    │
  ├─────────────────────────────────────────│
  │  servo> _                               │
  │  Commands: 1-5 <val>  all  free  tighten  lock │
  └─────────────────────────────────────────┘

Usage:
  python glove_monitor.py
"""

import asyncio
import curses
import threading

from bleak import BleakScanner, BleakClient

# ── BLE config ────────────────────────────────────────────────────────────────
DEVICE_NAME = "wearable_glove"
NUS_TX_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
NUS_RX_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"

FINGER_NAMES = ["Thumb", "Index", "Middle", "Ring", "Pinky"]

# ── Per-finger limits [open_adc, closed_adc] ─────────────────────────────────
# Used both for the sensor % display and for the lock command.
FINGER_LIMITS = {
    "Thumb":  [2600, 4000],
    "Index":  [1000, 3800],
    "Middle": [2400, 4000],
    "Ring":   [1950, 4000],
    "Pinky":  [2050, 4000],
}
# ── Shared state (accessed from both BLE thread and curses thread) ────────────
lock         = threading.Lock()
raw_values   = [0] * 5          # latest ADC readings from glove
servo_values = [1000] * 5       # current servo command (0=taut, 1000=free)
rx_buf       = ""               # BLE fragment buffer
ble_status   = "Scanning..."    # shown in UI header
stop_event   = asyncio.Event()

# ── Helpers ───────────────────────────────────────────────────────────────────

def map_pct(raw: int, finger: str) -> float:
    """Map raw ADC to 0.0 (open) – 1.0 (closed) using FINGER_LIMITS."""
    lo, hi = FINGER_LIMITS[finger]
    if lo > hi:
        lo, hi = hi, lo
    raw = max(lo, min(raw, hi))
    return (raw - lo) / (hi - lo) if hi != lo else 0.0

def parse_raw_line(line: str):
    parts = line.split(',')
    if len(parts) >= 7 and parts[0] == "RAW":
        try:
            return [int(parts[i]) for i in range(2, 7)]
        except ValueError:
            pass
    return None

def format_haptic(values: list) -> bytes:
    v = [max(0, min(1000, int(x))) for x in values]
    return f"A{v[0]}B{v[1]}C{v[2]}D{v[3]}E{v[4]}\n".encode()

def bar(value: float, width: int = 16) -> str:
    filled = int(value * width)
    return '█' * filled + '░' * (width - filled)

def bar_int(value: int, max_val: int = 1000, width: int = 16) -> str:
    filled = int((value / max_val) * width)
    return '█' * filled + '░' * (width - filled)

# ── Lock ──────────────────────────────────────────────────────────────────────

def lock_at_current_position() -> str:
    """
    Command each servo to the position matching the finger's current curl.
    Uses FINGER_LIMITS to convert raw ADC → 0.0–1.0, then scales to 0–1000.
      0%  (open)   → cmd 0    (servo holds at open end)
      50% (mid)    → cmd 500
      100%(closed) → cmd 1000 (servo holds at closed end)
    """
    with lock:
        raw = list(raw_values)

    cmds = []
    msgs = []
    for i, name in enumerate(FINGER_NAMES):
        pct = map_pct(raw[i], name)
        cmd = max(0, min(1000, int((1.0 - pct) * 1000)))
        cmds.append(cmd)
        msgs.append(f"{name}:{cmd}")

    with lock:
        servo_values[:] = cmds

    return "Locked: " + "  ".join(msgs)

# ── BLE ───────────────────────────────────────────────────────────────────────

def on_notify(sender, data: bytearray):
    global rx_buf
    rx_buf += data.decode("utf-8", errors="ignore")
    while "\n" in rx_buf:
        line, rx_buf = rx_buf.split("\n", 1)
        line = line.strip()
        if not line:
            continue
        values = parse_raw_line(line)
        if values:
            with lock:
                raw_values[:] = values

def on_disconnect(client: BleakClient):
    global ble_status
    with lock:
        ble_status = "Disconnected — reconnecting..."
    stop_event.set()

def is_nus_ready(client: BleakClient) -> bool:
    try:
        for svc in client.services:
            for char in svc.characteristics:
                if char.uuid.lower() == NUS_RX_UUID.lower():
                    return True
    except Exception:
        pass
    return False

async def safe_write(client: BleakClient, data: bytes) -> bool:
    for attempt in range(3):
        try:
            await client.write_gatt_char(NUS_RX_UUID, data, response=False)
            return True
        except Exception:
            if attempt < 2:
                await asyncio.sleep(0.15)
    return False

async def heartbeat_loop(client: BleakClient):
    """Continuously send current servo_values to the glove at ~5 Hz."""
    while not stop_event.is_set():
        with lock:
            values = list(servo_values)
        ok = await safe_write(client, format_haptic(values))
        if not ok:
            stop_event.set()
            break
        await asyncio.sleep(0.2)

async def ble_main():
    global ble_status
    while True:
        stop_event.clear()
        with lock:
            ble_status = f"Scanning for '{DEVICE_NAME}'..."

        device = await BleakScanner.find_device_by_name(
            DEVICE_NAME, timeout=10.0)

        if device is None:
            with lock:
                ble_status = "Device not found. Retrying in 5s..."
            await asyncio.sleep(5.0)
            continue

        with lock:
            ble_status = f"Connecting to {device.name}..."

        try:
            async with BleakClient(
                device, disconnected_callback=on_disconnect
            ) as client:

                deadline = asyncio.get_event_loop().time() + 10.0
                while asyncio.get_event_loop().time() < deadline:
                    if is_nus_ready(client):
                        break
                    await asyncio.sleep(0.2)
                else:
                    with lock:
                        ble_status = "Service discovery timed out."
                    continue

                with lock:
                    ble_status = f"Connected  [{device.address}]"

                await client.start_notify(NUS_TX_UUID, on_notify)
                await heartbeat_loop(client)
                try:
                    await client.stop_notify(NUS_TX_UUID)
                except Exception:
                    pass

        except Exception as e:
            with lock:
                ble_status = f"Error: {e}"

        await asyncio.sleep(3.0)

def run_ble():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(ble_main())

# ── Curses UI ─────────────────────────────────────────────────────────────────

def parse_command(cmd: str) -> str:
    """Parse a servo command string, update servo_values, return status message."""
    parts = cmd.strip().lower().split()
    if not parts:
        return ""

    c = parts[0]

    if c == 'lock':
        return lock_at_current_position()

    elif c == 'free':
        with lock:
            servo_values[:] = [1000] * 5
        return "All fingers FREE (1000)"

    elif c == 'tighten':
        with lock:
            servo_values[:] = [0] * 5
        return "All fingers TIGHTENED (0)"

    elif c == 'all':
        if len(parts) < 2:
            return "Usage: all <0-1000>"
        try:
            val = max(0, min(1000, int(parts[1])))
            with lock:
                servo_values[:] = [val] * 5
            return f"All fingers set to {val}"
        except ValueError:
            return "Value must be 0–1000"

    elif c in ('1', '2', '3', '4', '5'):
        if len(parts) < 2:
            return f"Usage: {c} <0-1000>"
        try:
            val = max(0, min(1000, int(parts[1])))
            idx = int(c) - 1
            with lock:
                servo_values[idx] = val
            return f"{FINGER_NAMES[idx]} set to {val}"
        except ValueError:
            return "Value must be 0–1000"

    elif c == 'quit':
        return "__QUIT__"

    # Multi-finger command: e.g. "1:1000, 2:500, 4:0"
    elif any(':' in p for p in parts) or ':' in cmd:
        with lock:
            new_vals = list(servo_values)
        updated = []
        errors  = []
        for token in cmd.replace(',', ' ').split():
            if ':' not in token:
                continue
            try:
                idx_str, val_str = token.split(':', 1)
                idx = int(idx_str.strip()) - 1
                val = max(0, min(1000, int(val_str.strip())))
                if 0 <= idx <= 4:
                    new_vals[idx] = val
                    updated.append(f"{FINGER_NAMES[idx]}={val}")
                else:
                    errors.append(f"finger {idx+1} out of range")
            except ValueError:
                errors.append(f"bad token '{token}'")
        if updated:
            with lock:
                servo_values[:] = new_vals
            msg = "Set: " + ", ".join(updated)
            if errors:
                msg += "  ERR: " + ", ".join(errors)
            return msg
        return "Usage: 1:val, 2:val ...  e.g. 1:1000, 3:0, 5:500"

    else:
        return f"Unknown: '{c}'  |  1-5 <val>  all  free  tighten  lock  1:v,2:v,...  quit"


def curses_main(stdscr):
    curses.curs_set(1)
    curses.noecho()
    stdscr.nodelay(False)

    curses.start_color()
    curses.init_pair(1, curses.COLOR_CYAN,   curses.COLOR_BLACK)  # header
    curses.init_pair(2, curses.COLOR_GREEN,  curses.COLOR_BLACK)  # sensor bars
    curses.init_pair(3, curses.COLOR_YELLOW, curses.COLOR_BLACK)  # servo bars
    curses.init_pair(4, curses.COLOR_WHITE,  curses.COLOR_BLACK)  # normal
    curses.init_pair(5, curses.COLOR_RED,    curses.COLOR_BLACK)  # error

    rows, cols = stdscr.getmaxyx()

    HEADER_H = 2
    SENSOR_H = len(FINGER_NAMES) + 2
    SERVO_H  = len(FINGER_NAMES) + 2
    CMD_H    = 3

    sensor_win = curses.newwin(SENSOR_H, cols, HEADER_H, 0)
    servo_win  = curses.newwin(SERVO_H,  cols, HEADER_H + SENSOR_H, 0)
    cmd_win    = curses.newwin(CMD_H,    cols, HEADER_H + SENSOR_H + SERVO_H, 0)

    status_msg = ""
    input_buf  = ""

    while True:
        rows, cols = stdscr.getmaxyx()
        bar_w = max(8, min(20, cols - 32))

        # ── Header ────────────────────────────────────────────────────────────
        stdscr.erase()
        with lock:
            ble_st = ble_status
        stdscr.addstr(0, 0, " WEARABLE GLOVE MONITOR ", curses.color_pair(1) | curses.A_BOLD)
        stdscr.addstr(1, 0, f" {ble_st}"[:cols-1], curses.color_pair(4))

        # ── Sensor panel ──────────────────────────────────────────────────────
        sensor_win.erase()
        sensor_win.addstr(0, 0, "─── SENSOR DATA (RAW / %) " + "─" * max(0, cols - 27),
                          curses.color_pair(1))
        with lock:
            raw = list(raw_values)

        for i, name in enumerate(FINGER_NAMES):
            pct = map_pct(raw[i], name)
            b   = bar(pct, bar_w)
            line = f"  {name:<7} [{b}]  raw:{raw[i]:4d}  {pct*100:5.1f}%"
            sensor_win.addstr(i + 1, 0, line[:cols-1], curses.color_pair(2))

        # ── Servo panel ───────────────────────────────────────────────────────
        servo_win.erase()
        servo_win.addstr(0, 0, "─── SERVO COMMANDS (0=blocked, 1000=free) " + "─" * max(0, cols - 43),
                         curses.color_pair(1))
        with lock:
            sv = list(servo_values)

        for i, name in enumerate(FINGER_NAMES):
            b    = bar_int(sv[i], 1000, bar_w)
            line = f"  {name:<7} [{b}]  {sv[i]:4d}"
            servo_win.addstr(i + 1, 0, line[:cols-1], curses.color_pair(3))

        # ── Command input ─────────────────────────────────────────────────────
        cmd_win.erase()
        cmd_win.addstr(0, 0, "─── COMMAND " + "─" * max(0, cols - 13),
                       curses.color_pair(1))
        prompt = f" servo> {input_buf}"
        cmd_win.addstr(1, 0, prompt[:cols-1], curses.color_pair(4))
        if status_msg:
            is_err = any(w in status_msg for w in ("Unknown", "Usage", "failed"))
            cmd_win.addstr(2, 0, f" {status_msg}"[:cols-1],
                           curses.color_pair(5) if is_err else curses.color_pair(2))

        stdscr.refresh()
        sensor_win.refresh()
        servo_win.refresh()
        cmd_win.refresh()

        # ── Handle keypress ───────────────────────────────────────────────────
        cmd_win.timeout(100)
        try:
            ch = cmd_win.get_wch()
        except curses.error:
            continue

        if ch == '\n' or ch == curses.KEY_ENTER or ch == 10 or ch == 13:
            result = parse_command(input_buf)
            if result == "__QUIT__":
                break
            status_msg = result
            input_buf = ""

        elif ch == curses.KEY_BACKSPACE or ch == 127 or ch == '\x7f':
            input_buf = input_buf[:-1]
            status_msg = ""

        elif isinstance(ch, str) and ch.isprintable():
            input_buf += ch
            status_msg = ""

        elif ch == curses.KEY_RESIZE:
            stdscr.clear()
            rows, cols = stdscr.getmaxyx()
            sensor_win.resize(SENSOR_H, cols)
            servo_win.resize(SERVO_H, cols)
            servo_win.mvwin(HEADER_H + SENSOR_H, 0)
            cmd_win.resize(CMD_H, cols)
            cmd_win.mvwin(HEADER_H + SENSOR_H + SERVO_H, 0)


def main():
    ble_thread = threading.Thread(target=run_ble, daemon=True)
    ble_thread.start()

    try:
        curses.wrapper(curses_main)
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        print("Exited.")


if __name__ == "__main__":
    main()
