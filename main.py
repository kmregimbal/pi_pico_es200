import struct
import _thread
from machine import Pin, UART, reset
from rp2 import PIO, StateMachine, asm_pio, DMA
from time import sleep, time, localtime, mktime
from ntptime import settime
import os
import json
from sys import exit
import network
import urequests as requests
import socket

import CONFIG as config                             # type: ignore

wlan = network.WLAN(network.STA_IF)  # WiFi
network_ready = False

adcpin = 4
sensor = machine.ADC(adcpin)

ntp_time_synced = False
syslog_sock = socket.socket()  # syslog via UDP
syslog_ready = False
led = Pin("LED", Pin.OUT)
stop_pin = Pin(16, Pin.IN, Pin.PULL_UP)
debug_pin = Pin(5,Pin.IN, Pin.PULL_UP)
running = True # so we can exit if the button is pushed
poll_triggered = False

WIFI_POST_TRIES = 10
wifi_post_tries_left = WIFI_POST_TRIES
UNLOCK_CODE_WAIT = 4.9

# OTA
firmware_url = "https://github.com/kmregimbal/pi_pico_es200/"

# Serial Communications
UART_BAUD = 9600
HARD_UART_TX_PIN = Pin(0, Pin.OUT) # pin 6
HARD_UART_RX_PIN = Pin(1, Pin.IN, Pin.PULL_UP) # pin 7
battery_list = {
  # 'B01': Pin(8, Pin.IN, Pin.PULL_UP), # pin 11
  'B01': {'tp': 'uart'}, # pin 7
  'B02': {'pin': Pin(11, Pin.IN, Pin.PULL_UP), 'tp': 'sm'}, # pin 12
  'B03': {'pin': Pin(12, Pin.IN, Pin.PULL_UP), 'tp': 'sm'}, # pin 16
  'B04': {'pin': Pin(13, Pin.IN, Pin.PULL_UP), 'tp': 'sm'}, # pin 17
  'B05': {'pin': Pin(18, Pin.IN, Pin.PULL_UP), 'tp': 'sm'}, # pin 21
  'B06': {'pin': Pin(19, Pin.IN, Pin.PULL_UP), 'tp': 'sm'}, # pin 22
  'B07': {'pin': Pin(20, Pin.IN, Pin.PULL_UP), 'tp': 'sm'}, # pin 26
  'B08': {'pin': Pin(21, Pin.IN, Pin.PULL_UP), 'tp': 'sm'}, # pin 27
  }

# PIO program for UART
@asm_pio(
  in_shiftdir=PIO.SHIFT_RIGHT,
  fifo_join=PIO.JOIN_RX,
  push_thresh=32,
)
def uart_rx():
  # fmt: off
  label("start")                                        # type: ignore
  # Stall until start bit is asserted
  wait(0, pin, 0)                                       # type: ignore
  # Preload bit counter, then delay until halfway through
  # the first data bit (12 cycles incl wait, set).
  set(x, 7)                 [10]                        # type: ignore
  label("bitloop")                                      # type: ignore
  # Shift data bit into ISR
  in_(pins, 1)                                          # type: ignore
  # Loop 8 times, each loop iteration is 8 cycles
  jmp(x_dec, "bitloop")     [6]                         # type: ignore
  # Check stop bit (should be high)
  jmp(pin, "good_stop")                                 # type: ignore
  # Either a framing error or a break. Set a sticky flag
  # and wait for line to return to idle state.
  irq(block, 4)                                         # type: ignore
  wait(1, pin, 0)                                       # type: ignore
  # Don't push data if we didn't see good framing.
  jmp("start")                                          # type: ignore
  # No delay before returning to start; a little slack is
  # important in case the TX clock is slightly too fast.
  label("good_stop")                                    # type: ignore
  # when doing single byte DMA, it reads most (least?) significant byte only.  so shift by 3 bytes
  in_(null,24)                                          # type: ignore
  push(block)                                           # type: ignore
  # fmt: on

# The handler for a UART break detected by the PIO.
def handler(sm):
  print("break", time.ticks_ms(), end=" ")

class OTAUpdater:
  """ This class handles OTA updates. It connects to the Wi-Fi, checks for updates, downloads and installs them."""
  def __init__(self, repo_url, filename):
    self.filename = filename
    self.repo_url = repo_url
    if "www.github.com" in self.repo_url :
      self.repo_url = self.repo_url.replace("www.github","raw.githubusercontent")
    elif "github.com" in self.repo_url:
      self.repo_url = self.repo_url.replace("github","raw.githubusercontent")            
    self.version_url = self.repo_url + 'main/version.json'
    self.firmware_url = self.repo_url + 'main/' + filename

    # get the current version (stored in version.json)
    if 'version.json' in os.listdir():    
      with open('version.json') as f:
        self.current_version = int(json.load(f)['version'])
      logit(f"Current device firmware version is '{self.current_version}'")

    else:
      self.current_version = 0
      # save the current version
      with open('version.json', 'w') as f:
        json.dump({'version': self.current_version}, f)
      
  def fetch_latest_code(self)->bool:
    """ Fetch the latest code from the repo, returns False if not found."""
    
    # Fetch the latest code from the repo.
    response = requests.get(self.firmware_url)
    if response.status_code == 200:
      logit(f'Fetched latest firmware code, status: {response.status_code}')
      # Save the fetched code to memory
      self.latest_code = response.text
      return True
    
    elif response.status_code == 404:
      logit(f'Firmware not found - {self.firmware_url}.')
      return False
    return False

  def update_no_reset(self):
    """ Update the code without resetting the device."""

    # Save the fetched code and update the version file to latest version.
    with open('latest_code.py', 'w') as f:
      f.write(self.latest_code)
    
    # update the version in memory
    self.current_version = self.latest_version

    # save the current version
    with open('version.json', 'w') as f:
      json.dump({'version': self.current_version}, f)
    
    # free up some memory
    self.latest_code = None

  def update_and_reset(self):
    """ Update the code and reset the device."""

    logit(f"Updating device... (Renaming latest_code.py to {self.filename})")

    # Overwrite the old code.
    os.rename('latest_code.py', self.filename)  

    # Restart the device to run the new code.
    logit('Restarting device...')
    reset()  # Reset the device to run the new code.
      
  def check_for_updates(self):
    """ Check if updates are available."""
    
    response = requests.get(self.version_url)
    
    data = json.loads(response.text)
    
    logit(f"data is: {data}, url is: {self.version_url}")
    self.latest_version = int(data['version'])
    logit(f'latest version is: {self.latest_version}')
    
    # compare versions
    newer_version_available = True if self.current_version < self.latest_version else False
    
    return newer_version_available
  
  def download_and_install_update_if_available(self):
    """ Check for updates, download and install them."""
    if self.check_for_updates():
      if self.fetch_latest_code():
        self.update_no_reset() 
        self.update_and_reset() 
    else:
      logit('No new updates available.')


class RuipuBattery:
  """" This class handles interactions with the es200 batteries via UART or StateMachine """
  
  def __init__(self, sm=StateMachine(0), uart=UART(0), sm_num = 0, tp="", name=""): # type: ignore
    self.sm = sm
    self.sm_num = sm_num
    self.uart = uart
    self.tp = tp
    self.bytesRead = 0
    self.buf = bytearray(36)
    self.pack_name = name
    self.buf_set_for_debug = False
    self.ctrl = 0
    
    if self.tp == 'sm':

      self.dma = DMA()
      treq = self.sm_num + 4 # SM0-3 -> DREQ 4-7
      if self.sm_num > 3:
        treq = self.sm_num + 8 #SM4-7 -> DREQ 12-15

      self.ctrl = self.dma.pack_ctrl(
        treq_sel=treq,
        inc_read=False, # always read from sm FIFO
        inc_write=True, # increment write position in self.buf as you go
        size=0 # single byte
      )
      self.start_dma()

  # def unlock(self):
  #   self.reset() # clear any lingering data in input buffer
  #   buf = b'\x3A\x13\x01\x16\x79' # unlock code for es200g batteries
  #   self.sm.write(buf)
  
  def start_dma(self):
    """ Trigger start of DMA transfer for _this_ state machine """

    self.dma.config(
      read=self.sm,
      write=self.buf,
      count=len(self.buf),
      ctrl=self.ctrl,
      trigger=True
    )

  def reset(self):
    """ Reset the port by reading any pending bytes from the queue/FIFO """

    if self.tp == 'sm':
      self.dma.active(0)
      self.sm.restart()
      self.bytesRead = 0
      self.start_dma()

    elif self.tp == 'uart':
      while self.uart.any() > 0:
        self.uart.read(1)
      self.bytesRead = 0  

  def read(self):
    """ Reads bytes from queue/FIFO to build buffer. Checks parity. Returns true when full, valid buffer exists """
    
    if self.tp == 'sm':
      if self.dma.active():
        pass
      else:
        self.start_dma()  # set up DMA for next loop
        self.bytesRead = 36  

    elif self.tp == 'uart':
      while self.uart.any() > 0 and self.bytesRead < 36:
        b = self.uart.read(1)
        self.buf[self.bytesRead] = b[0] # type: ignore
        self.bytesRead += 1
    
    if self.bytesRead > 35:
      self.bytesRead = 0
      if (int(self.buf[35]) == int(self.crc(self.buf,35),0)):
        return True # if the CRC calc matches the last byte
      else:
        logit(f"({self.name()}) Bad CRC: {bytes(self.buf).hex()}")
        self.reset()
    return False

  def setbuf(self,inbuf): 
    """ Allow manual set of buffer data for library debugging """
    
    self.buf = inbuf
    self.buf_set_for_debug = True

  def readbuf(self):
    return self.buf

  """ These functions pull data from relevant bytes in the buffer """
  def maxTemp(self):
    maxTemp = 0
    for i in range(7,10):
      if (self.buf[i] > maxTemp):
        maxTemp = self.buf[i]
    return maxTemp

  def minTemp(self):
    minTemp = 255
    for i in range(7,10):
      if (self.buf[i] < minTemp):
        minTemp = self.buf[i]
    return minTemp

  def rawStatus(self):
    return self.buf[3]

  def isChargingBulk(self):
    return (self.rawStatus() >> 5) & 1

  def isCellUndervoltage(self):
    return (self.rawStatus() >> 3) & 1

  def isChargerOK(self):
    return (self.rawStatus() >> 3) & 1

  def isChargerDetected(self):
    return (self.rawStatus() >> 2) & 1

  def isChargeFETEnabled(self):
    return self.rawStatus() & 1

  def isDischargeFETEnabled(self):
    return (self.rawStatus() >> 1) & 1

  def soc(self):
    return self.buf[5]

  def maxCellTemp(self):
    return self.buf[7]

  def avgCellTemp(self):
    return self.buf[8]

  def dischargeFETTemp(self):
    return self.buf[9]

  def microcontrollerTemp(self):
    return self.buf[10]

  def chargeCycleCount(self):
    return (self.buf[12] << 8 | self.buf[11])

  def voltage(self):
    return (self.buf[22] << 8 | self.buf[21]) / 1000

  def current(self):
    v = bytearray(2) # so we can use unpack to evaluated signed 16-bit int
    v[0],v[1] = self.buf[26],self.buf[25]
    return struct.unpack('>h',v)[0] / 1000

  def high(self):
    return (self.buf[30] << 8 | self.buf[29]) / 1000

  def low(self):
    return (self.buf[32] << 8 | self.buf[31]) / 1000

  def chargerState(self):
    if self.buf[13] == 0x00:
      return "Discharging"
    if self.buf[13] == 0x19:
      return "Begin Charging"
    if self.buf[13] == 0x7C:
      return "Charging"
    return "INVALID"
  
  def name(self):
    return self.pack_name
  
  def crc(self, data, len):
    """ calculate CRC of data based on es200 battery methodology """
    
    crc = 0x00
    dataArray = bytearray(data)
    for x in range(len):
      extract = dataArray[x]

      for i in range(8):
        sum = (crc ^ extract) & 0x01
        crc >>= 1
        if (sum):
          crc ^= 0x8C
        extract >>= 1
    return hex(crc)
  
  def create_influx_string(self):
    """ create influxdb string.  this part not so reusable, but convenient for my application """
    
    if (self.read() or self.buf_set_for_debug == True) and any(self.buf): # any() test strips out false all zeros buf if input left floating
      power = self.voltage() * self.current()
      discharge_enabled = 0
      
      if self.isDischargeFETEnabled():
        discharge_enabled = 1
      influx_string = f"es200_battery_data,unit={self.name()} "
      influx_string += f"soc={self.soc()}i,"
      influx_string += f"cycles={self.chargeCycleCount()}i,"
      influx_string += f"volts={self.voltage():.3f},"
      influx_string += f"amps={self.current():.3f},"
      influx_string += f"power={power:.3f},"
      influx_string += f"high={self.high():.3f},"
      influx_string += f"low={self.low():.3f},"
      influx_string += f"discharge={discharge_enabled}i"
      # if ntp_time_synced == True:
      #   nano_time = time_ns() # influx expects nanoseconds since UNIX epoch
      #   influx_string += f" {nano_time}"
      influx_string += "\n"
      self.reset() # reset now that data has been read/used
      return influx_string
    else:
      return None
                 

def connectWifi():
  """ Connect to wifi network and set up syslog socket """
  global ntp_time_synced
  global syslog_sock
  global syslog_ready
  global network_ready

  # clean up any outstanding connections
  try:
    syslog_sock.close()
    syslog_ready = False
  except:
    pass
  try:
    wlan.disconnect()
    wlan.active(False)
    network_ready = False
  except:
    pass

  # set up interface and connect
  wlan.active(True)
  wlan.ipconfig(dhcp4=False)
  wlan.ipconfig(gw4=config.IP_GW)
  wlan.ipconfig(addr4=config.IP_CIDR)
  network.ipconfig(dns=config.DNS_SRV)
  wlan.connect(config.WIFI_SSID,config.WIFI_PASSWORD)
  
  max_wait = 10
  print('Waiting to connect',end="")
  while max_wait > 0:
    if wlan.status() < 0 or wlan.status() >=3:
      break
    max_wait -= 1
    print('.',end="")
    sleep(1)
  print(f'wlan status: {wlan.status()}')
  
  if wlan.status() != 3:
    print('Network Connection has failed')
    syslog_ready = False
    network_ready = False
    return False
  else:
    print('connected')
    status = wlan.ipconfig('addr4')
    print('ip = ',end='')
    print(status)
    syslog_sock = socket.socket(socket.AF_INET, #internet
                                  socket.SOCK_DGRAM) # UDP
    syslog_ready = True
    network_ready = True
    try:
      settime() # grab GMT from NTP server and set local time
      ntp_time_synced = True
      time_now = localtime()
      logit(f"Time Synced.  Time now is {time_now[3]:02}:{time_now[4]:02}:{time_now[5]:02} GMT")
    except Exception as e:
      logit(f"Time Sync failed: {e}")
      ntp_time_synced = False
    
    return True

def postToInflux(data):
  """ Post influx line format data to influxdb server """

  global network_ready
  # connect to wifi if needed
  if network_ready == False:
    print("ReConnecting to wifi...")
    connectWifi()

  url = f"http://{config.HOST}:8086/api/v2/write?org={config.INFLUX_ORG}&bucket={config.INFLUX_BUCKET}"
  headers = {
      "Authorization": f"Token {config.INFLUX_TOKEN}",
      "Content-Type": "text/plain; charset=utf-8",
      "Accept": "application/json"
  }
  try:
    response = requests.post(url, headers=headers, data=data, timeout=5)
    response_code = response.status_code
    response.close()
    if response_code != 204:  
      logit(f"Response code was: {response_code}")
      return False
    return True
  except Exception as e:
    logit(f"Influxdb post failed via exception {e}")
    return False

def core1_task(uart,battery_instance_list):
  """ This loop sends unlock code every UNLOCK_CODE_WAIT seconds """
  
  global running
  global poll_triggered
  while running:
    for battery in battery_instance_list:
     battery.reset()
    buf = b'\x3A\x13\x01\x16\x79' # unlock code for es200g batteries
    uart.write(buf)
    poll_triggered = True
    sleep(UNLOCK_CODE_WAIT)

def logit(message):
  """ Send log/debug message to stdout and syslog if available """
  
  global syslog_ready
  led.on()
  print(message)
  led.off()
  message = f"pi_pico_es200: {message}"
  if syslog_ready:
    try:
      syslog_sock.sendto(message.encode(), (config.HOST,config.SYSLOG_PORT))
    except Exception as e:
      print(f"Error during syslog: {e}")

def restart_pico():
  """ Restart pico.  Probably because of network issues. """

  logit("Restarting Pico due to wifi/influx posting issue")
  reset()  # Reset the device to run the new code.

def button_handler(pin):
  """ Handle IRQ for a button press and halt the program """

  global running
  logit("forced exit")
  running = False # signal 2nd thread to exit
  exit(0)

def main():
  # bad practice <sigh>
  global wifi_post_tries_left
  global running
  global poll_triggered
  global ntp_time_synced
  successful_posts = 0

  # set up the IRQ for the button
  stop_pin.irq(trigger=Pin.IRQ_RISING,handler=button_handler)
 
  # Set up the hard UART
  uart = UART(0, UART_BAUD, tx=HARD_UART_TX_PIN, rx=HARD_UART_RX_PIN)

  # set up the instances pointing to the hard and PIO UARTS
  battery_instance_list = []
  sm_num = 0 
  for battery in battery_list:
    if sm_num == 4:
      sm_num += 1 # skip SM0 on PIO1 since is used by wifi
    if battery_list[battery]['tp'] == 'uart':
      battery_instance_list.append(RuipuBattery(tp='uart', uart=uart, name=battery))
    elif battery_list[battery]['tp'] == 'sm':
      sm = StateMachine(
            sm_num,
            uart_rx,
            freq=8 * UART_BAUD,
            in_base=battery_list[battery]['pin'],  # For WAIT, IN
            jmp_pin=battery_list[battery]['pin'],  # For JMP
        )
      sm.irq(handler)
      sm.active(1)
      
      battery_instance_list.append(RuipuBattery(tp='sm', sm=sm, sm_num=sm_num, name=battery))
      sm_num += 1
  sorted_battery_instances = sorted(battery_instance_list, key=lambda x: x.name())

  # start outputing the unlock code right away
  # tell core 1 to reset each hard/soft UART then output the unlock key every UNLOCK_CODE_WAIT seconds
  _thread.start_new_thread(core1_task, (uart,battery_instance_list))
  
  # connect wifi
  if connectWifi():
    logit("Connected to WiFi")
    ota_updater = OTAUpdater(firmware_url, "main.py")
    ota_updater.download_and_install_update_if_available()
  
  influx_strings = [''] * len(battery_instance_list)

  last_minute = 0
  ttp = localtime(time() + 60) # first target time during next minute
  ttp = (ttp[0],ttp[1],ttp[2],ttp[3],ttp[4],30,ttp[6],ttp[7]) # set to half minute
  target_time = mktime(ttp)

  resync_time_target = time() + 60*60#*24 # re-sync to NTP server daily
  
  while True:
    
    while poll_triggered == False:
      # while we're waiting, might as well post to influx when its time
      minute = localtime()[4]
      if minute != last_minute:
        target_time = time() + 30
        last_minute = minute
        if time() > resync_time_target:
          resync_time_target = time() + 60*60#*24 # re-sync to NTP server daily
          try:
            settime() # grab GMT from NTP server and set local time
            ntp_time_synced = True
            time_now = localtime()
            logit(f"Time Synced.  Time now is {time_now[3]:02}:{time_now[4]:02}:{time_now[5]:02} GMT")
          except Exception as e:
            logit(f"Time Sync failed: {e}")
            ntp_time_synced = False

      if time() > target_time:
        target_time = time() + 60
        
        influx_string = ""
        for work_string in influx_strings:
          if work_string is not None:
            influx_string += work_string
        
        influx_strings = [''] * len(battery_instance_list)
        
        if debug_pin.value() == 0: # active when pulled low
          logit(f'Posting data\n{influx_string}')
          adc_value = sensor.read_u16()
          volt = (3.3/65525) * adc_value
          temperature = 27 - (volt - 0.706)/0.001721
          round_temp = round(temperature,1)
          logit(f'Temperature: {round_temp} C')
        
        time_parts = localtime()
        time_string = f"{time_parts[3]:02}:{time_parts[4]:02}:{time_parts[5]:02} GMT"
        try:
          if postToInflux(influx_string) == True:
            successful_posts = successful_posts + 1
            logit(f"Post #{successful_posts} to influxdb Successfull at {time_string}")
            wifi_post_tries_left = WIFI_POST_TRIES
          else:
            wifi_post_tries_left -= 1
            logit(f"Posting data failed at {time_string}. {wifi_post_tries_left} tries left.")
        except Exception as e:
          wifi_post_tries_left -= 1
          logit(f"Posting data Failed via exception [{e}] at {time_string}. {wifi_post_tries_left} tries left.")
      if wifi_post_tries_left < 1:
              restart_pico()
      sleep(0.1)
    # end of while loop

    poll_triggered = False # set for next loop
    sleep(0.1) # 36 bytes at 9600 baud take ~.0375.  Wait about 2x that to allow DMAs to finish.

    log_string = ""
    for n, battery in enumerate(sorted_battery_instances):

      influx_string = battery.create_influx_string()
      if influx_string is not None:
        influx_strings[n] = influx_string
        log_string += f"({battery.name()}) "
    
    
      
    if len(log_string) > 0 and debug_pin.value() == 0: # debug pin is active when pulled low
      log_string = f"[{log_string.count('B')}] " + log_string
      logit(log_string)
  # end of while loop

if __name__ == '__main__':
  main()
  
