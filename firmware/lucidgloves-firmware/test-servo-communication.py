import asyncio
from bleak import BleakScanner, BleakClient

# ── Device config ─────────────────────────────────────────────────────────────
DEVICE_NAME = "wearable_glove"

NUS_TX_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  # ESP32 → Mac (notify)
NUS_RX_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  # Mac   → ESP32 (write)

# ── Servo state ───────────────────────────────────────────────────────────────
# 0    = fully blocked
# 1000 = full freedom
servo = {'thumb': 1000, 'index': 1000, 'middle': 1000, 'ring': 1000, 'pinky': 1000}
FINGER_NAMES = list(servo.keys())

stop_event = asyncio.Event()

def format_haptic(s):
    return f"A{s['thumb']}B{s['index']}C{s['middle']}D{s['ring']}E{s['pinky']}\n"

def print_status():
    print("\n── Current servo positions ──────────────────")
    print("   (0 = blocked, 1000 = full freedom)")
    for i, name in enumerate(FINGER_NAMES):
        bar = '█' * (servo[name] // 50) + '░' * (20 - servo[name] // 50)
        print(f"  {i+1}. {name:<8} [{bar}] {servo[name]:4d}")
    print()

def print_help():
    print("Commands:")
    print("  1-5 <value>   Set finger (1=thumb … 5=pinky) to value 0–1000")
    print("  all <value>   Set all fingers to value")
    print("  free          All fingers to 1000 (full freedom)")
    print("  block         All fingers to 0 (fully blocked)")
    print("  status        Show current positions")
    print("  quit          Disconnect and exit")
    print()

def on_disconnect(client: BleakClient):
    print("\n[BLE] ESP32 disconnected.")
    stop_event.set()

def is_nus_ready(client: BleakClient) -> bool:
    """Check that service discovery has completed and NUS RX is available."""
    try:
        svcs = client.services
        if svcs is None:
            return False
        for svc in svcs:
            for char in svc.characteristics:
                if char.uuid.lower() == NUS_RX_UUID.lower():
                    return True
        return False
    except Exception:
        return False

async def wait_for_services(client: BleakClient, timeout: float = 10.0) -> bool:
    """Poll until NUS RX characteristic is visible or timeout."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if is_nus_ready(client):
            return True
        await asyncio.sleep(0.2)
    return False

async def safe_write(client: BleakClient, data: bytes) -> bool:
    """Write without response (fire-and-forget) — more stable on macOS BLE."""
    for attempt in range(3):
        try:
            await client.write_gatt_char(NUS_RX_UUID, data, response=False)
            return True
        except Exception as e:
            if attempt < 2:
                await asyncio.sleep(0.15)
            else:
                print(f"[BLE] Write failed after 3 attempts: {e}")
                return False
    return False

async def input_loop(client):
    print_help()
    print_status()

    while not stop_event.is_set():
        try:
            raw = await asyncio.to_thread(input, "servo> ")
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            stop_event.set()
            return

        if stop_event.is_set():
            return

        parts = raw.strip().lower().split()
        if not parts:
            continue

        cmd = parts[0]

        if cmd == 'quit':
            stop_event.set()
            return
        elif cmd == 'status':
            print_status()
        elif cmd == 'free':
            for k in servo: servo[k] = 1000
            print("All fingers FREE (1000)")
        elif cmd == 'block':
            for k in servo: servo[k] = 0
            print("All fingers BLOCKED (0)")
        elif cmd == 'all':
            if len(parts) < 2:
                print("Usage: all <0-1000>")
                continue
            try:
                val = max(0, min(1000, int(parts[1])))
                for k in servo: servo[k] = val
                print(f"All fingers set to {val}")
            except ValueError:
                print("Value must be an integer 0–1000")
        elif cmd in ('1', '2', '3', '4', '5'):
            if len(parts) < 2:
                print(f"Usage: {cmd} <0-1000>")
                continue
            try:
                val = max(0, min(1000, int(parts[1])))
                finger = FINGER_NAMES[int(cmd) - 1]
                servo[finger] = val
                print(f"{finger} set to {val}")
            except ValueError:
                print("Value must be an integer 0–1000")
        else:
            print(f"Unknown command: '{cmd}'")
            print_help()
            continue

        if not stop_event.is_set():
            ok = await safe_write(client, format_haptic(servo).encode())
            if not ok:
                stop_event.set()

async def send_loop(client):
    while not stop_event.is_set():
        ok = await safe_write(client, format_haptic(servo).encode())
        if not ok:
            stop_event.set()
            break
        await asyncio.sleep(0.2)

async def run():
    global stop_event
    stop_event = asyncio.Event()

    print(f"Scanning for '{DEVICE_NAME}'...")
    device = await BleakScanner.find_device_by_name(DEVICE_NAME, timeout=10.0)

    if device is None:
        print(f"ERROR: '{DEVICE_NAME}' not found. Is the ESP32 powered?")
        return

    print(f"Found: {device.name}  [{device.address}]")

    async with BleakClient(device, disconnected_callback=on_disconnect) as client:
        print("Connected! Waiting for service discovery...")

        # Wait until NUS RX characteristic is actually visible
        ready = await wait_for_services(client, timeout=10.0)
        if not ready:
            print("ERROR: NUS service not found after 10s. Check firmware.")
            return

        print("Services ready.\n")

        try:
            await client.start_notify(NUS_TX_UUID, lambda s, d: None)
        except Exception as e:
            print(f"[BLE] Could not subscribe to TX: {e}")

        try:
            await asyncio.gather(
                send_loop(client),
                input_loop(client),
                stop_event.wait(),
            )
        except Exception as e:
            print(f"[BLE] Unexpected error: {e}")
        finally:
            print("[BLE] Cleaning up...")
            try:
                await client.stop_notify(NUS_TX_UUID)
            except Exception:
                pass
            try:
                await client.disconnect()
            except Exception:
                pass
            print("[BLE] Done. ESP32 will restart advertising.")

if __name__ == "__main__":
    asyncio.run(run())