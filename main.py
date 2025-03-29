import struct
import _thread
from machine import Pin, UART
from rp2 import PIO, StateMachine, asm_pio
from time import sleep, time, localtime
import network
import urequests as requests
import os
import json
import socket
from WIFI_CONFIG import WIFI_SSID, WIFI_PASSWORD
from INFLUX_CONFIG import INFLUX_USERNAME, INFLUX_TOKEN, INFLUX_ORG, INFLUX_BUCKET
from SYSLOG_CONFIG import SYSLOG_HOST, SYSLOG_PORT

# WiFi
wlan = network.WLAN(network.STA_IF)

# OTA
firmware_url = "https://github.com/kmregimbal/pi_pico_es200/"




# Serial Communications
UART_BAUD = 9600
HARD_UART_TX_PIN = Pin(4, Pin.OUT) # pin 6
HARD_UART_RX_PIN = Pin(5, Pin.IN, Pin.PULL_UP) # pin 7
RUN_PIN = Pin(3, Pin.IN, Pin.PULL_UP)
battery_list = {
    # 'B01': Pin(8, Pin.IN, Pin.PULL_UP), # pin 11
    'B01': {'tp': 'uart'}, # pin 7
    'B02': {'pin': Pin(9, Pin.IN, Pin.PULL_UP), 'tp': 'sm'}, # pin 12
    'B03': {'pin': Pin(12, Pin.IN, Pin.PULL_UP), 'tp': 'sm'}, # pin 16
    'B04': {'pin': Pin(13, Pin.IN, Pin.PULL_UP), 'tp': 'sm'}, # pin 17
    'B05': {'pin': Pin(16, Pin.IN, Pin.PULL_UP), 'tp': 'sm'}, # pin 21
    'B06': {'pin': Pin(17, Pin.IN, Pin.PULL_UP), 'tp': 'sm'}, # pin 22
    'B07': {'pin': Pin(20, Pin.IN, Pin.PULL_UP), 'tp': 'sm'}, # pin 26
    'B08': {'pin': Pin(21, Pin.IN, Pin.PULL_UP), 'tp': 'sm'}, # pin 27
  }

# syslog via UDP
syslog_sock = None

# PIO program for UART
@asm_pio(
    in_shiftdir=PIO.SHIFT_RIGHT,
    fifo_join=PIO.JOIN_RX,
)
def uart_rx():
    # fmt: off
    label("start")
    # Stall until start bit is asserted
    wait(0, pin, 0)
    # Preload bit counter, then delay until halfway through
    # the first data bit (12 cycles incl wait, set).
    set(x, 7)                 [10]
    label("bitloop")
    # Shift data bit into ISR
    in_(pins, 1)
    # Loop 8 times, each loop iteration is 8 cycles
    jmp(x_dec, "bitloop")     [6]
    # Check stop bit (should be high)
    jmp(pin, "good_stop")
    # Either a framing error or a break. Set a sticky flag
    # and wait for line to return to idle state.
    irq(block, 4)
    wait(1, pin, 0)
    # Don't push data if we didn't see good framing.
    jmp("start")
    # No delay before returning to start; a little slack is
    # important in case the TX clock is slightly too fast.
    label("good_stop")
    push(block)
    # fmt: on

# The handler for a UART break detected by the PIO.
def handler(sm):
    print("break", time.ticks_ms(), end=" ")

class OTAUpdater:
    """ This class handles OTA updates. It connects to the Wi-Fi, checks for updates, downloads and installs them."""
    # def __init__(self, ssid, password, repo_url, filename):
    def __init__(self, repo_url, filename):
        self.filename = filename
        # self.ssid = ssid
        # self.password = password
        self.repo_url = repo_url
        if "www.github.com" in self.repo_url :
            logit(f"Updating {repo_url} to raw.githubusercontent")
            self.repo_url = self.repo_url.replace("www.github","raw.githubusercontent")
        elif "github.com" in self.repo_url:
            logit(f"Updating {repo_url} to raw.githubusercontent'")
            self.repo_url = self.repo_url.replace("github","raw.githubusercontent")            
        self.version_url = self.repo_url + 'main/version.json'
        logit(f"version url is: {self.version_url}")
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
            
    # def connect_wifi(self):
    #     """ Connect to Wi-Fi."""

    #     sta_if = network.WLAN(network.STA_IF)
    #     sta_if.active(True)
    #     sta_if.connect(self.ssid, self.password)
    #     while not sta_if.isconnected():
    #         logit('.', end="")
    #         sleep(0.25)
    #     logit(f'Connected to WiFi, IP is: {sta_if.ifconfig()[0]}')
        
    def fetch_latest_code(self)->bool:
        """ Fetch the latest code from the repo, returns False if not found."""
        
        # Fetch the latest code from the repo.
        response = requests.get(self.firmware_url)
        if response.status_code == 200:
            logit(f'Fetched latest firmware code, status: {response.status_code}')
            #logit(f'Fetched latest firmware code, status: {response.status_code}, -  {response.text}')
    
            # Save the fetched code to memory
            self.latest_code = response.text
            return True
        
        elif response.status_code == 404:
            logit(f'Firmware not found - {self.firmware_url}.')
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

        # Overwrite the old code.
#         os.rename('latest_code.py', self.filename)

    def update_and_reset(self):
        """ Update the code and reset the device."""

        logit(f"Updating device... (Renaming latest_code.py to {self.filename})")

        # Overwrite the old code.
        os.rename('latest_code.py', self.filename)  

        # Restart the device to run the new code.
        logit('Restarting device...')
        machine.reset()  # Reset the device to run the new code.
        
    def check_for_updates(self):
        """ Check if updates are available."""
        
        # Connect to Wi-Fi
        # self.connect_wifi()

        logit(f'Checking for latest version... on {self.version_url}')
        response = requests.get(self.version_url)
        
        data = json.loads(response.text)
        
        logit(f"data is: {data}, url is: {self.version_url}")
        logit(f"data version is: {data['version']}")
        # Turn list to dict using dictionary comprehension
#         my_dict = {data[i]: data[i + 1] for i in range(0, len(data), 2)}
        
        self.latest_version = int(data['version'])
        logit(f'latest version is: {self.latest_version}')
        
        # compare versions
        newer_version_available = True if self.current_version < self.latest_version else False
        
        logit(f'Newer version available: {newer_version_available}')    
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

  def __init__(self, sm=None, uart=None, tp="", name=""):
    self.sm = sm
    self.uart = uart
    self.tp = tp
    self.bytesRead = 0
    self.buf = bytearray(36)
    self.pack_name = name

  # def unlock(self):
  #   self.reset() # clear any lingering data in input buffer
  #   buf = b'\x3A\x13\x01\x16\x79' # unlock code for es200g batteries
  #   self.sm.write(buf)
  
  def reset(self):
    # print("Reseting...",end="")
    # sleep(0.1)
    if self.tp == 'sm':
      while (self.sm.rx_fifo() > 0):
        self.sm.get()
      self.bytesRead = 0
      self.sm.restart()
    elif self.tp == 'uart':
      while self.uart.any() > 0:
        self.uart.read(1)
      pass

    # print("Done.")

  def read(self):
    if self.tp == 'sm':
      while (self.sm.rx_fifo() > 0 and self.bytesRead < 36):
        word = self.sm.get() # get returns a 32-bit word.  need just 8 bits of that
        b = bytearray()
        b.append(word >> 24)
        self.buf[self.bytesRead] = b[0] # 1st element of bytes object (even though there is only one anyway)
        self.bytesRead += 1
    elif self.tp == 'uart':
      while self.uart.any() > 0 and self.bytesRead < 36:
        b = self.uart.read(1)
        # print(f'({self.bytesRead}) Char: {b.hex()}')
        self.buf[self.bytesRead] = b[0]
        self.bytesRead += 1
    
    if self.bytesRead > 35:
      self.bytesRead = 0
      if (int(self.buf[35]) == int(self.crc(self.buf,35),0)):
        return True # if the CRC calc matches the last byte
      else:
        logit(f"({self.name()}) Bad CRC: {bytes(self.buf).hex()}")
        # for z in range(36):
        #   print(f"{hex(self.buf[z])}",end="")
        # print("")
        self.reset()
    return False

  def setbuf(self,inbuf): # for library debugging
    self.buf = inbuf

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

def connectWifi():
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
  
  if wlan.status() != 3:
    print('Network Connection has failed')
    return False
  else:
    print('connected')
    status = wlan.ifconfig()
    print('ip = ' + status[0])
    syslog_sock = socket.socket(socket.AF_INET, #internet
                                  socket.SOCK_DGRAM) # UDP
    
    return True

def postToInflux(data):
  # connect to wifi if needed
  if wlan.isconnected():
    pass
  else:
    print("ReConnecting to wifi...")
    connectWifi()

  url = f"http://192.168.2.70:8086/api/v2/write?org={INFLUX_ORG}&bucket={INFLUX_BUCKET}"
  headers = {
      "Authorization": f"Token {INFLUX_TOKEN}",
      "Content-Type": "text/plain; charset=utf-8",
      "Accept": "application/json"
  }
  response = requests.post(url, headers=headers, data=data)
  response_code = response.status_code
  if response_code != 204:
    logit(f"Response code was: {response_code}")
  response.close()
  if response_code == 204:
    return True
  return False

def core1_task(uart,battery_instance_list):
  while RUN_PIN.value() == 0:
    for battery in battery_instance_list:
      battery.reset()
    buf = b'\x3A\x13\x01\x16\x79' # unlock code for es200g batteries
    uart.write(buf)
    # print(".",end="")
    sleep(4.9)

def logit(message):
  print(message)
  if syslog_sock is not None:
    syslog_sock.sendto(message.encode(), (SYSLOG_HOST,SYSLOG_PORT))


def main():
  # bad practice <sigh>
  global syslog_sock

  # check to make sure the RUN_PIN is low
  if RUN_PIN.value() == 1:
    print("RUN_PIN is high.  Bailing out!")
    sleep(2)
  else:   
    # Set up the hard UART
    uart = UART(1, UART_BAUD, tx=HARD_UART_TX_PIN, rx=HARD_UART_RX_PIN)
    battery_instance_list = []

    # set up the instances pointing to the hard and PIO UARTS
    sm_count = 0 # 1 of the state machines on PIO1 (sm 4-7) is used by wifi
    for battery in battery_list:
      if battery_list[battery]['tp'] == 'uart':
        battery_instance_list.append(RuipuBattery(tp='uart', uart=uart, name=battery))
      elif battery_list[battery]['tp'] == 'sm':
        sm = StateMachine(
              sm_count,
              uart_rx,
              freq=8 * UART_BAUD,
              in_base=battery_list[battery]['pin'],  # For WAIT, IN
              jmp_pin=battery_list[battery]['pin'],  # For JMP
          )
        sm.irq(handler)
        sm.active(1)
        battery_instance_list.append(RuipuBattery(tp='sm', sm=sm, name=battery))
        sm_count += 1

    # connect wifi
    if connectWifi():
      logit("Connected to WiFi")
      ota_updater = OTAUpdater(firmware_url, "main.py")
      ota_updater.download_and_install_update_if_available()
    # tell core 1 to reset each hard/soft UART then output the unlock key every 4.9 seconds
    _thread.start_new_thread(core1_task, (uart,battery_instance_list))

    last_influx_update_minute = [0] * len(battery_instance_list)

    while RUN_PIN.value() == 0: # bail unless the RUN_PIN is low
      influx_string = logstring = ""
      # logstring = ""
      for n, battery in enumerate(battery_instance_list):
        if battery.read():
          state_of_charge = battery.soc()
          cycle_count = battery.chargeCycleCount()
          voltage = battery.voltage()
          current = battery.current()
          power = voltage * current
          cell_volts_high = battery.high()
          cell_volts_low = battery.low()
          discharge_enabled = 0
          if battery.isDischargeFETEnabled():
            discharge_enabled = 1

          logstring += f"({battery.name()}) "
          # logstring += f"SOC:{state_of_charge},"
          # logstring += f"Cycles:{cycle_count},"
          # logstring += f"Volts:{voltage:.2f},"
          # logstring += f"Amps:{current:.2f},"
          # logstring += f"Power:{power:.2f},"
          # logstring += f"Low:{cell_volts_low:.2f},"
          # logstring += f"High:{cell_volts_high:.2f},"
          # logstring += f"Discharge_Enabled:{discharge_enabled}"
          # print(logstring,end="")
          
          minute = localtime()[4]
          if minute != last_influx_update_minute[n]: # post to influx once per minute
            name = battery.name()
            work_string= f'battery_data,unit={name} soc={state_of_charge}i,cycles={cycle_count}i,volts={voltage:.3f},amps={current:.3f},power={power:.3f},high={cell_volts_high:.3f},low={cell_volts_low:.3f},discharge={discharge_enabled}i\n'
            # print(f"{work_string}")
            influx_string += work_string
            last_influx_update_minute[n] = minute
      if len(logstring) > 0:
        # print(logstring)
        # if syslog_sock is not None:
        #   syslog_sock.sendto(logstring.encode(), (SYSLOG_HOST,SYSLOG_PORT))
        logit(logstring)
      if len(influx_string) > 0:
        
        try:
          # print(f'Posting data\n{influx_string}',end="")
          if postToInflux(influx_string) == True:
            logit("Posting data Success")
          else:
            logit("Posting data failed")
        except:
          logit("Posting data Failed via exception")

if __name__ == '__main__':
  main()