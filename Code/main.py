# main.py - Simplified DDSM115 + PCA9685 Servo Control
import time
time.sleep(1)

import socket
import _thread
import json
from machine import UART, Pin, I2C
from DNS_Server import CaptiveDNS

print("_____________________________________________________")
print("Simplified DDSM115 + PCA9685 Control v4")

# ---------------------------------------------------------------------
# 1. UART & RS485 SETUP (DDSM115 Motors)
# ---------------------------------------------------------------------

uart = UART(2, baudrate=115200, tx=Pin(19), rx=Pin(18), timeout=50)
de_pin = Pin(21, Pin.OUT)
de_pin.value(0)

bus_lock = _thread.allocate_lock()
motor_ids = [1, 2, 3]  
motor_temp = 0
is_moving = False
move_start_ticks = 0
move_duration_ms = 0
current_speed = 50

# ---------------------------------------------------------------------
# 2. I2C & PCA9685 SETUP (6 Servos)
# ---------------------------------------------------------------------
# Using standard ESP32 I2C pins: SCL=Pin(22), SDA=Pin(21) - note: if Pin 21 is used for RS485 DE, change I2C pins accordingly (e.g., SDA=23, SCL=22)
i2c = I2C(0, scl=Pin(22), sda=Pin(23), freq=50000)
PCA9685_ADDR = 0x40

def init_pca9685():
    try:
        i2c.writeto_mem(PCA9685_ADDR, 0x00, b'\x20') # Sleep mode to set prescale
        time.sleep_ms(5)
        i2c.writeto_mem(PCA9685_ADDR, 0xFE, b'\x79') # Set PWM frequency to ~50Hz for servos
        i2c.writeto_mem(PCA9685_ADDR, 0x00, b'\xA1') # Normal mode, auto-increment
        print("PCA9685 Initialized successfully.")
    except Exception as e:
        print("PCA9685 initialization failed:", e)

init_pca9685()

def set_servo_angle(channel, angle):
    """Set servo angle (0 to 180 degrees) on a specific channel (0-5)."""
    angle = max(0, min(180, angle))
    # Map angle (0-180) to pulse width ticks (approx 150 to 600 for standard servos)
    pulse = int(150 + (angle / 180.0) * 450)
    on_val = 0
    off_val = pulse
    
    data = bytes([on_val & 0xFF, on_val >> 8, off_val & 0xFF, off_val >> 8])
    reg = 0x06 + (4 * channel)
    try:
        with bus_lock:
            i2c.writeto_mem(PCA9685_ADDR, reg, data)
    except Exception as e:
        print(f"Error setting servo {channel}:", e)

# ---------------------------------------------------------------------
# RS485 Motor Helpers
# ---------------------------------------------------------------------

def crc8_maxim(data) -> int:
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8C if (crc & 0x01) else (crc >> 1)
    return crc

def with_crc(data9) -> bytes:
    return bytes(data9) + bytes([crc8_maxim(data9)])

def cmd_set_mode(mid: int, mode: int = 0x02) -> bytes:
    return bytes([mid, 0xA0, 0, 0, 0, 0, 0, 0, 0, mode])

def cmd_speed(mid: int, rpm: int) -> bytes:
    rpm = max(-330, min(330, int(rpm)))
    sb = rpm.to_bytes(2, 'big', signed=True)
    return with_crc([mid, 0x64, sb[0], sb[1], 0, 0, 0, 0, 0])

def cmd_stop(mid: int) -> bytes:
    return cmd_speed(mid, 0)

def cmd_query_status(mid: int) -> bytes:
    return with_crc([mid, 0x74, 0, 0, 0, 0, 0, 0, 0])

def _tx_rx(data: bytes, expect_reply: bool = True) -> bytes:
    while uart.any():
        uart.read()
    de_pin.value(1)
    uart.write(data)
    if hasattr(uart, 'txdone'):
        while not uart.txdone(): pass
    else:
        time.sleep_ms(2)
    de_pin.value(0)
    time.sleep_ms(5)
    return (uart.read(10) or b"") if expect_reply else b""

def send_rs485(data: bytes) -> bytes:
    with bus_lock:
        return _tx_rx(data)

def init_motors():
    for mid in motor_ids:
        send_rs485(cmd_set_mode(mid, 0x02))

def stop_all_motors():
    global is_moving, current_speed
    is_moving = False
    for mid in motor_ids:
        send_rs485(cmd_stop(mid))

init_motors()

# Motor Background Loop
def motor_loop():
    global is_moving, current_speed, motor_temp
    query_counter = 0

    while True:
        if is_moving:
            if time.ticks_diff(time.ticks_ms(), move_start_ticks) >= move_duration_ms:
                stop_all_motors()
            else:
                for mid in motor_ids:
                    send_rs485(cmd_speed(mid, current_speed))
        else:
            for mid in motor_ids:
                send_rs485(cmd_stop(mid))

        query_counter += 1
        if query_counter >= 5:
            query_counter = 0
            res = send_rs485(cmd_query_status(1))
            if res and len(res) == 10 and res[0] == 1:
                motor_temp = res[6]

        time.sleep_ms(50)

# ---------------------------------------------------------------------
# HTTP SERVER & ROUTING
# ---------------------------------------------------------------------

dns = CaptiveDNS("192.168.4.1")
dns.start()

def send_html_file(conn, filename="index.html"):
    try:
        conn.send("HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n")
        with open(filename, "r") as f:
            while True:
                chunk = f.read(512)
                if not chunk: break
                conn.send(chunk)
    except:
        conn.send("HTTP/1.1 404 Not Found\r\nContent-Type: text/plain\r\n\r\nFile Not Found")

def parse_params(path):
    params = {}
    if '?' in path:
        for pair in path.split('?', 1)[1].split('&'):
            if '=' in pair:
                k, v = pair.split('=', 1)
                params[k] = v
    return params

def state_json():
    return json.dumps({
        "state": "RUNNING" if is_moving else "STOPPED",
        "temp": motor_temp
    })
    
def webserver_loop():
    global is_moving, current_speed, move_start_ticks, move_duration_ms
 
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(('', 80))
    s.listen(5)

    while True:
        try:
            conn, addr = s.accept()
            conn.settimeout(2.0)
            req = conn.recv(1024).decode('utf-8')
            path = req.split(' ')[1] if ' ' in req else '/'
            response = "{}"

            if path.startswith('/move?'):
                params = parse_params(path)
                dist = float(params.get('dist', '1.0'))
                speed = int(params.get('speed', '50'))
                direction = int(params.get('dir', '1'))

                circumference = 3.14159265 * 0.1007
                duration_sec = ((dist / circumference) * 60.0) / speed
                move_duration_ms = int(duration_sec * 1000)
                current_speed = speed * direction
                move_start_ticks = time.ticks_ms()
                is_moving = True
                response = state_json()

            elif path.startswith('/servo?'):
                params = parse_params(path)
                ch = int(params.get('ch', '0'))
                angle = int(params.get('angle', '90'))
                set_servo_angle(ch, angle)
                response = json.dumps({"status": "ok", "channel": ch, "angle": angle})

            elif path == '/stop':
                stop_all_motors()
                response = state_json()

            elif path == '/state':
                response = state_json()

            else:
                send_html_file(conn, "index.html")
                conn.close()
                continue

            conn.send('HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n' + response)
            conn.close()
        except:
            try: conn.close()
            except: pass
            time.sleep_ms(100)
            
_thread.start_new_thread(motor_loop, ())
_thread.start_new_thread(webserver_loop, ())
print("Server started. Connect to http://")