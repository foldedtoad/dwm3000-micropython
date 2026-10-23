"""
examples/ex_02d_rx_sniff/main.py

MicroPython port of the upstream ex_02d_rx_sniff example, transliterated from
rx_sniff.c. Same polled receive loop as ex_02a, but with SNIFF mode enabled:
the receiver is duty-cycled on/off (low-power listening) instead of being on
continuously. Pair with ex_01a_simple_tx on a second board.

Notes on the port
  * Sniff timing (SNIFF_ON_TIME = 2, SNIFF_OFF_TIME = 16) gives roughly a
    50% duty cycle with ~16 us on / ~16 us off phases at PAC8.
  * Upstream calls dwt_setlnapamode(LNA | PA) "for testing" so the RX on/off
    pattern can be seen on a scope (the DW3000 drives GPIO4/5/6 with it). It
    is kept for fidelity; it only matters if you probe those module pins.
  * A sniffing receiver can miss the start of a preamble, so some frames may
    be dropped or arrive with errors compared with ex_02a.
"""


import utime as time

from platform.port import port
from decadriver import deca_device as dwt
from decadriver import deca_regs as R
from decadriver.deca_types import DwtConfig
from shared_data.shared_defines import FRAME_LEN_MAX

APP_NAME = "RX SNIFF (MicroPython) v0.1"

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

# SNIFF mode on/off times. ON is in multiples of PAC size (the IC adds one PAC),
# so 2 at DWT_PAC8 is ~16 us. OFF is in multiples of 128/125 us (~1 us).
SNIFF_ON_TIME = 2
SNIFF_OFF_TIME = 16

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

    # For testing, so the receiver ON/OFF pattern can be seen on a scope.
    dwt.dwt_setlnapamode(R.DWT_LNA_ENABLE | R.DWT_PA_ENABLE)

    # If dwt_configure returns DWT_ERROR either the PLL or RX calibration
    # failed; the host should reset the device.
    if dwt.dwt_configure(config):
        print("CONFIG FAILED")
        while True:
            pass

    # Configure SNIFF mode.
    dwt.dwt_setsniffmode(1, SNIFF_ON_TIME, SNIFF_OFF_TIME)

    print("Ready to Receive (SNIFF mode)")

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
                    # (upstream 02d reads the whole frame, FCS included)
                    rx_buffer = dwt.dwt_readrxdata(frame_len, 0)

                # Clear the good-RX-frame event.
                dwt.dwt_write32bitreg(R.SYS_STATUS_ID, R.SYS_STATUS_RXFCG_BIT_MASK)

                print("len %d:" % frame_len, rx_buffer)
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
