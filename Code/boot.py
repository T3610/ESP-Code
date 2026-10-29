import network
import gc
print("HELLO") 
gc.collect()
    
ap = network.WLAN(network.AP_IF)
ap.active(True)
ap.config(essid="DDSM115-Motor-Control")
    
print("Access Point Ready. SSID: DDSM115-Motor-Control")
print("IP Address:", ap.ifconfig()[0])


