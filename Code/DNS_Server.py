# dns_server.py
import socket
import _thread
 
class CaptiveDNS:
    def __init__(self, ip="192.168.4.1"):
        self.ip = ip
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("", 53))
 
    def _loop(self):
        while True:
            try:
                data, addr = self.sock.recvfrom(1024)
                # Redirect all domain queries to 192.168.4.1
                response = data[:2] + b"\x81\x80\x00\x01\x00\x01\x00\x00\x00\x00" + data[12:]
                response += b"\xc0\x0c\x00\x01\x00\x01\x00\x00\x00\x3c\x00\x04"
                response += bytes([int(x) for x in self.ip.split(".")])
                self.sock.sendto(response, addr)
            except Exception:
                pass
 
    def start(self):
        _thread.start_new_thread(self._loop, ())
