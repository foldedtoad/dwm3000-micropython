"""
examples/ex_06b_ss_twr_responder/main.py

MicroPython port of the upstream ex_06b_ss_twr_responder example
(ss_twr_responder.c): single-sided two-way ranging, responder side.

It waits for a poll from ex_06a_ss_twr_initiator, records the poll's RX
timestamp, and sends a response at a precise (hardware-timed) delay after the
poll, containing the poll-RX and response-TX timestamps. Start this one first.

Difference from upstream: POLL_RX_TO_RESP_TX_DLY_UUS is 20000 here instead of
650. In C the host can read the frame and queue the reply in well under 650 us;
MicroPython needs several ms. If the delay is too short the chip reports a
"half period warning" and the reply is abandoned: this example then prints a
message telling you to raise the delay (and the initiator's copy of the same
constant is derived from it, so change both files to match).
"""

import utime as time

from platform.port import port
from decadriver import deca_device as dwt
from decadriver import deca_regs as R
from decadriver.deca_types import DwtConfig
from config_options import txconfig_options
from shared_data.shared_defines import RXFLEN_MASK, UUS_TO_DWT_TIME
from shared_data.shared_functions import get_rx_timestamp_u64, resp_msg_set_ts

APP_NAME = "SS TWR RESP (MicroPython) v0.1"

# Default communication configuration -- must match the initiator (ex_06a).
config = DwtConfig(
    chan=5,
    txPreambLength=R.DWT_PLEN_128,
    rxPAC=R.DWT_PAC8,
    txCode=9,
    rxCode=9,
    sfdType=1,                 # non-standard 8 symbol SFD
    dataRate=R.DWT_BR_6M8,
    phrMode=R.DWT_PHRMODE_STD,
    phrRate=R.DWT_PHRRATE_STD,
    sfdTO=(129 + 8 - 8),
    stsMode=R.DWT_STS_MODE_OFF,
    stsLength=R.DWT_STS_LEN_64,
    pdoaMode=R.DWT_PDOA_M0,
)

# Default antenna delay values for 64 MHz PRF (calibrate per device in a real
# product).
TX_ANT_DLY = 16385
RX_ANT_DLY = 16385

# Frames used in the ranging process (see ex_06a for the layout).
rx_poll_msg = bytes([0x41, 0x88, 0, 0xCA, 0xDE, ord('W'), ord('A'), ord('V'), ord('E'), 0xE0, 0, 0])
tx_resp_msg = bytearray([0x41, 0x88, 0, 0xCA, 0xDE, ord('V'), ord('E'), ord('W'), ord('A'), 0xE1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0])
ALL_MSG_COMMON_LEN = 10
ALL_MSG_SN_IDX = 2
RESP_MSG_POLL_RX_TS_IDX = 10
RESP_MSG_RESP_TX_TS_IDX = 14
RX_BUF_LEN = 12

# Delay from poll RX to response TX, in UWB microseconds. MUST be the same
# value as POLL_RX_TO_RESP_TX_DLY_UUS in ex_06a. Upstream uses 650.
POLL_RX_TO_RESP_TX_DLY_UUS = 20000


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

    dwt.dwt_setrxantennadelay(RX_ANT_DLY)
    dwt.dwt_settxantennadelay(TX_ANT_DLY)

    dwt.dwt_setlnapamode(R.DWT_LNA_ENABLE | R.DWT_PA_ENABLE)

    print("Responder ready (response delay %d uus)" % POLL_RX_TO_RESP_TX_DLY_UUS)

    frame_seq_nb = 0
    try:
        while True:
            dwt.dwt_rxenable(R.DWT_START_RX_IMMEDIATE)

            while True:
                status_reg = dwt.dwt_read32bitreg(R.SYS_STATUS_ID)
                if status_reg & (R.SYS_STATUS_RXFCG_BIT_MASK | R.SYS_STATUS_ALL_RX_ERR):
                    break

            if status_reg & R.SYS_STATUS_RXFCG_BIT_MASK:
                t_rx = time.ticks_us()

                dwt.dwt_write32bitreg(R.SYS_STATUS_ID, R.SYS_STATUS_RXFCG_BIT_MASK)

                frame_len = dwt.dwt_read32bitreg(R.RX_FINFO_ID) & RXFLEN_MASK
                if frame_len <= RX_BUF_LEN:
                    rx_buffer = bytearray(dwt.dwt_readrxdata(frame_len, 0))

                    # Is it a poll from the ex_06a initiator? (Sequence number
                    # is not relevant: clear it before comparing.)
                    rx_buffer[ALL_MSG_SN_IDX] = 0
                    if bytes(rx_buffer[:ALL_MSG_COMMON_LEN]) == rx_poll_msg[:ALL_MSG_COMMON_LEN]:
                        # Poll reception timestamp (40-bit).
                        poll_rx_ts = get_rx_timestamp_u64()

                        # Response TX time: DX_TIME takes the high 32 bits of
                        # the 40-bit time, so shift by 8 and keep 32 bits (the
                        # device time wraps; the C code truncates the same way).
                        resp_tx_time = ((poll_rx_ts + POLL_RX_TO_RESP_TX_DLY_UUS * UUS_TO_DWT_TIME) >> 8) & 0xFFFFFFFF
                        dwt.dwt_setdelayedtrxtime(resp_tx_time)

                        # Response TX timestamp = programmed TX time (bit 0 is
                        # ignored by the chip) plus the TX antenna delay.
                        resp_tx_ts = ((resp_tx_time & 0xFFFFFFFE) << 8) + TX_ANT_DLY

                        # Write both timestamps into the response message.
                        resp_msg_set_ts(tx_resp_msg, RESP_MSG_POLL_RX_TS_IDX, poll_rx_ts)
                        resp_msg_set_ts(tx_resp_msg, RESP_MSG_RESP_TX_TS_IDX, resp_tx_ts)

                        # Write and send the response.
                        tx_resp_msg[ALL_MSG_SN_IDX] = frame_seq_nb
                        dwt.dwt_writetxdata(len(tx_resp_msg), tx_resp_msg, 0)
                        dwt.dwt_writetxfctrl(len(tx_resp_msg), 0, 1)    # zero offset, ranging
                        ret = dwt.dwt_starttx(R.DWT_START_TX_DELAYED)
                        t_queued = time.ticks_us()

                        # If dwt_starttx() fails (the delayed time had already
                        # passed), abandon this exchange.
                        if ret == R.DWT_SUCCESS:
                            while not (dwt.dwt_read32bitreg(R.SYS_STATUS_ID) & R.SYS_STATUS_TXFRS_BIT_MASK):
                                pass
                            dwt.dwt_write32bitreg(R.SYS_STATUS_ID, R.SYS_STATUS_TXFRS_BIT_MASK)
                            frame_seq_nb = (frame_seq_nb + 1) & 0xFF
                            print("replied (host turnaround %d us of %d us allowed)" %
                                  (time.ticks_diff(t_queued, t_rx),
                                   POLL_RX_TO_RESP_TX_DLY_UUS * 5120 // 4992))
                        else:
                            print("TOO LATE: host took %d us; raise POLL_RX_TO_RESP_TX_DLY_UUS "
                                  "(here and in ex_06a)" % time.ticks_diff(t_queued, t_rx))
            else:
                dwt.dwt_write32bitreg(R.SYS_STATUS_ID, R.SYS_STATUS_ALL_RX_ERR)
    except KeyboardInterrupt:
        dwt.dwt_forcetrxoff()
        print("\nStopped by user (Ctrl-C). Transceiver forced off.")


if __name__ == "__main__":
    main()
