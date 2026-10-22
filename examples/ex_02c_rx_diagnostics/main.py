"""
examples/ex_02c_rx_diagnostics/main.py

MicroPython port of the upstream ex_02c_rx_diagnostics example, transliterated
from rx_diagnostics.c. Receives frames in polled mode (pair with
ex_01a_simple_tx on a second board). After each good frame it reads:
  - the CIA diagnostics (first path index, F1/F2/F3, peak, xtal offset, ...)
  - the accumulator (CIR) samples around the first path
and after *every* event, good frame or RX error, it reads the event counters
(good/bad CRC, header errors, sync losses, ...).

Notes on the port
  * The upstream example is meant to be inspected at a debugger breakpoint;
    here each event is printed instead.
  * Upstream passes the first-path integer index straight to dwt_readaccdata
    as the accumulator offset, which the driver uses as a *byte* offset into
    the accumulator memory (the API doc calls it a "complex sample index",
    but the code does not multiply by the 6-byte sample size). That is kept
    as-is for fidelity, so the accumulator bytes are printed raw (the first,
    dummy, byte already removed) rather than decoded into complex samples,
    which would be misaligned.
"""

import utime as time

from platform.port import port
from decadriver import deca_device as dwt
from decadriver import deca_regs as R
from decadriver.deca_types import DwtConfig
from shared_data.shared_defines import FRAME_LEN_MAX

APP_NAME = "RX DIAG (MicroPython) v0.1"

# Default communication configuration -- non-STS DW mode.
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

# Accumulator bytes to read: 3 below + 3 above the first path, 6 bytes per
# complex sample, plus 1 dummy byte (upstream ACCUM_DATA_LEN).
ACCUM_DATA_LEN = 3 * 2 * (3 + 3) + 1


def _print_counters(c):
    print("  counters: CRCG=%d CRCB=%d PHE=%d RSL=%d ARFE=%d OVER=%d "
          "SFDTO=%d PTO=%d RTO=%d TXF=%d PREJ=%d" %
          (c.CRCG, c.CRCB, c.PHE, c.RSL, c.ARFE, c.OVER,
           c.SFDTO, c.PTO, c.RTO, c.TXF, c.PREJ))


def main():
    print(APP_NAME)

    port.spi_set_rate_high()
    port.reset_dwic()
    time.sleep_ms(2)  # let DW3000 transition INIT_RC -> IDLE_RC

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

    # Activate event counters (also clears them).
    dwt.dwt_configeventcounters(1)

    # Enable IC diagnostic calculation and logging.
    dwt.dwt_configciadiag(1)

    print("Diagnostics ready")

    try:
        while True:
            # Activate reception immediately.
            dwt.dwt_rxenable(R.DWT_START_RX_IMMEDIATE)

            # Poll until a frame is properly received or an error occurs.
            while True:
                status_reg = dwt.dwt_read32bitreg(R.SYS_STATUS_ID)
                if status_reg & (R.SYS_STATUS_RXFCG_BIT_MASK | R.SYS_STATUS_ALL_RX_ERR):
                    break

            if status_reg & R.SYS_STATUS_RXFCG_BIT_MASK:
                # Clear good RX frame event.
                dwt.dwt_write32bitreg(R.SYS_STATUS_ID, R.SYS_STATUS_RXFCG_BIT_MASK)

                # Copy the frame (upstream reads frame_len bytes, FCS included).
                frame_len = dwt.dwt_read32bitreg(R.RX_FINFO_ID) & R.RX_FINFO_RXFLEN_BIT_MASK
                rx_buffer = b""
                if frame_len <= FRAME_LEN_MAX:
                    rx_buffer = dwt.dwt_readrxdata(frame_len, 0)

                # Read diagnostics data.
                rx_diag = dwt.dwt_readdiagnostics()

                # Read accumulator around the first path. fp index is 10.6
                # fixed point; >> 6 gives the integer part. Upstream computes
                # (fp_int - 2) in unsigned arithmetic; clamp so a very early
                # first path doesn't wrap to a huge offset.
                fp_int = rx_diag.ipatovFpIndex >> 6
                acc = dwt.dwt_readaccdata(ACCUM_DATA_LEN, max(fp_int - 2, 0))

                print("good frame, len %d: %s" % (frame_len, rx_buffer))
                print("  first path idx %d.%02d (raw 0x%04X)  accum count %d  xtal offset %d" %
                      (fp_int, ((rx_diag.ipatovFpIndex & 0x3F) * 100) >> 6,
                       rx_diag.ipatovFpIndex, rx_diag.ipatovAccumCount, rx_diag.xtalOffset))
                print("  ipatov peak 0x%08X power %d  F1 %d F2 %d F3 %d" %
                      (rx_diag.ipatovPeak, rx_diag.ipatovPower,
                       rx_diag.ipatovF1, rx_diag.ipatovF2, rx_diag.ipatovF3))
                print("  accumulator bytes (dummy dropped):", acc[1:])
            else:
                # Clear RX error events.
                dwt.dwt_write32bitreg(R.SYS_STATUS_ID, R.SYS_STATUS_ALL_RX_ERR)
                print("rx error, status 0x%08X" % (status_reg & R.SYS_STATUS_ALL_RX_ERR))

            # Read event counters after any event (counters are never reset here).
            _print_counters(dwt.dwt_readeventcounters())
    except KeyboardInterrupt:
        dwt.dwt_forcetrxoff()
        print("\nStopped by user (Ctrl-C). Transceiver forced off.")


if __name__ == "__main__":
    main()
