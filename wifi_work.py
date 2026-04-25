import network
import socket
import binascii

from time import sleep
from ntptime import settime
from CONFIG import WIFI_SSID, WIFI_PASSWORD, INFLUX_HOST, INFLUX_TOKEN, INFLUX_ORG, INFLUX_BUCKET, SYSLOG_HOST, SYSLOG_PORT


wlan = network.WLAN(network.STA_IF)  # WiFi

ntp_time_synced = False
syslog_sock = None  # syslog via UDP


def connectWifi():
  global ntp_time_synced
  global syslog_sock

  # clean up any outstanding connections
  try:
    syslog_sock.close()
  except:
    pass
  try:
    wlan.disconnect()
    wlan.active(False)
  except:
    pass

  wlan.active(True)
  wlan.ipconfig(gw4='192.168.2.1')
  wlan.ipconfig(addr4='192.168.2.71/24')
  network.ipconfig(dns='192.168.2.1')
  
  wlan.config(hostname='picow2')
  wlan.ipconfig(autoconf6=False)
  
  wlan.connect(WIFI_SSID,WIFI_PASSWORD)
  
  

  max_wait = 10
  print('Waiting to connect',end="")
  while max_wait > 0:
    if wlan.status() < 0 or wlan.status() >=3:
      break
    max_wait -= 1
    print('.',end="")
    sleep(1)
  print(f'wlan status: {wlan.status()}')
  
  
  #sleep(5)
  
  if wlan.status() != 3:
    print('Network Connection has failed')
    return False
  else:
    print('connected')
    status = wlan.ifconfig()
    print(f'ip = {status}')
    syslog_sock = socket.socket(socket.AF_INET, #internet
                                  socket.SOCK_DGRAM) # UDP
    try:
      settime()
      ntp_time_synced = True
      print(f"Time Synced")
    except Exception as e:
      print(f"Time Sync failed: {e}")
    
    return True

def main():
  if connectWifi():
    print("Connected to WiFi")
  else:
    print("Dang. Wifi no workie.")


if __name__ == '__main__':
  main()