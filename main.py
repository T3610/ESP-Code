# main.py - DDSM115 Multi-Motor Control for Waveshare DDSM Driver HAT (A)

import socket
import time
import _thread
import json
from machine import UART, Pin
from DNS_Server import CaptiveDNS

print("_____________________________________________________")
print("DDSM115 Multi-Motor Control - ESP32 HAT (A)")

# ---------------------------------------------------------------------
# 1. UART & RS485 SETUP
# ---------------------------------------------------------------------

uart = UART(2, baudrate=115200, tx=Pin(19), rx=Pin(18), timeout=50)
de_pin = Pin(21, Pin.OUT)
de_pin.value(0)

bus_lock = _thread.allocate_lock()   # only one thread talks on the bus at a time

motor_id = 1          # currently selected motor
motor_temp = 0
is_moving = False
move_start_ticks = 0
move_duration_ms = 0
current_speed = 0

MIN_ID, MAX_ID = 1, 127   # 0xAA / 0xC8 are reserved by the protocol


# ---------------------------------------------------------------------
# Packet helpers
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

CMD_SET_ID_TEMPLATE = lambda new_id: with_crc([0xAA, 0x55, 0x53, new_id, 0, 0, 0, 0, 0])
CMD_QUERY_ID = with_crc([0xC8, 0x64, 0, 0, 0, 0, 0, 0, 0])


# ---------------------------------------------------------------------
# RS485 transmit / receive
# ---------------------------------------------------------------------

def _tx_rx(data: bytes, expect_reply: bool = True) -> bytes:
    while uart.any():
        uart.read()

    de_pin.value(1)
    uart.write(data)
    if hasattr(uart, 'txdone'):
        while not uart.txdone():
            pass
    else:
        time.sleep_ms(2)
    de_pin.value(0)

    time.sleep_ms(5)
    return (uart.read(10) or b"") if expect_reply else b""

def send_rs485(data: bytes) -> bytes:
    with bus_lock:
        return _tx_rx(data)

def select_motor(new_id: int):
    global motor_id, is_moving, current_speed
    if new_id == motor_id:
        return
    is_moving = False
    current_speed = 0
    send_rs485(cmd_stop(motor_id))
    motor_id = new_id
    send_rs485(cmd_set_mode(motor_id, 0x02))

def set_motor_id(new_id: int):
    global is_moving, current_speed, motor_id
    is_moving = False
    current_speed = 0
    pkt = CMD_SET_ID_TEMPLATE(new_id)
    with bus_lock:
        for _ in range(5):
            _tx_rx(pkt, expect_reply=False)
            time.sleep_ms(20)
        time.sleep_ms(100)
    motor_id = new_id
    send_rs485(cmd_set_mode(motor_id, 0x02))

def query_motor_id():
    res = send_rs485(CMD_QUERY_ID)
    if res and len(res) == 10:
        return res[0]
    return None

send_rs485(cmd_set_mode(motor_id, 0x02))


# ---------------------------------------------------------------------
# 2. MOTOR CONTROL & TELEMETRY LOOP
# ---------------------------------------------------------------------

def motor_loop():
    global is_moving, current_speed, motor_temp
    query_counter = 0

    while True:
        mid = motor_id
        if is_moving:
            if time.ticks_diff(time.ticks_ms(), move_start_ticks) >= move_duration_ms:
                is_moving = False
                current_speed = 0
                send_rs485(cmd_stop(mid))
            else:
                send_rs485(cmd_speed(mid, current_speed))
        else:
            send_rs485(cmd_stop(mid))

        query_counter += 1
        if query_counter >= 5:
            query_counter = 0
            res = send_rs485(cmd_query_status(mid))
            if res and len(res) == 10 and res[0] == mid:
                motor_temp = res[6]

        time.sleep_ms(50)

_thread.start_new_thread(motor_loop, ())


# ---------------------------------------------------------------------
# 3. CAPTIVE PORTAL DNS
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
<title>DDSM115 Motor Control</title>
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
.btn-id { background: #ff9100; color: #000; font-size: 1rem; padding: 12px; }
.btn-q { background: #444; color: #fff; font-size: 1rem; padding: 12px; }
.warn { color: #ff8a80; font-size: 0.8rem; text-align: left; margin: 6px 0; }
.msg { font-size: 0.9rem; color: #80d8ff; min-height: 1.2em; }
</style>
</head>
<body>
<div class="card">
    <h2>Motor Control</h2>
    <div id="st" class="status">STOPPED</div>
    <div class="temp-box">Motor <span id="mid">--</span> &middot; Winding Temp: <span id="temp" class="temp-val">--</span> &deg;C</div>

    <div class="input-group">
        <label>Motor ID to control</label>
        <input type="number" id="mid_in" value="1" step="1" min="1" max="127">
    </div>
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

<div class="card">
    <h2>Assign Motor ID</h2>
    <div class="warn">&#9888; Connect ONLY ONE motor to the bus before assigning or querying an ID. The ID can only be set once per power-up &mdash; power-cycle the motor before changing it again.</div>
    <div class="input-group">
        <label>New ID (1&ndash;127)</label>
        <input type="number" id="newid" value="2" step="1" min="1" max="127">
    </div>
    <button class="btn btn-id" onclick="setId()">SET ID</button>
    <button class="btn btn-q" onclick="queryId()">QUERY ID OF CONNECTED MOTOR</button>
    <div id="msg" class="msg"></div>
</div>

<script>
    function showMsg(t) { document.getElementById('msg').innerText = t; }
    function move(dir) {
        let id = document.getElementById('mid_in').value;
        let d = document.getElementById('dist').value;
        let s = document.getElementById('speed').value;
        fetch(`/move?id=${id}&dist=${d}&speed=${s}&dir=${dir}`).then(r => r.json()).then(u);
    }
    function stopMotor() { fetch('/stop').then(r => r.json()).then(u); }

    function setId() {
        let n = document.getElementById('newid').value;
        if (!confirm('Is ONLY ONE motor connected? Set its ID to ' + n + '?')) return;
        showMsg('Setting ID...');
        fetch(`/setid?id=${n}`).then(r => r.json()).then(d => {
            showMsg(d.error ? 'Error: ' + d.error : 'ID sent. Motor ' + d.id + ' selected. Use Query to verify.');
            document.getElementById('mid_in').value = d.id || n;
            u();
        });
    }
    function queryId() {
        showMsg('Querying...');
        fetch('/queryid').then(r => r.json()).then(d => {
            showMsg(d.id ? 'Motor on bus has ID ' + d.id : 'No reply (check wiring / more than one motor?)');
        });
    }
    function u() {
        fetch('/state').then(r => r.json()).then(d => {
            let e = document.getElementById('st');
            e.innerText = d.state;
            e.className = 'status' + (d.state === 'RUNNING' ? ' active' : '');
            document.getElementById('temp').innerText = d.temp;
            document.getElementById('mid').innerText = d.id;
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

def parse_params(path):
    params = {}
    if '?' in path:
        for pair in path.split('?', 1)[1].split('&'):
            if '=' in pair:
                k, v = pair.split('=', 1)
                params[k] = v
    return params

def valid_id(v):
    return MIN_ID <= v <= MAX_ID

def state_json():
    return json.dumps({"state": "RUNNING" if is_moving else "STOPPED",
                       "temp": motor_temp, "id": motor_id})

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
                params = parse_params(path)
                dist = float(params.get('dist', '1.0'))
                speed = int(params.get('speed', '50'))
                direction = int(params.get('dir', '1'))
                target = int(params.get('id', str(motor_id)))

                if not valid_id(target):
                    raise ValueError("ID must be %d-%d" % (MIN_ID, MAX_ID))
                if speed < 1:
                    raise ValueError("speed must be >= 1")

                select_motor(target)

                circumference = 3.14159265 * 0.1007
                revolutions = dist / circumference
                duration_sec = (revolutions * 60.0) / speed

                move_duration_ms = int(duration_sec * 1000)
                current_speed = speed * direction
                move_start_ticks = time.ticks_ms()
                is_moving = True

                response = state_json()
            except Exception as e:
                response = json.dumps({"state": "ERROR", "error": str(e)})

        elif path == '/stop':
            is_moving = False
            current_speed = 0
            send_rs485(cmd_stop(motor_id))
            response = state_json()

        elif path.startswith('/setid'):
            try:
                new_id = int(parse_params(path).get('id', '0'))
                if not valid_id(new_id):
                    raise ValueError("ID must be %d-%d" % (MIN_ID, MAX_ID))
                set_motor_id(new_id)
                response = json.dumps({"id": new_id})
            except Exception as e:
                response = json.dumps({"error": str(e)})

        elif path == '/queryid':
            response = json.dumps({"id": query_motor_id()})

        elif path == '/state':
            response = state_json()

        else:
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