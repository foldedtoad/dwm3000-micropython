"""
examples/ex_02f_rx_with_crystal_trim/main.py

MicroPython port of the upstream ex_02f_rx_with_crystal_trim example,
transliterated from rx_with_xtal_trim.c. A receiver that, after each good frame,
measures the transmitter's clock offset and trims its OWN crystal so that the
offset settles between TARGET_XTAL_OFFSET_VALUE_PPM_MIN and _MAX (2..4 ppm).
Pair with ex_01a_simple_tx on a second board. Only trim one end of a link.

Differences from upstream: it prints the offset and trim value for each frame
(upstream is meant to be watched at a debugger breakpoint).
"""

import utime as time

from platform.port import port
from decadriver import deca_device as dwt
from decadriver import deca_regs as R
from decadriver.deca_types import DwtConfig
from shared_data.shared_defines import FRAME_LEN_MAX

APP_NAME = "RX TRIM (MicroPython) v0.1"

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

# The receiver's crystal is trimmed so the offset from the transmitter's has a
# magnitude between these. Both must be positive, with (max - min) larger than
# the trimming resolution (~1.5 ppm); upstream recommends max - min >= 2.
TARGET_XTAL_OFFSET_VALUE_PPM_MIN = 2.0
TARGET_XTAL_OFFSET_VALUE_PPM_MAX = 4.0

FS_XTALT_MAX_VAL = R.XTAL_TRIM_BIT_MASK

# Typical trimming range (4.7 pF external caps) is ~77 ppm over all steps.
AVG_TRIM_PER_PPM = (FS_XTALT_MAX_VAL + 1) / 77.0


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

    dwt.dwt_setleds(R.DWT_LEDS_ENABLE | R.DWT_LEDS_INIT_BLINK)

    if dwt.dwt_configure(config):
        print("CONFIG FAILED")
        while True:
            pass

    # Read the initial trim value. Must be done after dwt_initialise(), which
    # sets up the initial trimming code.
    current_trim = dwt.dwt_getxtaltrim()
    print("initial xtal trim:", current_trim)

    try:
        while True:
            dwt.dwt_rxenable(R.DWT_START_RX_IMMEDIATE)

            while True:
                status_reg = dwt.dwt_read32bitreg(R.SYS_STATUS_ID)
                if status_reg & (R.SYS_STATUS_RXFCG_BIT_MASK | R.SYS_STATUS_ALL_RX_ERR):
                    break

            if status_reg & R.SYS_STATUS_RXFCG_BIT_MASK:
                # Read the frame (a real application would check it comes from
                # the node whose crystal we want to follow).
                frame_len = dwt.dwt_read32bitreg(R.RX_FINFO_ID) & R.RX_FINFO_RXFLEN_BIT_MASK
                rx_buffer = b""
                if frame_len <= FRAME_LEN_MAX:
                    rx_buffer = dwt.dwt_readrxdata(frame_len, 0)

                # Carrier frequency offset of the remote transmitter, in ppm.
                # Positive = local RX clock running faster than the remote's.
                # Must be read before the receiver is re-enabled.
                xtal_offset_ppm = dwt.dwt_readclockoffset() * R.CLOCK_OFFSET_PPM_TO_RATIO * 1e6

                adjusted = False
                if (abs(xtal_offset_ppm) > TARGET_XTAL_OFFSET_VALUE_PPM_MAX or
                        abs(xtal_offset_ppm) < TARGET_XTAL_OFFSET_VALUE_PPM_MIN):
                    # C: int8 -= float  ->  the float result is truncated toward
                    # zero; then &= 0x3F (a 6-bit trim value, so wraps).
                    current_trim = int(current_trim -
                                       ((TARGET_XTAL_OFFSET_VALUE_PPM_MAX +
                                         TARGET_XTAL_OFFSET_VALUE_PPM_MIN) / 2 +
                                        xtal_offset_ppm) * AVG_TRIM_PER_PPM)
                    current_trim &= FS_XTALT_MAX_VAL
                    dwt.dwt_setxtaltrim(current_trim)
                    adjusted = True

                print("seq %d  offset %+.2f ppm  trim %d%s" %
                      (rx_buffer[1] if len(rx_buffer) > 1 else -1,
                       xtal_offset_ppm, current_trim, "  (adjusted)" if adjusted else ""))

                # Clear good RX frame event.
                dwt.dwt_write32bitreg(R.SYS_STATUS_ID, R.SYS_STATUS_RXFCG_BIT_MASK)
            else:
                # Clear RX error events.
                dwt.dwt_write32bitreg(R.SYS_STATUS_ID, R.SYS_STATUS_ALL_RX_ERR)
    except KeyboardInterrupt:
        dwt.dwt_forcetrxoff()
        print("\nStopped by user (Ctrl-C). Transceiver forced off.")


if __name__ == "__main__":
    main()
