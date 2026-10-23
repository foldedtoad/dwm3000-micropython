"""
examples/ex_05a_ds_twr_initiator/main.py

MicroPython port of the upstream ex_05a_ds_twr_init example
(ds_twr_initiator.c): double-sided two-way ranging, initiator side.

Exchange:  initiator POLL ->  responder RESPONSE ->  initiator FINAL.
The FINAL frame carries the initiator's poll-TX, response-RX and final-TX
timestamps; the RESPONDER combines them with its own and computes and prints
the distance (as upstream does), so look at ex_05b's output for distances.
Start ex_05b_ds_twr_responder first.

Differences from upstream, all forced by MicroPython being slower than C
(the same changes as ex_06, see there):
  * The reply delays are 15000 uus instead of ~700-900: reading a frame and
    queueing the reply takes several ms over MicroPython SPI. Replies are still
    sent at a precise hardware-timed instant, just later. The delays are the
    same in both examples: POLL_RX_TO_RESP_TX_DLY_UUS (responder's) and
    RESP_RX_TO_FINAL_TX_DLY_UUS (this file's). Upstream's relations between
    them and the receive windows are kept (window delay = reply delay - 200).
  * Receive windows are widened by WINDOW_MARGIN_UUS on each side (ex_06
    showed upstream's tight window missing the response at long delays), and
    the preamble-detect timeout is off (upstream's 5 PACs only works with a
    window precise to ~20 us).
  * Unlike single-sided ranging, double-sided ranging cancels the clock offset
    between the boards to first order, so the long delays do not hurt accuracy.
"""

import utime as time

from platform.port import port
from decadriver import deca_device as dwt
from decadriver import deca_regs as R
from decadriver.deca_types import DwtConfig
from config_options import txconfig_options
from shared_data.shared_defines import FRAME_LEN_MAX_EX, UUS_TO_DWT_TIME
from shared_data.shared_functions import get_tx_timestamp_u64, get_rx_timestamp_u64, final_msg_set_ts

APP_NAME = "DS TWR INIT (MicroPython) v0.1"

# Default communication configuration -- non-STS DW mode. Must match the
# responder (ex_05b).
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

# Inter-ranging delay period, in milliseconds.
RNG_DELAY_MS = 1000

# Default antenna delay values for 64 MHz PRF (calibrate per device in a real
# product).
TX_ANT_DLY = 16385
RX_ANT_DLY = 16385

# Frames used in the ranging process (IEEE 802.15.4 data frames):
#   0-1 frame control (0x8841), 2 sequence number, 3-4 PAN ID (0xDECA),
#   5-6 destination, 7-8 source, 9 function code,
#   response: 10 activity code (0x02 = go on), 11-12 parameter (unused),
#   final: 10-13 poll TX ts, 14-17 response RX ts, 18-21 final TX ts.
# The DW IC appends the 2-byte checksum.
tx_poll_msg = bytearray([0x41, 0x88, 0, 0xCA, 0xDE, ord('W'), ord('A'), ord('V'), ord('E'), 0x21])
rx_resp_msg = bytes([0x41, 0x88, 0, 0xCA, 0xDE, ord('V'), ord('E'), ord('W'), ord('A'), 0x10, 0x02, 0, 0])
tx_final_msg = bytearray([0x41, 0x88, 0, 0xCA, 0xDE, ord('W'), ord('A'), ord('V'), ord('E'), 0x23,
                          0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0])
ALL_MSG_COMMON_LEN = 10     # up to and including the function code
ALL_MSG_SN_IDX = 2
FINAL_MSG_POLL_TX_TS_IDX = 10
FINAL_MSG_RESP_RX_TS_IDX = 14
FINAL_MSG_FINAL_TX_TS_IDX = 18
FCS_LEN = R.FCS_LEN
RX_BUF_LEN = 20

# --- timing (uus; 1 uus = 512/499.2 us ~ 1.0256 us) ------------------------
# Delay from poll RX to response TX that the RESPONDER uses (same value in
# ex_05b). Must be longer than the responder's host turnaround.
POLL_RX_TO_RESP_TX_DLY_UUS = 15000
# Delay from response RX to final TX that THIS side uses (same value in ex_05b).
RESP_RX_TO_FINAL_TX_DLY_UUS = 15000
# Slack added each side of the expected response (see the module docstring).
WINDOW_MARGIN_UUS = 1000
# End of poll TX -> receiver on. Upstream: 700 for a 900 reply delay.
POLL_TX_TO_RESP_RX_DLY_UUS = POLL_RX_TO_RESP_TX_DLY_UUS - 200 - WINDOW_MARGIN_UUS
# How long the receiver stays on for the response. Upstream: 300.
RESP_RX_TIMEOUT_UUS = 300 + 2 * WINDOW_MARGIN_UUS
# Preamble detect timeout, in PACs. Upstream: 5. 0 = off (see docstring).
PRE_TIMEOUT = 0


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

    # Expected response's delay and timeout (one response, always the same).
    dwt.dwt_setrxaftertxdelay(POLL_TX_TO_RESP_RX_DLY_UUS)
    dwt.dwt_setrxtimeout(RESP_RX_TIMEOUT_UUS)
    dwt.dwt_setpreambledetecttimeout(PRE_TIMEOUT)

    # TX/RX state output on GPIOs, for debug (as upstream)
    dwt.dwt_setlnapamode(R.DWT_LNA_ENABLE | R.DWT_PA_ENABLE)

    dwt.dwt_setleds(R.DWT_LEDS_ENABLE | R.DWT_LEDS_INIT_BLINK)

    print("Initiator ready (reply delays %d / %d uus, rx on after %d uus, timeout %d uus)" %
          (POLL_RX_TO_RESP_TX_DLY_UUS, RESP_RX_TO_FINAL_TX_DLY_UUS,
           POLL_TX_TO_RESP_RX_DLY_UUS, RESP_RX_TIMEOUT_UUS))

    frame_seq_nb = 0
    try:
        while True:
            # Write the poll and prepare transmission.
            tx_poll_msg[ALL_MSG_SN_IDX] = frame_seq_nb
            dwt.dwt_writetxdata(len(tx_poll_msg), tx_poll_msg, 0)       # zero offset in TX buffer
            dwt.dwt_writetxfctrl(len(tx_poll_msg) + FCS_LEN, 0, 1)      # zero offset, ranging

            # Start transmission; reception is enabled automatically after the
            # frame is sent and the delay set by dwt_setrxaftertxdelay elapses.
            t_tx = time.ticks_us()
            dwt.dwt_starttx(R.DWT_START_TX_IMMEDIATE | R.DWT_RESPONSE_EXPECTED)

            # Poll for reception of a frame or error/timeout.
            while True:
                status_reg = dwt.dwt_read32bitreg(R.SYS_STATUS_ID)
                if status_reg & (R.SYS_STATUS_RXFCG_BIT_MASK |
                                 R.SYS_STATUS_ALL_RX_TO |
                                 R.SYS_STATUS_ALL_RX_ERR):
                    break
            t_evt = time.ticks_diff(time.ticks_us(), t_tx)

            # Increment frame sequence number after the poll (modulo 256).
            frame_seq_nb = (frame_seq_nb + 1) & 0xFF

            if status_reg & R.SYS_STATUS_RXFCG_BIT_MASK:
                t_rx = time.ticks_us()

                # Clear good RX frame event and TX frame sent.
                dwt.dwt_write32bitreg(R.SYS_STATUS_ID, R.SYS_STATUS_RXFCG_BIT_MASK | R.SYS_STATUS_TXFRS_BIT_MASK)

                frame_len = dwt.dwt_read32bitreg(R.RX_FINFO_ID) & FRAME_LEN_MAX_EX
                rx_buffer = bytearray(dwt.dwt_readrxdata(frame_len, 0)) if frame_len <= RX_BUF_LEN else bytearray(RX_BUF_LEN)

                # Is it the expected response? (Sequence number is not relevant:
                # clear it to simplify the comparison.)
                rx_buffer[ALL_MSG_SN_IDX] = 0
                if bytes(rx_buffer[:ALL_MSG_COMMON_LEN]) == rx_resp_msg[:ALL_MSG_COMMON_LEN]:
                    # Poll transmission and response reception timestamps (40-bit).
                    poll_tx_ts = get_tx_timestamp_u64()
                    resp_rx_ts = get_rx_timestamp_u64()

                    # Final message transmission time: DX_TIME takes the high 32
                    # bits of the 40-bit time (shift by 8, keep 32 bits).
                    final_tx_time = ((resp_rx_ts + RESP_RX_TO_FINAL_TX_DLY_UUS * UUS_TO_DWT_TIME) >> 8) & 0xFFFFFFFF
                    dwt.dwt_setdelayedtrxtime(final_tx_time)

                    # Final TX timestamp = programmed time (bit 0 ignored by the
                    # chip) plus the TX antenna delay.
                    final_tx_ts = ((final_tx_time & 0xFFFFFFFE) << 8) + TX_ANT_DLY

                    # Write all timestamps (low 32 bits) into the final message.
                    final_msg_set_ts(tx_final_msg, FINAL_MSG_POLL_TX_TS_IDX, poll_tx_ts)
                    final_msg_set_ts(tx_final_msg, FINAL_MSG_RESP_RX_TS_IDX, resp_rx_ts)
                    final_msg_set_ts(tx_final_msg, FINAL_MSG_FINAL_TX_TS_IDX, final_tx_ts)

                    # Write and send the final message.
                    tx_final_msg[ALL_MSG_SN_IDX] = frame_seq_nb
                    dwt.dwt_writetxdata(len(tx_final_msg), tx_final_msg, 0)
                    dwt.dwt_writetxfctrl(len(tx_final_msg) + FCS_LEN, 0, 1)

                    # If dwt_starttx() fails (the delayed time had already
                    # passed), abandon this exchange.
                    ret = dwt.dwt_starttx(R.DWT_START_TX_DELAYED)
                    t_queued = time.ticks_us()
                    if ret == R.DWT_SUCCESS:
                        while not (dwt.dwt_read32bitreg(R.SYS_STATUS_ID) & R.SYS_STATUS_TXFRS_BIT_MASK):
                            pass
                        dwt.dwt_write32bitreg(R.SYS_STATUS_ID, R.SYS_STATUS_TXFRS_BIT_MASK)
                        frame_seq_nb = (frame_seq_nb + 1) & 0xFF
                        print("final sent (host turnaround %d us of %d us allowed); distance is shown by the responder" %
                              (time.ticks_diff(t_queued, t_rx), RESP_RX_TO_FINAL_TX_DLY_UUS * 5120 // 4992))
                    else:
                        print("TOO LATE: host took %d us; raise RESP_RX_TO_FINAL_TX_DLY_UUS "
                              "(here and in ex_05b)" % time.ticks_diff(t_queued, t_rx))
                else:
                    print("unexpected frame, len", frame_len)
            else:
                # Clear RX error/timeout events.
                dwt.dwt_write32bitreg(R.SYS_STATUS_ID,
                                      R.SYS_STATUS_ALL_RX_TO | R.SYS_STATUS_ALL_RX_ERR | R.SYS_STATUS_TXFRS_BIT_MASK)
                print("no valid response, status 0x%08X, gave up after %d us (window closes at ~%d us)" %
                      (status_reg & (R.SYS_STATUS_ALL_RX_TO | R.SYS_STATUS_ALL_RX_ERR), t_evt,
                       (POLL_TX_TO_RESP_RX_DLY_UUS + RESP_RX_TIMEOUT_UUS) * 5120 // 4992))

            # Delay between ranging exchanges.
            time.sleep_ms(RNG_DELAY_MS)
    except KeyboardInterrupt:
        dwt.dwt_forcetrxoff()
        print("\nStopped by user (Ctrl-C). Transceiver forced off.")


if __name__ == "__main__":
    main()
