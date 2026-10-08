# main.py

import socket
import time
import _thread
import json
from machine import UART, Pin
from DNS_Server import CaptiveDNS

# ---------------------------------------------------------------------
# 1. UART & RS485 SETUP
# ---------------------------------------------------------------------

uart = UART(2, baudrate=115200, tx=Pin(19), rx=Pin(18), timeout=50)

# RS485 DE/RE Direction Pin (GPIO 21)
de_pin = Pin(21, Pin.OUT) if 21 is not None else None
if de_pin:
    de_pin.value(0)

# DDSM115 Raw Byte Commands (ID = 0x01)
CMD_SET_VEL_MODE = bytes([0x01, 0xA0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x02])
CMD_STOP         = bytes([0x01, 0x64, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x50]) # 0 RPM
CMD_QUERY_STATUS = bytes([0x01, 0x74, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x04]) # Protocol 2 Query

motor_temp = 0      # Temperature in °C
is_moving = False
move_start_ticks = 0
move_duration_ms = 0
current_speed = 0

def send_rs485(data: bytes) -> bytes:
    while uart.any():
        uart.read()
    
    if de_pin:
        de_pin.value(1)
    uart.write(data)
    
    if hasattr(uart, 'txdone'):
        while not uart.txdone():
            pass
    else:
        time.sleep_ms(2)
        
    if de_pin:
        de_pin.value(0)
        
    time.sleep_ms(5)
    return uart.read(10)

def get_speed_command(speed_rpm: int, motor_id: int = 1) -> bytes:
    """Dynamically generates a DDSM115 speed command packet with valid CRC-8/MAXIM."""
    if speed_rpm > 330: speed_rpm = 330
    if speed_rpm < -330: speed_rpm = -330
    
    speed_bytes = int(speed_rpm).to_bytes(2, 'big', signed=True)
    
    data = bytearray(9)
    data[0] = motor_id
    data[1] = 0x64
    data[2] = speed_bytes[0]
    data[3] = speed_bytes[1]
    data[4] = 0x00
    data[5] = 0x00
    data[6] = 0x00
    data[7] = 0x00
    data[8] = 0x00
    
    # CRC-8/MAXIM calculation
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8C if (crc & 0x01) else (crc >> 1)
            
    return bytes(data) + bytes([crc])

# Initialize motor to velocity mode
send_rs485(CMD_SET_VEL_MODE)

# ---------------------------------------------------------------------
# 2. MOTOR CONTROL & TELEMETRY LOOP
# ---------------------------------------------------------------------

def motor_loop():
    global is_moving, current_speed, motor_temp, move_start_ticks, move_duration_ms
    query_counter = 0
    
    while True:
        if is_moving:
            # Check if travel duration has elapsed
            if time.ticks_diff(time.ticks_ms(), move_start_ticks) >= move_duration_ms:
                is_moving = False
                current_speed = 0
                send_rs485(CMD_STOP)
            else:
                cmd = get_speed_command(current_speed)
                send_rs485(cmd)
        else:
            send_rs485(CMD_STOP)
            
        # Every 250ms (5 cycles), query temperature
        query_counter += 1
        if query_counter >= 5:
            query_counter = 0
            res = send_rs485(CMD_QUERY_STATUS)
            if res and len(res) == 10:
                motor_temp = res[6] # DATA[6] = Winding Temp in °C
                
        time.sleep_ms(50)

_thread.start_new_thread(motor_loop, ())

# ---------------------------------------------------------------------
# 3. START CAPTIVE PORTAL DNS
# ---------------------------------------------------------------------

dns = CaptiveDNS("192.168.4.1")
dns.start()

# ---------------------------------------------------------------------
# 4. HTML CONTROL PAGE
# ---------------------------------------------------------------------

HTML_PAGE = """<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DDSM115 Distance Control</title>
<style>
        body { font-family: -apple-system, sans-serif; background: #121212; color: #fff; text-align: center; margin: 0; padding: 20px; }
        .card { background: #1e1e1e; padding: 25px 20px; border-radius: 16px; max-width: 360px; margin: 20px auto; box-shadow: 0 4px 20px rgba(0,0,0,0.5); }
        h2 { margin-top: 0; color: #aaa; font-size: 1.2rem; text-transform: uppercase; letter-spacing: 1px; }
        .status { font-size: 1.4rem; font-weight: bold; margin: 10px 0; color: #888; }
        .status.active { color: #00e676; }
        .temp-box { background: #2a2a2a; border-radius: 10px; padding: 10px; margin: 10px 0 20px 0; font-size: 1rem; color: #ffb74d; }
        .temp-val { font-size: 1.2rem; font-weight: bold; color: #ffa726; }
        .input-group { margin: 15px 0; text-align: left; }
        label { display: block; font-size: 0.85rem; color: #aaa; margin-bottom: 5px; }
        input { width: 100%; padding: 12px; box-sizing: border-box; background: #2a2a2a; border: 1px solid #444; color: #fff; border-radius: 8px; font-size: 1rem; }
        .btn { width: 100%; padding: 15px; margin: 8px 0; border: none; border-radius: 12px; font-size: 1.1rem; font-weight: bold; cursor: pointer; }
        .btn-fwd { background: #00e676; color: #000; }
        .btn-rev { background: #2979ff; color: #fff; }
        .btn-stop { background: #ff1744; color: #fff; }
</style>
</head>
<body>
<div class="card">
    <h2>DDSM115 Controller</h2>
    <div id="st" class="status">STOPPED</div>
    <div class="temp-box">Winding Temp: <span id="temp" class="temp-val">--</span> °C</div>
    
    <div class="input-group">
        <label>Distance (Meters)</label>
        <input type="number" id="dist" value="1.0" step="0.1" min="0.01">
    </div>
    
    <div class="input-group">
        <label>Speed (RPM, max 330)</label>
        <input type="number" id="speed" value="50" step="1" min="1" max="330">
    </div>
    
    <button class="btn btn-fwd" onclick="move(1)">MOVE FORWARD</button>
    <button class="btn btn-rev" onclick="move(-1)">MOVE BACKWARD</button>
    <button class="btn btn-stop" onclick="stopMotor()">STOP</button>
</div>
<script>
    function move(dir) {
        let d = document.getElementById('dist').value;
        let s = document.getElementById('speed').value;
        fetch(`/move?dist=${d}&speed=${s}&dir=${dir}`).then(r => r.json()).then(u);
    }
    function stopMotor() {
        fetch('/stop').then(r => r.json()).then(u);
    }
    function u() {
        fetch('/state').then(r => r.json()).then(d => {
            let e = document.getElementById('st');
            e.innerText = d.state;
            e.className = 'status' + (d.state === 'RUNNING' ? ' active' : '');
            document.getElementById('temp').innerText = d.temp;
        });
    }
    setInterval(u, 500);
    u();
</script>
</body>
</html>
"""

# ---------------------------------------------------------------------
# 5. HTTP SERVER & ROUTING
# ---------------------------------------------------------------------

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
            try:
                query = path.split('?')[1]
                params = {}
                for pair in query.split('&'):
                    if '=' in pair:
                        k, v = pair.split('=')
                        params[k] = v
                
                dist = float(params.get('dist', '1.0'))
                speed = int(params.get('speed', '50'))
                direction = int(params.get('dir', '1')) # 1 = Forward, -1 = Backward
                
                # Math: Wheel diameter = 100.7 mm -> Circumference = pi * 0.1007 m
                circumference = 3.14159265 * 0.1007
                revolutions = dist / circumference
                duration_sec = (revolutions * 60.0) / speed
                
                move_duration_ms = int(duration_sec * 1000)
                current_speed = speed * direction
                move_start_ticks = time.ticks_ms()
                is_moving = True
                
                response = json.dumps({"state": "RUNNING", "temp": motor_temp})
            except Exception as e:
                response = json.dumps({"state": "ERROR", "error": str(e)})
                
        elif path == '/stop':
            is_moving = False
            current_speed = 0
            send_rs485(CMD_STOP)
            response = json.dumps({"state": "STOPPED", "temp": motor_temp})
            
        elif path == '/state':
            state_str = "RUNNING" if is_moving else "STOPPED"
            response = json.dumps({"state": state_str, "temp": motor_temp})
            
        else:
            # Captive portal fallback: serve HTML page
            conn.send('HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n' + HTML_PAGE)
            conn.close()
            continue
            
        conn.send('HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n' + response)
        conn.close()
        
    except Exception:
        try:
            conn.close()
        except:
            pass
