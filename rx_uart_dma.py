import _thread
from rp2 import PIO, StateMachine, asm_pio, DMA
from machine import Pin, UART
from array import array
from time import sleep

# 1. Define the PIO program for UART RX
@asm_pio(
    autopush=True,
    push_thresh=8,
    in_shiftdir=PIO.SHIFT_RIGHT,
    fifo_join=PIO.JOIN_RX,
)
def uart_rx_mini():
    # fmt: off
    # Wait for start bit
    wait(0, pin, 0)
    # Preload bit counter, delay until eye of first data bit
    set(x, 7)                 [10]
    # Loop 8 times
    label("bitloop")
    # Sample data
    in_(pins, 1)
    # Each iteration is 8 cycles
    jmp(x_dec, "bitloop")     [6]
    # fmt: on

@asm_pio(
        in_shiftdir=PIO.SHIFT_RIGHT,
        fifo_join=PIO.JOIN_RX,
        push_thresh=8,
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

def handler(sm):
    print("break")

# 2. Setup Parameters
UART_BAUD = 9600
RX_PIN = Pin(12, Pin.IN, Pin.PULL_UP)
data_buffer = bytearray.fromhex('001600020064001D001C00130000000000000020008E000000000000002F101F10522C2E')
word_buffer = array('L',range(36))

uart = UART(1, UART_BAUD, rx=Pin(5, Pin.IN, Pin.PULL_UP), tx=Pin(4, Pin.OUT))

# 3. Initialize State Machine
sm = StateMachine(0,uart_rx, freq=8 * UART_BAUD, in_base=RX_PIN, jmp_pin=RX_PIN)
sm.irq(handler)
sm.active(1)
sm.restart()

buf = b'\x3A\x13\x01\x16\x79' # unlock code for es200g batteries


# 4. Configure DMA
# We need to find the DREQ (Data Request) ID for the state machine's TX FIFO
# For PIO0, SM0, DREQ is typically 0.
dma = DMA()
ctrl = dma.pack_ctrl(
    treq_sel=4,       # DREQ for PIO0 SM0 (Consult RP2040 datasheet for others)
    inc_read=False,    # Step through our data buffer
    inc_write=True,  # Always write to the same PIO TX FIFO register
    size=2            # 0 = Byte (8-bit) transfers
)

# 5. Trigger Transfer
dma.config(
    read=sm,
    write=word_buffer,         # MicroPython handles the FIFO address internally
    count=len(data_buffer),
    ctrl=ctrl,
    trigger=True,
)

uart.write(buf)

while dma.count:
    pass

for n,w in enumerate(word_buffer):
    data_buffer[n] = w >> 24

print(f"DMA Active: {dma.active()}")

print(f"{bytes(data_buffer).hex()}")
