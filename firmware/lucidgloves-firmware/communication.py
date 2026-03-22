import asyncio
import re
from bleak import BleakScanner, BleakClient

# ── Device config ─────────────────────────────────────────────────────────────
DEVICE_NAME   = "wearable_glove"   # must match BTSERIAL_DEVICE_NAME in Config.h

# Nordic UART Service UUIDs (must match BTSerialCommunication.h)
NUS_TX_UUID   = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  # ESP32 → Mac (notify)
NUS_RX_UUID   = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  # Mac   → ESP32 (write)

# ── Per-finger calibration limits ─────────────────────────────────────────────
# [Open, Closed] raw ADC values. Swap if your finger reads higher when open.
FINGER_LIMITS = {
    'A': [2600, 4000],  # Thumb
    'B': [2600, 4000],  # Index
    'C': [2600, 4000],  # Middle
    'D': [2600, 4000],  # Ring
    'E': [2600, 4000],  # Pinky
}

# ── Parsing ───────────────────────────────────────────────────────────────────

def parse_raw(data_str: str):
    """Parses RAW,timestamp,T,I,M,R,P"""
    parts = data_str.split(',')
    if len(parts) >= 7 and parts[0] == "RAW":
        try:
            return {k: int(v) for k, v in zip('ABCDE', parts[2:7])}
        except ValueError:
            pass
    return None

def map_range(value, in_min, in_max, out_min=0, out_max=100):
    if in_min > in_max:                          # inverted sensor
        in_min, in_max = in_max, in_min
        out_min, out_max = out_max, out_min
    value = max(in_min, min(value, in_max))
    if in_max == in_min:
        return 0
    return int((value - in_min) * (out_max - out_min) / (in_max - in_min) + out_min)

def get_mapped(raw_val, finger_letter):
    lo, hi = FINGER_LIMITS.get(finger_letter, [0, 4095])
    return map_range(raw_val, lo, hi)

def format_haptic(thumb=0, index=0, middle=0, ring=0, pinky=0):
    return f"A{thumb}B{index}C{middle}D{ring}E{pinky}\n"

# ── Receive buffer ─────────────────────────────────────────────────────────────
# BLE packets can be fragmented — accumulate until we see a newline.
_rx_buf = ""

def handle_notify(sender, data: bytearray):
    global _rx_buf
    _rx_buf += data.decode('utf-8', errors='ignore')

    while '\n' in _rx_buf:
        line, _rx_buf = _rx_buf.split('\n', 1)
        line = line.strip()
        if not line:
            continue

        glove = parse_raw(line)
        if glove:
            pct = {k: get_mapped(glove[k], k) for k in 'ABCDE'}
            print(f"RAW  | T:{glove['A']:4} I:{glove['B']:4} M:{glove['C']:4} "
                  f"R:{glove['D']:4} P:{glove['E']:4}")
            print(f"MAP% | T:{pct['A']:3}% I:{pct['B']:3}% M:{pct['C']:3}% "
                  f"R:{pct['D']:3}% P:{pct['E']:3}%")
            print("-" * 50)

# ── Main ──────────────────────────────────────────────────────────────────────

async def run():
    print(f"Scanning for '{DEVICE_NAME}'...")
    device = await BleakScanner.find_device_by_name(DEVICE_NAME, timeout=10.0)

    if device is None:
        print(f"ERROR: '{DEVICE_NAME}' not found. Check that the ESP32 is powered and advertising.")
        return

    print(f"Found: {device.name}  [{device.address}]")

    async with BleakClient(device) as client:
        print("Connected!")

        # Subscribe to TX notifications (ESP32 → Mac)
        await client.start_notify(NUS_TX_UUID, handle_notify)
        print("Receiving finger data. Press Ctrl+C to stop.\n")

        haptic_cmd = format_haptic().encode('utf-8')

        try:
            while True:
                # Send haptic command to ESP32 (RX characteristic)
                await client.write_gatt_char(NUS_RX_UUID, haptic_cmd, response=True)
                await asyncio.sleep(0.02)   # ~50 Hz send rate
        except KeyboardInterrupt:
            print("\nStopped by user.")
        finally:
            await client.stop_notify(NUS_TX_UUID)

if __name__ == "__main__":
    asyncio.run(run())
