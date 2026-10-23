"""
examples/ex_06a_ss_twr_initiator/main.py

MicroPython port of the upstream ex_06a_ss_twr_initiator example
(ss_twr_initiator.c): single-sided two-way ranging, initiator side.

It sends a "poll" frame (recording the poll's TX timestamp) and waits for the
responder's "response" frame, which carries the responder's poll-RX and
response-TX timestamps. From those and its own poll-TX / response-RX timestamps
it computes the time of flight and prints the distance. Run
ex_06b_ss_twr_responder on the other board (start the responder first).

Differences from upstream, all forced by MicroPython being slower than C:

  * The responder cannot turn a poll around in 650 us (upstream), because
    reading the frame and queueing the reply over SPI takes several ms in
    Python. The reply is still sent at a precise, hardware-timed instant, just
    later: POLL_RX_TO_RESP_TX_DLY_UUS is 20000 here. The initiator's receive
    window is moved to match (see below). Upstream's relation is kept:
    RX-enable delay = reply delay - 410 uus, frame-wait timeout = 400 uus.
  * SS-TWR accuracy depends on the clock offset between the boards multiplied
    by the reply delay, so a 20 ms delay makes this noticeably less accurate
    than upstream's 650 us (the clock offset correction helps a lot, but not
    perfectly). Expect tens of cm of noise; ex_05 (double-sided) does not have
    this weakness.
  * RP2040 MicroPython floats are 32-bit. The round-trip times here are ~10^9
    device ticks, which float32 cannot represent to the tick (a straight port
    of the C float math would be off by roughly 10-20 cm), so the time of
    flight is computed with exact integer arithmetic and only the final small
    values use floats. The formula is the same as upstream's.
"""

import utime as time

from platform.port import port
from decadriver import deca_device as dwt
from decadriver import deca_regs as R
from decadriver.deca_types import DwtConfig
from config_options import txconfig_options
from shared_data.shared_defines import SPEED_OF_LIGHT, RXFLEN_MASK, UUS_TO_DWT_TIME
from shared_data.shared_functions import resp_msg_get_ts

APP_NAME = "SS TWR INIT (MicroPython) v0.1"

# Default communication configuration -- non-STS DW mode. Must match the
# responder (ex_06b).
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

# Default antenna delay values for 64 MHz PRF. The sum is the TX-to-RX antenna
# delay and should be calibrated per device; these defaults are expected to
# leave a fixed error in the distance.
TX_ANT_DLY = 16385
RX_ANT_DLY = 16385

# Frames used in the ranging process (IEEE 802.15.4 data frames):
#   0-1 frame control (0x8841), 2 sequence number, 3-4 PAN ID (0xDECA),
#   5-6 destination address, 7-8 source address, 9 function code,
#   response: 10-13 poll RX timestamp, 14-17 response TX timestamp,
#   last 2 bytes: checksum, filled in by the DW IC.
tx_poll_msg = bytearray([0x41, 0x88, 0, 0xCA, 0xDE, ord('W'), ord('A'), ord('V'), ord('E'), 0xE0, 0, 0])
rx_resp_msg = bytes([0x41, 0x88, 0, 0xCA, 0xDE, ord('V'), ord('E'), ord('W'), ord('A'), 0xE1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0])
ALL_MSG_COMMON_LEN = 10     # up to and including the function code
ALL_MSG_SN_IDX = 2
RESP_MSG_POLL_RX_TS_IDX = 10
RESP_MSG_RESP_TX_TS_IDX = 14
RX_BUF_LEN = 20

# --- timing (uus; 1 uus = 512/499.2 us ~ 1.0256 us) ------------------------
# Delay from poll RX to response TX that the RESPONDER uses. Must be the same
# value in ex_06b. It has to be longer than the responder's host turnaround.
POLL_RX_TO_RESP_TX_DLY_UUS = 20000
# Extra slack, each side of the expected response, added to the receive window.
# Upstream's window (reply delay - 410 .. +400 uus) is only ~170 us from its end
# when the response preamble starts. That is fine with a 650 us delay, but with
# a 20 ms delay a small error in a time-unit assumption (2.5% of 20 ms is 500 us)
# would miss the response entirely. Set to 0 to get upstream's exact window.
WINDOW_MARGIN_UUS = 1000
# Initiator: end of poll TX -> receiver on. Upstream: 240 for a 650 reply delay.
POLL_TX_TO_RESP_RX_DLY_UUS = POLL_RX_TO_RESP_TX_DLY_UUS - 410 - WINDOW_MARGIN_UUS
# Initiator: how long the receiver stays on waiting for the response.
RESP_RX_TIMEOUT_UUS = 400 + 2 * WINDOW_MARGIN_UUS

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

    dwt.dwt_setleds(R.DWT_LEDS_ENABLE | R.DWT_LEDS_INIT_BLINK)

    if dwt.dwt_configure(config):
        print("CONFIG FAILED")
        while True:
            pass

    # TX spectrum parameters (power, PG delay and PG count)
    dwt.dwt_configuretxrf(txconfig_options)

    # Antenna delays
    dwt.dwt_setrxantennadelay(RX_ANT_DLY)
    dwt.dwt_settxantennadelay(TX_ANT_DLY)

    # Expected response's delay and timeout (one response, always the same).
    dwt.dwt_setrxaftertxdelay(POLL_TX_TO_RESP_RX_DLY_UUS)
    dwt.dwt_setrxtimeout(RESP_RX_TIMEOUT_UUS)

    # TX/RX state output on GPIOs, for debug (as upstream)
    dwt.dwt_setlnapamode(R.DWT_LNA_ENABLE | R.DWT_PA_ENABLE)

    print("Initiator ready (response delay %d uus, rx on after %d uus, timeout %d uus)" %
          (POLL_RX_TO_RESP_TX_DLY_UUS, POLL_TX_TO_RESP_RX_DLY_UUS, RESP_RX_TIMEOUT_UUS))

    frame_seq_nb = 0
    try:
        while True:
            # Write frame data to the DW IC and prepare transmission.
            tx_poll_msg[ALL_MSG_SN_IDX] = frame_seq_nb
            dwt.dwt_write32bitreg(R.SYS_STATUS_ID, R.SYS_STATUS_TXFRS_BIT_MASK)
            dwt.dwt_writetxdata(len(tx_poll_msg), tx_poll_msg, 0)   # zero offset in TX buffer
            dwt.dwt_writetxfctrl(len(tx_poll_msg), 0, 1)            # zero offset, ranging

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
            t_evt = time.ticks_diff(time.ticks_us(), t_tx)      # host-measured, +/- ~0.5 ms

            # Increment frame sequence number after the poll (modulo 256).
            frame_seq_nb = (frame_seq_nb + 1) & 0xFF

            if status_reg & R.SYS_STATUS_RXFCG_BIT_MASK:
                # Clear good RX frame event.
                dwt.dwt_write32bitreg(R.SYS_STATUS_ID, R.SYS_STATUS_RXFCG_BIT_MASK)

                frame_len = dwt.dwt_read32bitreg(R.RX_FINFO_ID) & RXFLEN_MASK
                if frame_len <= RX_BUF_LEN:
                    rx_buffer = bytearray(dwt.dwt_readrxdata(frame_len, 0))

                    # Check this is the expected response. The sequence number
                    # is not relevant, so clear it to simplify the comparison.
                    rx_buffer[ALL_MSG_SN_IDX] = 0
                    if bytes(rx_buffer[:ALL_MSG_COMMON_LEN]) == rx_resp_msg[:ALL_MSG_COMMON_LEN]:
                        # Poll TX and response RX timestamps (low 32 bits).
                        poll_tx_ts = dwt.dwt_readtxtimestamplo32()
                        resp_rx_ts = dwt.dwt_readrxtimestamplo32()

                        # Raw clock offset (signed, units of 2^-26). Positive:
                        # our clock runs faster than the responder's.
                        clock_offset_raw = dwt.dwt_readclockoffset()

                        # Timestamps embedded in the response message.
                        poll_rx_ts = resp_msg_get_ts(rx_buffer, RESP_MSG_POLL_RX_TS_IDX)
                        resp_tx_ts = resp_msg_get_ts(rx_buffer, RESP_MSG_RESP_TX_TS_IDX)

                        # Round-trip delays. The 32-bit device-time counters
                        # wrap, so subtract modulo 2^32 (delays are positive
                        # and well under 2^32 ticks = ~67 ms).
                        rtd_init = (resp_rx_ts - poll_tx_ts) & 0xFFFFFFFF
                        rtd_resp = (resp_tx_ts - poll_rx_ts) & 0xFFFFFFFF

                        # tof = (rtd_init - rtd_resp * (1 - clockOffsetRatio)) / 2,
                        # with clockOffsetRatio = raw / 2^26, in device ticks.
                        # Exact integers: float32 cannot hold ~10^9 ticks.
                        diff = rtd_init - rtd_resp
                        corr = (rtd_resp * clock_offset_raw) / (1 << 26)   # a few hundred ticks
                        tof_ticks = (diff + corr) / 2.0
                        tof = tof_ticks * DWT_TIME_UNITS
                        distance = tof * SPEED_OF_LIGHT

                        # Same distance WITHOUT the clock offset correction, shown
                        # so a first hardware run can tell whether the correction
                        # helps (it should pull the result toward the true value).
                        dist_uncorrected = (diff / 2.0) * DWT_TIME_UNITS * SPEED_OF_LIGHT

                        print("dist %3.2f m   (offset %+.2f ppm, uncorrected %3.2f m)" %
                              (distance, clock_offset_raw * 1e6 / (1 << 26), dist_uncorrected))
                        # Timing diagnostics: the chip-measured poll->response time
                        # should be the reply delay (+ ~2 x time of flight).
                        print("   round trip %.2f uus (reply delay %d uus), response seen after %d us" %
                              (rtd_init / UUS_TO_DWT_TIME, POLL_RX_TO_RESP_TX_DLY_UUS, t_evt))
                    else:
                        print("unexpected frame, len", frame_len)
            else:
                # Clear RX error/timeout events.
                dwt.dwt_write32bitreg(R.SYS_STATUS_ID, R.SYS_STATUS_ALL_RX_TO | R.SYS_STATUS_ALL_RX_ERR)
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
