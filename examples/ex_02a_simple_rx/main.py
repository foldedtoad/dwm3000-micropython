"""
examples/ex_02a_simple_rx/main.py

MicroPython port of the upstream ex_02a_simple_rx example, transliterated
from simple_rx.c. Receives frames in polled mode and prints each good
frame's length and contents. Pair it with ex_01a_simple_tx running on a
second board (same channel/preamble/PRF configuration).
"""

import utime as time

from platform.port import port
from decadriver import deca_device as dwt
from decadriver import deca_regs as R
from decadriver.deca_types import DwtConfig
from shared_data.shared_defines import FRAME_LEN_MAX

APP_NAME = "SIMPLE RX (MicroPython) v0.1"

# Default communication configuration -- non-STS DW mode. Must match the
# transmitter's configuration (ex_01a_simple_tx uses the same values).
config = DwtConfig(
    chan=5,
    txPreambLength=R.DWT_PLEN_128,   # used in TX only
    rxPAC=R.DWT_PAC8,                # used in RX only
    txCode=9,                        # used in TX only
    rxCode=9,                        # used in RX only
    sfdType=1,                       # non-standard 8 symbol SFD
    dataRate=R.DWT_BR_6M8,
    phrMode=R.DWT_PHRMODE_STD,
    phrRate=R.DWT_PHRRATE_STD,
    sfdTO=(129 + 8 - 8),             # SFD timeout, used in RX only
    stsMode=R.DWT_STS_MODE_OFF,
    stsLength=R.DWT_STS_LEN_64,
    pdoaMode=R.DWT_PDOA_M0,
)

# (mask, name) pairs for the error events reported by simple_rx.c
_RX_ERRORS = (
    (R.SYS_STATUS_RXPHE_BIT_MASK, "RXPHE"),    # PHY header error
    (R.SYS_STATUS_RXFCE_BIT_MASK, "RXFCE"),    # received frame CRC error
    (R.SYS_STATUS_RXFSL_BIT_MASK, "RXFSL"),    # frame sync loss
    (R.SYS_STATUS_RXSTO_BIT_MASK, "RXSTO"),    # receive timeout
    (R.SYS_STATUS_ARFE_BIT_MASK, "ARFE"),      # receive frame error
    (R.SYS_STATUS_CIAERR_BIT_MASK, "CIAERR"),  # CIA error
)


def main():
    print(APP_NAME)

    # DW3000 supports up to 38 MHz SPI once initialised/configured.
    port.spi_set_rate_high()

    port.reset_dwic()

    time.sleep_ms(2)  # let DW3000 transition INIT_RC -> IDLE_RC

    while not dwt.dwt_checkidlerc():
        pass  # make sure the DW IC is in IDLE_RC before proceeding

    if dwt.dwt_initialise(R.DWT_DW_INIT) == R.DWT_ERROR:
        print("INIT FAILED")
        while True:
            pass

    # LEDs on for debug: each RX-enable flashes the RX LED on DW3000 boards.
    dwt.dwt_setleds(R.DWT_LEDS_ENABLE | R.DWT_LEDS_INIT_BLINK)

    # If dwt_configure returns DWT_ERROR either the PLL or RX calibration
    # failed; the host should reset the device.
    if dwt.dwt_configure(config):
        print("CONFIG FAILED")
        while True:
            pass

    print("Ready to Receive")

    try:
        while True:
            # Activate reception immediately.
            dwt.dwt_rxenable(R.DWT_START_RX_IMMEDIATE)

            # Poll until a frame is properly received or an error/timeout
            # occurs. The events of interest are all in the low 32 bits of
            # the 5-byte status register, so a 32-bit read is sufficient.
            while True:
                status_reg = dwt.dwt_read32bitreg(R.SYS_STATUS_ID)
                if status_reg & (R.SYS_STATUS_RXFCG_BIT_MASK | R.SYS_STATUS_ALL_RX_ERR):
                    break

            if status_reg & R.SYS_STATUS_ALL_RX_ERR:
                for mask, name in _RX_ERRORS:
                    if status_reg & mask:
                        print("receive error:", name)

            if status_reg & R.SYS_STATUS_RXFCG_BIT_MASK:
                # A good frame has arrived: read its length, then the payload.
                frame_len = dwt.dwt_read32bitreg(R.RX_FINFO_ID) & R.RX_FINFO_RXFLEN_BIT_MASK
                rx_buffer = b""
                if frame_len <= FRAME_LEN_MAX:
                    # No need to read the FCS/CRC.
                    rx_buffer = dwt.dwt_readrxdata(frame_len - R.FCS_LEN, 0)

                # Clear the good-RX-frame event.
                dwt.dwt_write32bitreg(R.SYS_STATUS_ID, R.SYS_STATUS_RXFCG_BIT_MASK)

                print("len %d:" % (frame_len - R.FCS_LEN), rx_buffer)
            else:
                # Clear RX error events.
                dwt.dwt_write32bitreg(R.SYS_STATUS_ID, R.SYS_STATUS_ALL_RX_ERR)
    except KeyboardInterrupt:
        # Leave the transceiver in a known-off state (the receiver is
        # normally running when Ctrl-C arrives).
        dwt.dwt_forcetrxoff()
        print("\nStopped by user (Ctrl-C). Transceiver forced off.")


if __name__ == "__main__":
    main()
