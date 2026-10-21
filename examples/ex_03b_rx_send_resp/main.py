"""
examples/ex_03b_rx_send_resp/main.py

MicroPython port of the upstream ex_03b_rx_send_resp example (rx_send_resp.c).

Receives frames (polled). When it sees the blink frame sent by
ex_03a_tx_wait_resp, it replies with a data frame addressed to the blink's
sender. This is the companion to ex_03a (and, later, ex_03d).
"""

import utime as time

from platform.port import port
from decadriver import deca_device as dwt
from decadriver import deca_regs as R
from decadriver.deca_types import DwtConfig
from config_options import txconfig_options
from shared_data.shared_defines import FRAME_LEN_MAX

APP_NAME = "RX SENDRESP (MicroPython) v0.1"

# Must match the transmitter (ex_03a) exactly.
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

# Response frame (per upstream), 21 bytes, following the same ISO/IEC
# 24730-62:2013 standard as the blink it answers:
#   bytes 0-1   frame control, 0x8C41: data frame, 16-bit source address,
#               64-bit destination address
#   byte 2      sequence number, incremented per frame
#   bytes 3-4   application ID, 0x609A (sent as 0x9A, 0x60)
#   bytes 5-12  64-bit destination address (copied from the blink's source)
#   bytes 13-14 16-bit source address, hard coded ("DW")
#   byte 15     function code 0x10: activity control message
#   byte 16     activity code 0x00: activity finished
#   bytes 17-18 new tag blink rate
#   bytes 19-20 frame check sequence slot, filled in by the DW IC
tx_msg = bytearray([0x41, 0x8C, 0, 0x9A, 0x60, 0, 0, 0, 0, 0, 0, 0, 0,
                    ord('D'), ord('W'), 0x10, 0x00, 0, 0, 0, 0])
DATA_FRAME_SN_IDX = 2
DATA_FRAME_DEST_IDX = 5

BLINK_FRAME_SRC_IDX = 2     # source address (device ID) within the blink frame

_RX_ERRORS = (
    (R.SYS_STATUS_RXPHE_BIT_MASK, "RXPHE"),
    (R.SYS_STATUS_RXFCE_BIT_MASK, "RXFCE"),
    (R.SYS_STATUS_RXFSL_BIT_MASK, "RXFSL"),
    (R.SYS_STATUS_RXSTO_BIT_MASK, "RXSTO"),
    (R.SYS_STATUS_ARFE_BIT_MASK, "ARFE"),
    (R.SYS_STATUS_CIAERR_BIT_MASK, "CIAERR"),
)


def hexdump(data):
    return " ".join("%02x" % b for b in data)


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

    dwt.dwt_setleds(R.DWT_LEDS_ENABLE | R.DWT_LEDS_INIT_BLINK)

    if dwt.dwt_configure(config):
        print("CONFIG FAILED")
        while True:
            pass

    dwt.dwt_configuretxrf(txconfig_options)

    print("Ready to receive")

    try:
        while True:
            dwt.dwt_rxenable(R.DWT_START_RX_IMMEDIATE)

            # Poll until a good frame arrives or an error occurs.
            events = R.SYS_STATUS_RXFCG_BIT_MASK | R.SYS_STATUS_ALL_RX_ERR
            while True:
                status_reg = dwt.dwt_read32bitreg(R.SYS_STATUS_ID)
                if status_reg & events:
                    break

            t_rx = time.ticks_us()  # blink detected; host turnaround is timed from here

            if status_reg & R.SYS_STATUS_RXFCG_BIT_MASK:
                frame_len = dwt.dwt_read32bitreg(R.RX_FINFO_ID) & R.RX_FINFO_RXFLEN_BIT_MASK
                rx_buffer = b""
                if frame_len <= FRAME_LEN_MAX:
                    rx_buffer = dwt.dwt_readrxdata(frame_len, 0)

                dwt.dwt_write32bitreg(R.SYS_STATUS_ID, R.SYS_STATUS_RXFCG_BIT_MASK)

                # Is this the blink frame sent by ex_03a_tx_wait_resp?
                if (frame_len == 14 and rx_buffer[0] == 0xC5
                        and rx_buffer[10] == 0x43 and rx_buffer[11] == 0x02):
                    # Address the response to the blink's source.
                    for i in range(8):
                        tx_msg[DATA_FRAME_DEST_IDX + i] = rx_buffer[BLINK_FRAME_SRC_IDX + i]

                    dwt.dwt_writetxdata(len(tx_msg), bytes(tx_msg), 0)
                    dwt.dwt_writetxfctrl(len(tx_msg), 0, 0)  # no ranging
                    dwt.dwt_starttx(R.DWT_START_TX_IMMEDIATE)
                    t_tx = time.ticks_us()

                    # Poll until the TX-frame-sent event is set, then clear it.
                    while not (dwt.dwt_read32bitreg(R.SYS_STATUS_ID) & R.SYS_STATUS_TXFRS_BIT_MASK):
                        pass
                    dwt.dwt_write32bitreg(R.SYS_STATUS_ID, R.SYS_STATUS_TXFRS_BIT_MASK)

                    # MicroPython adaptation: the sender is waiting for this
                    # reply with its receiver on, so all console output (slow:
                    # USB serial, string formatting) happens only AFTER the
                    # response is on the air. Upstream prints before replying,
                    # which is fine in C but costs milliseconds here.
                    print("msg len %d: %s" % (frame_len, hexdump(rx_buffer)))
                    print("resp len %d: %s" % (len(tx_msg), hexdump(tx_msg)))
                    print("host turnaround: %d us (blink detected -> reply started)"
                          % time.ticks_diff(t_tx, t_rx))

                    tx_msg[DATA_FRAME_SN_IDX] = (tx_msg[DATA_FRAME_SN_IDX] + 1) & 0xFF
                else:
                    print("msg len %d: %s" % (frame_len, hexdump(rx_buffer)))
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
