"""
examples/ex_04b_cont_frame/main.py

MicroPython port of upstream ex_04b_cont_frame (continuous_frame.c).

Repeats an 802.15.4e blink frame back-to-back-ish (period CONT_FRAME_PERIOD
units of ~4 ns, i.e. 249600 = ~1 ms) for CONT_FRAME_DURATION_MS, then
soft-resets the chip. Intended for spectrum-analyser / regulatory testing;
a second board running ex_02a_simple_rx may also see the frames.
"""

import utime as time

from platform.port import port
from decadriver import deca_device as dwt
from decadriver import deca_regs as R
from decadriver.deca_types import DwtConfig
from config_options import txconfig_options

APP_NAME = "CONT FRAME (MicroPython) v0.1"

# Start-to-start delay between frames, in halves of the 499.2 MHz fundamental
# frequency (~4 ns): 249600 -> about 1 ms.
CONT_FRAME_PERIOD = 249600
CONT_FRAME_DURATION_MS = 120000

config = DwtConfig(
    chan=5,
    txPreambLength=R.DWT_PLEN_128,
    rxPAC=R.DWT_PAC8,
    txCode=9,
    rxCode=9,
    sfdType=1,
    dataRate=R.DWT_BR_6M8,
    phrMode=R.DWT_PHRMODE_STD,
    phrRate=R.DWT_PHRRATE_STD,
    sfdTO=(129 + 8 - 8),
    stsMode=R.DWT_STS_MODE_OFF,
    stsLength=R.DWT_STS_LEN_64,
    pdoaMode=R.DWT_PDOA_M0,
)

tx_msg = bytes([0xC5, 0, ord('D'), ord('E'), ord('C'), ord('A'),
                ord('W'), ord('A'), ord('V'), ord('E'), 0, 0])


def main():
    print(APP_NAME)
    port.spi_set_rate_high()
    port.reset_dwic()
    time.sleep_ms(2)
    while not dwt.dwt_checkidlerc():
        pass
    if dwt.dwt_initialise(R.DWT_DW_INIT) == R.DWT_ERROR:
        print("INIT FAILED")
        while True:
            pass
    if dwt.dwt_configure(config):
        print("CONFIG FAILED")
        while True:
            pass
    dwt.dwt_configuretxrf(txconfig_options)

    print("Continuous frames for %d ms" % CONT_FRAME_DURATION_MS)
    dwt.dwt_configcontinuousframemode(CONT_FRAME_PERIOD, config.chan)
    # Once configured, continuous frame mode starts like a normal transmission.
    dwt.dwt_writetxdata(len(tx_msg), tx_msg, 0)
    dwt.dwt_writetxfctrl(len(tx_msg), 0, 0)
    dwt.dwt_starttx(R.DWT_START_TX_IMMEDIATE)
    try:
        t0 = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), t0) < CONT_FRAME_DURATION_MS:
            time.sleep_ms(100)
    except KeyboardInterrupt:
        print("\nStopped by user (Ctrl-C).")
    dwt.dwt_softreset()
    port.spi_set_rate_low()
    print("done (chip reset; re-run initialise/configure for normal use)")


if __name__ == "__main__":
    main()
