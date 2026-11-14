"""
examples/ex_02g_simple_rx_sts_sdc/main.py

MicroPython port of the upstream ex_02g_simple_rx_sts_sdc example
(simple_rx_sts_sdc.c): receive frames that carry a STS (Scrambled Timestamp
Sequence) with SDC (Super Deterministic Code), and check after each one that the
STS was received well. Companion of ex_01g_simple_tx_sts_sdc: run that on the
other board.

The STS quality index (dwt_readstsquality) counts how many STS symbols matched
the expected sequence; it must reach 90% of the STS length (57 of 64 here) for
the STS-derived timestamp to be trusted. The STS status (dwt_readstsstatus) must
be zero. Both are printed, with the frame.

A receiver configured without STS (ex_02a) will not decode ex_01g's frames, and
this receiver will not decode ex_01a's: the SFD type and STS mode must match
the transmitter.
"""

import utime as time

from platform.port import port
from decadriver import deca_device as dwt
from decadriver import deca_regs as R
from decadriver.deca_types import DwtConfig
from shared_data.shared_defines import FRAME_LEN_MAX, FRAME_LEN_MAX_EX

APP_NAME = "RX 4Z STS (MicroPython) v0.1"

# Communication configuration -- must match the transmitter (ex_01g).
config = DwtConfig(
    chan=5,
    txPreambLength=R.DWT_PLEN_128,
    rxPAC=R.DWT_PAC8,
    txCode=9,
    rxCode=9,
    sfdType=3,                          # 4z 8 symbol SFD
    dataRate=R.DWT_BR_6M8,
    phrMode=R.DWT_PHRMODE_STD,
    phrRate=R.DWT_PHRRATE_STD,
    sfdTO=(129 + 8 - 8),
    stsMode=R.DWT_STS_MODE_1 | R.DWT_STS_MODE_SDC,
    stsLength=R.DWT_STS_LEN_64,
    pdoaMode=R.DWT_PDOA_M0,
)

FRAME_PAYLOAD_IDX = 9

# (mask, name) pairs for the error events
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

    port.spi_set_rate_high()
    port.reset_dwic()
    time.sleep_ms(2)  # INIT_RC -> IDLE_RC

    while not dwt.dwt_checkidlerc():
        pass

    if dwt.dwt_initialise(R.DWT_DW_INIT) == R.DWT_ERROR:
        print("INIT FAILED")
        while True:
            pass

    # LEDs on for debug: each RX-enable flashes the RX LED on DW3000 boards.
    dwt.dwt_setleds(R.DWT_LEDS_ENABLE | R.DWT_LEDS_INIT_BLINK)

    if dwt.dwt_configure(config):
        print("CONFIG FAILED")
        while True:
            pass

    print("Ready to Receive")

    good = 0
    bad = 0
    try:
        while True:
            dwt.dwt_rxenable(R.DWT_START_RX_IMMEDIATE)

            # Poll until a frame is properly received or an error occurs.
            while True:
                status_reg = dwt.dwt_read32bitreg(R.SYS_STATUS_ID)
                if status_reg & (R.SYS_STATUS_RXFCG_BIT_MASK | R.SYS_STATUS_ALL_RX_ERR):
                    break

            if status_reg & R.SYS_STATUS_RXFCG_BIT_MASK:
                # A good frame has arrived: read its length, then the payload.
                frame_len = dwt.dwt_read32bitreg(R.RX_FINFO_ID) & FRAME_LEN_MAX_EX
                rx_buffer = b""
                if frame_len <= FRAME_LEN_MAX:
                    # No need to read the FCS/CRC.
                    rx_buffer = dwt.dwt_readrxdata(frame_len - R.FCS_LEN, 0)

                # Check the STS was received and is good. With ex_01g sending STS
                # with the same SDC this should always be true.
                cpq_result, cpq = dwt.dwt_readstsquality()
                st_result, cp_status = dwt.dwt_readstsstatus(0)
                if cpq_result >= 0 and st_result == R.DWT_SUCCESS:
                    good += 1
                    verdict = "STS is GOOD"
                else:
                    bad += 1
                    verdict = "STS qual/status FAIL"

                # Clear the good-RX-frame event.
                dwt.dwt_write32bitreg(R.SYS_STATUS_ID, R.SYS_STATUS_RXFCG_BIT_MASK)

                print("len %d: %s | %s (quality %d, status 0x%03X; %d good, %d bad)" %
                      (frame_len - R.FCS_LEN, rx_buffer, verdict, cpq, cp_status, good, bad))
            else:
                for mask, name in _RX_ERRORS:
                    if status_reg & mask:
                        print("receive error:", name)
                dwt.dwt_write32bitreg(R.SYS_STATUS_ID, R.SYS_STATUS_ALL_RX_ERR)
    except KeyboardInterrupt:
        dwt.dwt_forcetrxoff()
        print("\nStopped by user (Ctrl-C). Transceiver forced off.")


if __name__ == "__main__":
    main()
