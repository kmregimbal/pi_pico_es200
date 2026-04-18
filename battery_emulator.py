from machine import Pin, UART

uart0 = UART(1, baudrate=9600, tx=Pin(4, Pin.OUT), rx=Pin(5, Pin.IN, Pin.PULL_UP))

def send_data():
    data = bytearray.fromhex('3A1620020064641E1E1E1F1901000F0000000020001CA30000270400005C104010522C0A')
    uart0.write(data)

def main():
    rxData = bytes()
    while True:
        
        while uart0.any() > 0:
            rxData += uart0.read(1)
        if len(rxData) > 4:
            # print(f"{rxData.hex()}")
            if rxData == b'\x3A\x13\x01\x16\x79':
                # print("Valid")
                send_data()
                rxData = bytes()
            else:
                # probably crufty data
                while uart0.any() > 0:
                    single_byte = uart0.read(1)
                    if single_byte == b'\x3A':
                        rxData = bytes()
                        rxData += b'\x3A'

if __name__ == '__main__':
  main()