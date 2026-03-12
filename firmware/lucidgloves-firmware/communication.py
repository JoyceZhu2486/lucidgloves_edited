import serial
import re
import time

PORT = '/dev/cu.usbserial-0001' # Change to your port
BAUD_RATE = 115200

# --- PER-FINGER CALIBRATION LIMITS ---
# Replace these [MIN, MAX] numbers with the exact RAW values 
# you see when your hand is fully open (MIN) and fully closed (MAX).
# Note: If your finger goes DOWN when you close it, just swap the numbers!
FINGER_LIMITS = {
    'A': [2600, 4000], # Thumb  [Open, Closed]
    'B': [2600, 4000], # Index  [Open, Closed]
    'C': [2600, 4000], # Middle [Open, Closed]
    'D': [2600, 4000], # Ring   [Open, Closed]
    'E': [2600, 4000]  # Pinky  [Open, Closed]
}

def parse_raw_data(data_str):
    """ Parses the RAW,timestamp,T,I,M,R,P stream """
    parts = data_str.split(',')
    if len(parts) >= 7 and parts[0] == "RAW":
        return {
            'A': int(parts[2]), 'B': int(parts[3]), 
            'C': int(parts[4]), 'D': int(parts[5]), 'E': int(parts[6])
        }
    return None

def map_range(value, in_min, in_max, out_min=0, out_max=100):
    """ Maps raw values cleanly between 0 and 100% """
    # Handle inverted limits (if sensor values go down when closing)
    is_inverted = in_min > in_max
    if is_inverted:
        in_min, in_max = in_max, in_min
        out_min, out_max = out_max, out_min

    value = max(in_min, min(value, in_max))
    if in_max == in_min: return 0 
    
    return int((value - in_min) * (out_max - out_min) / (in_max - in_min) + out_min)

def get_mapped_finger(raw_val, finger_letter):
    """ Helper to fetch the correct limits and map the value """
    limits = FINGER_LIMITS.get(finger_letter, [0, 4095])
    return map_range(raw_val, limits[0], limits[1])

def format_haptic_command(thumb=0, index=0, middle=0, ring=0, pinky=0):
    return f"A{thumb}B{index}C{middle}D{ring}E{pinky}\n"

def main():
    try:
        ser = serial.Serial(PORT, BAUD_RATE, timeout=0.05)
        print(f"Connected to {PORT} at {BAUD_RATE} baud.")
    except Exception as e:
        print(f"Failed to connect. Error: {e}")
        return

    next_output_command = format_haptic_command()

    try:
        while True:
            ser.write(next_output_command.encode('utf-8'))

            if ser.in_waiting > 0:
                raw_line = ser.readline().decode('utf-8', errors='ignore').strip()
                
                if raw_line.startswith('RAW'):
                    glove_data = parse_raw_data(raw_line)
                    if glove_data:
                        raw_thumb, raw_index = glove_data['A'], glove_data['B']
                        raw_middle, raw_ring = glove_data['C'], glove_data['D']
                        raw_pinky = glove_data['E']

                        # Map values using specific limits
                        pct_thumb  = get_mapped_finger(raw_thumb, 'A')
                        pct_index  = get_mapped_finger(raw_index, 'B')
                        pct_middle = get_mapped_finger(raw_middle, 'C')
                        pct_ring   = get_mapped_finger(raw_ring, 'D')
                        pct_pinky  = get_mapped_finger(raw_pinky, 'E')

                        print(f"RAW  | T:{raw_thumb:4} I:{raw_index:4} M:{raw_middle:4} R:{raw_ring:4} P:{raw_pinky:4}")
                        print(f"MAP% | T:{pct_thumb:3}% I:{pct_index:3}% M:{pct_middle:3}% R:{pct_ring:3}% P:{pct_pinky:3}%")
                        print("-" * 50)
            
            time.sleep(0.01)

    except KeyboardInterrupt:
        print("\nScript terminated.")
    finally:
        if 'ser' in locals() and ser.is_open: ser.close()

if __name__ == "__main__":
    main()