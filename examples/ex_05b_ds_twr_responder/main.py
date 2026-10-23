"""
examples/ex_05b_ds_twr_responder/main.py

MicroPython port of the upstream ex_05b_ds_twr_resp example
(ds_twr_responder.c): double-sided two-way ranging, responder side.

Exchange:  initiator POLL ->  responder RESPONSE ->  initiator FINAL.
This side answers the poll, receives the final (which carries the initiator's
timestamps), computes the time of flight from all six timestamps and PRINTS THE
DISTANCE. Start this one first, then ex_05a_ds_twr_initiator.

See ex_05a for why the delays are 15000 uus instead of upstream's ~700-900, why
the receive windows are widened, and why the preamble timeout is off. Two more
MicroPython points specific to this side:
  * The time-of-flight formula multiplies round-trip times of ~10^9 device
    ticks, giving products of ~10^18, beyond what a 32-bit float (or even a
    double) holds exactly. It is computed with exact integers.
  * After a successful exchange the example waits RNG_DELAY_MS - 100 ms before
    listening again (upstream: RNG_DELAY_MS - 10): the initiator's next poll comes
    1 s after its final, and our slower host needs the extra head start.
"""

import utime as time

from platform.port import port
from decadriver import deca_device as dwt
from decadriver import deca_regs as R
from decadriver.deca_types import DwtConfig
from config_options import txconfig_options
from shared_data.shared_defines import FRAME_LEN_MAX_EX, UUS_TO_DWT_TIME, SPEED_OF_LIGHT
from shared_data.shared_functions import (get_tx_timestamp_u64, get_rx_timestamp_u64,
                                          final_msg_get_ts)

APP_NAME = "DS TWR RESP (MicroPython) v0.1"

# Default communication configuration -- must match the initiator (ex_05a).
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

# Inter-ranging delay period of the initiator, in milliseconds.
RNG_DELAY_MS = 1000

# Default antenna delay values for 64 MHz PRF.
TX_ANT_DLY = 16385
RX_ANT_DLY = 16385

# Frames used in the ranging process (see ex_05a for the layout).
rx_poll_msg = bytes([0x41, 0x88, 0, 0xCA, 0xDE, ord('W'), ord('A'), ord('V'), ord('E'), 0x21, 0, 0])
tx_resp_msg = bytearray([0x41, 0x88, 0, 0xCA, 0xDE, ord('V'), ord('E'), ord('W'), ord('A'), 0x10, 0x02, 0, 0, 0, 0])
rx_final_msg = bytes([0x41, 0x88, 0, 0xCA, 0xDE, ord('W'), ord('A'), ord('V'), ord('E'), 0x23,
                      0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0])
ALL_MSG_COMMON_LEN = 10
ALL_MSG_SN_IDX = 2
FINAL_MSG_POLL_TX_TS_IDX = 10
FINAL_MSG_RESP_RX_TS_IDX = 14
FINAL_MSG_FINAL_TX_TS_IDX = 18
RX_BUF_LEN = 24

# --- timing (uus). Must match ex_05a. ----------------------------------------
# Delay from poll RX to response TX (responder's reply delay).
POLL_RX_TO_RESP_TX_DLY_UUS = 15000
# Delay from response RX to final TX that the INITIATOR uses.
RESP_RX_TO_FINAL_TX_DLY_UUS = 15000
# Slack added each side of the expected final frame.
WINDOW_MARGIN_UUS = 1000
# End of response TX -> receiver on. Upstream: 500 for a 700 final delay.
RESP_TX_TO_FINAL_RX_DLY_UUS = RESP_RX_TO_FINAL_TX_DLY_UUS - 200 - WINDOW_MARGIN_UUS
# How long the receiver stays on for the final. Upstream: 220.
FINAL_RX_TIMEOUT_UUS = 220 + 2 * WINDOW_MARGIN_UUS
# Preamble detect timeout, in PACs. Upstream: 5. 0 = off.
PRE_TIMEOUT = 0

DWT_TIME_UNITS = R.DWT_TIME_UNITS


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

    if dwt.dwt_configure(config):
        print("CONFIG FAILED")
        while True:
            pass

    dwt.dwt_configuretxrf(txconfig_options)

    dwt.dwt_setrxantennadelay(RX_ANT_DLY)
    dwt.dwt_settxantennadelay(TX_ANT_DLY)

    dwt.dwt_setlnapamode(R.DWT_LNA_ENABLE | R.DWT_PA_ENABLE)

    dwt.dwt_setleds(R.DWT_LEDS_ENABLE | R.DWT_LEDS_INIT_BLINK)

    print("Responder ready (reply delays %d / %d uus, final rx on after %d uus, timeout %d uus)" %
          (POLL_RX_TO_RESP_TX_DLY_UUS, RESP_RX_TO_FINAL_TX_DLY_UUS,
           RESP_TX_TO_FINAL_RX_DLY_UUS, FINAL_RX_TIMEOUT_UUS))

    frame_seq_nb = 0
    try:
        while True:
            dwt.dwt_setpreambledetecttimeout(0)
            # Clear reception timeout to start the next ranging process.
            dwt.dwt_setrxtimeout(0)

            # Activate reception immediately.
            dwt.dwt_rxenable(R.DWT_START_RX_IMMEDIATE)

            # Poll for reception of a frame or error/timeout.
            while True:
                status_reg = dwt.dwt_read32bitreg(R.SYS_STATUS_ID)
                if status_reg & (R.SYS_STATUS_RXFCG_BIT_MASK |
                                 R.SYS_STATUS_ALL_RX_TO |
                                 R.SYS_STATUS_ALL_RX_ERR):
                    break

            if status_reg & R.SYS_STATUS_RXFCG_BIT_MASK:
                t_rx = time.ticks_us()

                # Clear good RX frame event.
                dwt.dwt_write32bitreg(R.SYS_STATUS_ID, R.SYS_STATUS_RXFCG_BIT_MASK)

                frame_len = dwt.dwt_read32bitreg(R.RX_FINFO_ID) & FRAME_LEN_MAX_EX
                rx_buffer = bytearray(dwt.dwt_readrxdata(frame_len, 0)) if frame_len <= RX_BUF_LEN else bytearray(RX_BUF_LEN)

                # Is it a poll from the ex_05a initiator? (Sequence number is
                # not relevant: clear it before comparing.)
                rx_buffer[ALL_MSG_SN_IDX] = 0
                if bytes(rx_buffer[:ALL_MSG_COMMON_LEN]) == rx_poll_msg[:ALL_MSG_COMMON_LEN]:
                    # Poll reception timestamp (40-bit).
                    poll_rx_ts = get_rx_timestamp_u64()

                    # Response TX time (high 32 bits of the 40-bit time).
                    resp_tx_time = ((poll_rx_ts + POLL_RX_TO_RESP_TX_DLY_UUS * UUS_TO_DWT_TIME) >> 8) & 0xFFFFFFFF
                    dwt.dwt_setdelayedtrxtime(resp_tx_time)

                    # Expected delay and timeout for the final message reception.
                    dwt.dwt_setrxaftertxdelay(RESP_TX_TO_FINAL_RX_DLY_UUS)
                    dwt.dwt_setrxtimeout(FINAL_RX_TIMEOUT_UUS)

                    # Preamble timeout for expected frames.
                    dwt.dwt_setpreambledetecttimeout(PRE_TIMEOUT)

                    # Write and send the response message.
                    tx_resp_msg[ALL_MSG_SN_IDX] = frame_seq_nb
                    dwt.dwt_writetxdata(len(tx_resp_msg), tx_resp_msg, 0)
                    dwt.dwt_writetxfctrl(len(tx_resp_msg), 0, 1)        # zero offset, ranging
                    ret = dwt.dwt_starttx(R.DWT_START_TX_DELAYED | R.DWT_RESPONSE_EXPECTED)
                    t_queued = time.ticks_us()

                    # If dwt_starttx() fails (delayed time already passed),
                    # abandon this ranging exchange.
                    if ret == R.DWT_ERROR:
                        print("TOO LATE: host took %d us; raise POLL_RX_TO_RESP_TX_DLY_UUS "
                              "(here and in ex_05a)" % time.ticks_diff(t_queued, t_rx))
                        continue

                    # Poll for reception of the expected "final" frame or error/timeout.
                    while True:
                        status_reg = dwt.dwt_read32bitreg(R.SYS_STATUS_ID)
                        if status_reg & (R.SYS_STATUS_RXFCG_BIT_MASK |
                                         R.SYS_STATUS_ALL_RX_TO |
                                         R.SYS_STATUS_ALL_RX_ERR):
                            break

                    # Increment frame sequence number after the response (modulo 256).
                    frame_seq_nb = (frame_seq_nb + 1) & 0xFF

                    if status_reg & R.SYS_STATUS_RXFCG_BIT_MASK:
                        # Clear good RX frame event and TX frame sent.
                        dwt.dwt_write32bitreg(R.SYS_STATUS_ID, R.SYS_STATUS_RXFCG_BIT_MASK | R.SYS_STATUS_TXFRS_BIT_MASK)

                        frame_len = dwt.dwt_read32bitreg(R.RX_FINFO_ID) & FRAME_LEN_MAX_EX
                        rx_buffer = bytearray(dwt.dwt_readrxdata(frame_len, 0)) if frame_len <= RX_BUF_LEN else bytearray(RX_BUF_LEN)

                        # Is it the final message from the ex_05a initiator?
                        rx_buffer[ALL_MSG_SN_IDX] = 0
                        if bytes(rx_buffer[:ALL_MSG_COMMON_LEN]) == rx_final_msg[:ALL_MSG_COMMON_LEN]:
                            # Response transmission and final reception timestamps.
                            resp_tx_ts = get_tx_timestamp_u64()
                            final_rx_ts = get_rx_timestamp_u64()

                            # Timestamps embedded in the final message (32 bit).
                            poll_tx_ts = final_msg_get_ts(rx_buffer, FINAL_MSG_POLL_TX_TS_IDX)
                            resp_rx_ts = final_msg_get_ts(rx_buffer, FINAL_MSG_RESP_RX_TS_IDX)
                            final_tx_ts = final_msg_get_ts(rx_buffer, FINAL_MSG_FINAL_TX_TS_IDX)

                            # Time of flight. The 32-bit counters wrap, so all
                            # differences are taken modulo 2^32 (they are positive
                            # and below 2^32 ticks, ~67 ms).
                            poll_rx_ts_32 = poll_rx_ts & 0xFFFFFFFF
                            resp_tx_ts_32 = resp_tx_ts & 0xFFFFFFFF
                            final_rx_ts_32 = final_rx_ts & 0xFFFFFFFF
                            Ra = (resp_rx_ts - poll_tx_ts) & 0xFFFFFFFF
                            Rb = (final_rx_ts_32 - resp_tx_ts_32) & 0xFFFFFFFF
                            Da = (final_tx_ts - resp_rx_ts) & 0xFFFFFFFF
                            Db = (resp_tx_ts_32 - poll_rx_ts_32) & 0xFFFFFFFF
                            # tof = (Ra*Rb - Da*Db) / (Ra + Rb + Da + Db), exact
                            # integer numerator/denominator; truncated to whole
                            # device ticks as upstream does.
                            tof_dtu = int((Ra * Rb - Da * Db) / (Ra + Rb + Da + Db))

                            tof = tof_dtu * DWT_TIME_UNITS
                            distance = tof * SPEED_OF_LIGHT

                            print("dist %3.2f m" % distance)
                            print("   Ra %.2f Rb %.2f Da %.2f Db %.2f uus; host turnaround %d us of %d us allowed" %
                                  (Ra / UUS_TO_DWT_TIME, Rb / UUS_TO_DWT_TIME,
                                   Da / UUS_TO_DWT_TIME, Db / UUS_TO_DWT_TIME,
                                   time.ticks_diff(t_queued, t_rx),
                                   POLL_RX_TO_RESP_TX_DLY_UUS * 5120 // 4992))

                            # The initiator waits RNG_DELAY_MS before the next poll,
                            # so wait here too before listening again.
                            time.sleep_ms(RNG_DELAY_MS - 100)
                        else:
                            print("unexpected frame in place of final, len", frame_len)
                    else:
                        # Clear RX error/timeout events.
                        dwt.dwt_write32bitreg(R.SYS_STATUS_ID, R.SYS_STATUS_ALL_RX_TO | R.SYS_STATUS_ALL_RX_ERR)
                        print("no final received, status 0x%08X" %
                              (status_reg & (R.SYS_STATUS_ALL_RX_TO | R.SYS_STATUS_ALL_RX_ERR)))
            else:
                # Clear RX error/timeout events.
                dwt.dwt_write32bitreg(R.SYS_STATUS_ID, R.SYS_STATUS_ALL_RX_TO | R.SYS_STATUS_ALL_RX_ERR)
    except KeyboardInterrupt:
        dwt.dwt_forcetrxoff()
        print("\nStopped by user (Ctrl-C). Transceiver forced off.")


if __name__ == "__main__":
    main()
