"""
examples/ex_01e_tx_with_cca/main.py

MicroPython port of the upstream ex_01e_tx_with_cca example (tx_with_cca.c):
transmit a blink frame with CLEAR CHANNEL ASSESSMENT. The chip first listens
for a preamble for a short time (the preamble-detect timeout, here 3 PACs); if it
hears none it transmits, and if it hears one it cancels the transmission
(CCA_FAIL) and the host backs off before trying again.

When a frame is sent, the next attempt follows after TX_DELAY_MS and the back-off
period is reset. When CCA fails, the next attempt follows after the back-off
period, which grows by 1 ms per consecutive failure (upstream's simplified
stand-in for randomised exponential back-off). INITIAL_BACKOFF_PERIOD is 400 ms
only so that the failures are easy to see on the console; a real device would
use about 1 ms.

Seeing a CCA failure needs another transmitter on the air at the same time: run
this on one board, ex_01a_simple_tx on a second one and ex_02a_simple_rx on a
third (or watch with the second board's receiver). With a quiet channel every
attempt succeeds and you only see "frame N sent".

Differences from upstream:
  * The CCA_FAIL status bit (SYS_STATUS_HI) is cleared on a failure. Upstream
    never clears it, so after the first failure the polling loop would see the
    stale bit immediately on every later attempt and report failure even when
    the chip goes on to send the frame.
  * A 100 ms guard on the polling loop, in case neither event arrives.
"""

import utime as time

from platform.port import port
from decadriver import deca_device as dwt
from decadriver import deca_regs as R
from decadriver.deca_types import DwtConfig
from config_options import txconfig_options

APP_NAME = "TX WITH CCA (MicroPython) v0.1"

# Default communication configuration -- non-STS DW mode.
config = DwtConfig(
    chan=5,
    txPreambLength=R.DWT_PLEN_128,
    rxPAC=R.DWT_PAC8,
    txCode=9,
    rxCode=9,
    sfdType=1,                 # non-standard 8 symbol SFD (as upstream)
    dataRate=R.DWT_BR_6M8,
    phrMode=R.DWT_PHRMODE_STD,
    phrRate=R.DWT_PHRRATE_STD,
    sfdTO=(129 + 8 - 8),
    stsMode=R.DWT_STS_MODE_OFF,
    stsLength=R.DWT_STS_LEN_64,
    pdoaMode=R.DWT_PDOA_M0,
)

# 802.15.4e blink frame: [frame type, seq#, device ID bytes...]
tx_msg = bytearray(b"\xC5\x00DECAWAVE")
BLINK_FRAME_SN_IDX = 1
FRAME_LENGTH = len(tx_msg) + R.FCS_LEN

# Inter-frame delay period after a successful transmission, in milliseconds.
TX_DELAY_MS = 100

# First back-off after a CCA failure, in milliseconds (see the docstring).
INITIAL_BACKOFF_PERIOD = 400

# Preamble detect timeout, in PACs: how long the chip listens before deciding
# the channel is clear.
CCA_PREAMBLE_TIMEOUT_PACS = 3

# Give up waiting for TX-done or CCA-fail after this long.
POLL_GUARD_MS = 100


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

    # LEDs for debug: D1 flashes on each TX on the red DW3000 eval shields.
    dwt.dwt_setleds(R.DWT_LEDS_ENABLE | R.DWT_LEDS_INIT_BLINK)

    if dwt.dwt_configure(config):
        print("CONFIG FAILED")
        while True:
            pass

    dwt.dwt_configuretxrf(txconfig_options)

    # If no preamble is detected within this time the channel is assumed clear.
    dwt.dwt_setpreambledetecttimeout(CCA_PREAMBLE_TIMEOUT_PACS)

    next_backoff_interval = INITIAL_BACKOFF_PERIOD
    sent = 0
    failed = 0
    try:
        while True:
            dwt.dwt_writetxdata(FRAME_LENGTH - R.FCS_LEN, bytes(tx_msg), 0)
            dwt.dwt_writetxfctrl(FRAME_LENGTH, 0, 0)    # zero offset, no ranging

            # Transmit only if no preamble is detected within the timeout above;
            # otherwise the transmission is cancelled (CCA_FAIL).
            dwt.dwt_starttx(R.DWT_START_TX_CCA)

            # Poll until TX complete or CCA fail.
            t0 = time.ticks_ms()
            cca_fail = False
            timed_out = False
            while True:
                status_reg = dwt.dwt_read32bitreg(R.SYS_STATUS_ID)
                if status_reg & R.SYS_STATUS_TXFRS_BIT_MASK:
                    break
                if dwt.dwt_read32bitreg(R.SYS_STATUS_HI_ID) & R.SYS_STATUS_HI_CCA_FAIL_BIT_MASK:
                    cca_fail = True
                    break
                if time.ticks_diff(time.ticks_ms(), t0) > POLL_GUARD_MS:
                    timed_out = True
                    break

            if status_reg & R.SYS_STATUS_TXFRS_BIT_MASK:
                tx_sleep_period = TX_DELAY_MS            # sent a frame: normal inter-frame period
                next_backoff_interval = INITIAL_BACKOFF_PERIOD
                sent += 1
                print("frame %d sent (%d sent, %d CCA failures)" % (tx_msg[BLINK_FRAME_SN_IDX], sent, failed))

                # Increment the blink frame sequence number (modulo 256).
                tx_msg[BLINK_FRAME_SN_IDX] = (tx_msg[BLINK_FRAME_SN_IDX] + 1) & 0xFF
            elif cca_fail:
                # A preamble was detected: the device is back in IDLE.
                failed += 1
                tx_sleep_period = next_backoff_interval
                print("CCA: preamble detected, frame %d not sent; backing off %d ms (%d sent, %d CCA failures)" %
                      (tx_msg[BLINK_FRAME_SN_IDX], tx_sleep_period, sent, failed))
                # If failed to transmit, increase the back-off and try again.
                # (A real implementation would randomise it within a range that
                # grows exponentially with the number of consecutive failures.)
                next_backoff_interval += 1
                dwt.dwt_write32bitreg(R.SYS_STATUS_HI_ID, R.SYS_STATUS_HI_CCA_FAIL_BIT_MASK)
            else:
                assert timed_out
                dwt.dwt_forcetrxoff()
                tx_sleep_period = TX_DELAY_MS
                print("WARNING: neither TX done nor CCA fail within %d ms; transmitter forced off" % POLL_GUARD_MS)

            # Clear TX frame sent event.
            dwt.dwt_write32bitreg(R.SYS_STATUS_ID, R.SYS_STATUS_TXFRS_BIT_MASK)

            time.sleep_ms(tx_sleep_period)
    except KeyboardInterrupt:
        dwt.dwt_forcetrxoff()
        print("\nStopped by user (Ctrl-C). Transceiver forced off.")


if __name__ == "__main__":
    main()
