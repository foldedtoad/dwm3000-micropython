"""
examples/ex_07c_ack_data_rx_dbl_buff/main.py

MicroPython port of upstream ex_07c_ack_data_rx_dbl_buff
(ack_data_rx_dbl_buff.c). Same as ex_07b but the receiver uses the chip's two
RX buffers in manual mode: while the host is reading buffer A the chip can
already be receiving into buffer B. The loop alternates buffer A, buffer B:
receive, clear that buffer's status bits, read it, wait for the ACK to go out,
then hand the buffer back with dwt_signal_rx_buff_free(). Auto-ACK delay is
50 symbols here (upstream). Companion of ex_07a_ack_data_tx.

Note: only this simple alternating use is ported; the interrupt handler
(dwt_isr) does not support double-buffered RX.
"""

import utime as time

from platform.port import port
from decadriver import deca_device as dwt
from decadriver import deca_regs as R
from decadriver.deca_types import DwtConfig
from config_options import txconfig_options

APP_NAME = "ACK DATA RX DB (MicroPython) v0.1"

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

PAN_ID = 0xDECA
SHORT_ADDR = 0x5258  # "RX"
FCTRL_ACK_REQ_MASK = 0x20
FRAME_LEN_MAX = 127
FRAME_CTRL_FIRST_BYTE_IDX = 0


def read_received_data(buffer_b: bool):
    """Read the frame from RX buffer A (False) or B (True). Returns (frame_len, data)."""
    if not buffer_b:
        frame_len = dwt.dwt_read16bitoffsetreg(R.BUF0_RX_FINFO, 0)
    else:
        # Indirect pointer B was set up by dwt_setdblrxbuffmode to point at BUF1_RX_FINFO.
        frame_len = dwt.dwt_read16bitoffsetreg(R.INDIRECT_POINTER_B_ID, 0)
    frame_len &= R.EXT_FRAME_LEN
    data = dwt.dwt_readrxdata(frame_len, 0) if frame_len <= FRAME_LEN_MAX else b""
    return frame_len, data


def wait_for_ack_tx_end_if_needed(ack_flag: int) -> bool:
    if ack_flag & FCTRL_ACK_REQ_MASK:
        while not (dwt.dwt_read32bitreg(R.SYS_STATUS_ID) & R.SYS_STATUS_TXFRS_BIT_MASK):
            pass
        dwt.dwt_write32bitreg(R.SYS_STATUS_ID, R.SYS_STATUS_TXFRS_BIT_MASK)
        return True
    return False


def main():
    print(APP_NAME)
    port.spi_set_rate_high()
    port.reset_dwic()
    time.sleep_ms(2)
    while not dwt.dwt_checkidlerc():
        pass
    if dwt.dwt_initialise(R.DWT_DW_IDLE) == R.DWT_ERROR:
        print("INIT FAILED")
        while True:
            pass
    if dwt.dwt_configure(config):
        print("CONFIG FAILED")
        while True:
            pass
    dwt.dwt_configuretxrf(txconfig_options)

    dwt.dwt_setpanid(PAN_ID)
    dwt.dwt_setaddress16(SHORT_ADDR)
    dwt.dwt_configureframefilter(R.DWT_FF_ENABLE_802_15_4, R.DWT_FF_DATA_EN)
    dwt.dwt_enableautoack(50, 1)
    dwt.dwt_setlnapamode(R.DWT_LNA_ENABLE | R.DWT_PA_ENABLE)
    dwt.dwt_setleds(R.DWT_LEDS_ENABLE | R.DWT_LEDS_INIT_BLINK)

    # Double buffer, manual mode; minimal CIA diagnostics.
    dwt.dwt_setdblrxbuffmode(R.DBL_BUF_STATE_EN, R.DBL_BUF_MODE_MAN)
    dwt.dwt_configciadiag(R.DW_CIA_DIAG_LOG_MIN)

    count = 0
    try:
        while True:
            for buffer_b in (False, True):
                dwt.dwt_rxenable(R.DWT_START_RX_IMMEDIATE)
                good = R.RDB_STATUS_RXFCG1_BIT_MASK if buffer_b else R.RDB_STATUS_RXFCG0_BIT_MASK
                while not (dwt.dwt_read8bitoffsetreg(R.RDB_STATUS_ID, 0) & good):
                    pass
                dwt.dwt_write8bitoffsetreg(R.RDB_STATUS_ID, 0,
                                           R.RDB_STATUS_CLEAR_BUFF1_EVENTS if buffer_b
                                           else R.RDB_STATUS_CLEAR_BUFF0_EVENTS)
                frame_len, data = read_received_data(buffer_b)
                acked = wait_for_ack_tx_end_if_needed(data[FRAME_CTRL_FIRST_BYTE_IDX] if data else 0)
                dwt.dwt_signal_rx_buff_free()
                count += 1
                print("frame %d (buffer %s): len %d seq %s %s" %
                      (count, "B" if buffer_b else "A", frame_len, data[2] if len(data) > 2 else "?",
                       "-> ACK sent" if acked else "(no ACK requested)"))
    except KeyboardInterrupt:
        dwt.dwt_forcetrxoff()
        print("\nStopped by user (Ctrl-C). Transceiver forced off.")


if __name__ == "__main__":
    main()
