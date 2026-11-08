"""
examples/ex_01d_tx_timed_sleep/main.py

MicroPython port of the upstream ex_01d_tx_timed_sleep example
(tx_timed_sleep.c): send a blink, put the DW3000 to sleep, and let the chip's own
SLEEP TIMER wake it again -- no WAKEUP pin or CS pulse from the host. The chip
announces that it is awake with an SPI-ready interrupt; the callback waits for
IDLE_RC, restores the configuration, and clears the `sleeping` flag.

Needs the DW3000 IRQ line wired to the Pico (Flexypin GP2). On a board with no
known IRQ pin (ArduPico, see platform/port.py) the example stops with a message.

Timing: the sleep counter holds the top 16 bits of a 28-bit counter clocked by
the chip's low-power oscillator (15..34 kHz), so the sleep time comes in steps of
4096 / f_lp = 120..273 ms, rounded DOWN from the requested SLEEP_TIME_MS
(upstream NOTE 2). The example prints the calibrated frequency, the counter value,
the nominal sleep time, and the period actually measured between frames.

Pair with ex_02a_simple_rx on a second board: its sequence numbers must advance
by one per frame.
"""

import utime as time

from platform.port import port
from decadriver import deca_device as dwt
from decadriver import deca_regs as R
from decadriver.deca_types import DwtConfig
from config_options import txconfig_options

APP_NAME = "TX TIME SLP (MicroPython) v0.1"

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

# 802.15.4e blink frame (12 bytes incl. the 2 checksum bytes, set by the IC).
tx_msg = bytearray(b"\xC5\x00DECAWAVE\x00\x00")
BLINK_FRAME_SN_IDX = 1

# Inter-frame delay period, in milliseconds.
TX_DELAY_MS = 1000

# Sleep a little less than the frame period so the chip has time to wake up and
# the next frame is prepared (upstream: TX_DELAY_MS - 10).
SLEEP_TIME_MS = TX_DELAY_MS - 10

# Set by the main loop when the chip is put to sleep, cleared by the SPI-ready
# callback when it wakes up.
sleeping = False

# Warn if the chip has not woken this long after its sleep should have ended.
WAKE_WATCHDOG_MS = 3000


def spi_ready_cb(cb_data):
    """SPI ready / IDLE_RC event: the chip has woken up."""
    global sleeping
    while not dwt.dwt_checkidlerc():   # make sure the device is in IDLE_RC
        pass
    dwt.dwt_restoreconfig()            # restore what sleep does not keep
    sleeping = False                   # device is awake


def main():
    global sleeping
    print(APP_NAME)

    if port.irq is None:
        print("This example needs the DW3000 IRQ line, which is not defined for this board (see platform/port.py).")
        return

    port.spi_set_rate_high()
    port.reset_dwic()
    time.sleep_ms(2)  # INIT_RC -> IDLE_RC

    while not dwt.dwt_checkidlerc():
        pass

    if dwt.dwt_initialise(R.DWT_DW_INIT) == R.DWT_ERROR:
        print("INIT FAILED")
        while True:
            pass

    # Clear the SPI ready interrupt left over from power-up.
    dwt.dwt_write32bitreg(R.SYS_STATUS_ID, R.SYS_STATUS_RCINIT_BIT_MASK | R.SYS_STATUS_SPIRDY_BIT_MASK)

    # Install the DW IC IRQ handler (the IRQ line has a pull-down so it cannot
    # trigger by itself while the chip sleeps).
    port.set_dwic_isr(dwt.dwt_isr)

    # Calibrate the low-power oscillator and set the sleep counter.
    cal = dwt.dwt_calibratesleepcnt()
    lp_osc_freq = R.XTAL_FREQ_HZ // cal
    sleep_cnt = ((SLEEP_TIME_MS * lp_osc_freq) // 1000) >> 12
    dwt.dwt_configuresleepcnt(sleep_cnt)
    nominal_ms = sleep_cnt * 4096 * 1000 // lp_osc_freq
    print("LP oscillator %d Hz (%d XTAL cycles), sleep counter %d, nominal sleep %d ms (asked for %d ms, step %d ms)" %
          (lp_osc_freq, cal, sleep_cnt, nominal_ms, SLEEP_TIME_MS, 4096 * 1000 // lp_osc_freq))
    if sleep_cnt == 0:
        print("Sleep counter would be 0: SLEEP_TIME_MS is shorter than one counter step.")
        return

    if dwt.dwt_configure(config):
        print("CONFIG FAILED")
        while True:
            pass

    dwt.dwt_configuretxrf(txconfig_options)

    # Sleep (not deep sleep) with the config restored from AON on wake; wake on CS as well.
    dwt.dwt_configuresleep(R.DWT_CONFIG, R.DWT_PRES_SLEEP | R.DWT_WAKE_CSN | R.DWT_SLEEP | R.DWT_SLP_EN)

    # Only the SPI-ready callback is used.
    dwt.dwt_setcallbacks(None, None, None, None, None, spi_ready_cb)

    t_prev = None
    try:
        while True:
            dwt.dwt_writetxdata(len(tx_msg), bytes(tx_msg), 0)
            dwt.dwt_writetxfctrl(len(tx_msg), 0, 0)
            dwt.dwt_starttx(R.DWT_START_TX_IMMEDIATE)
            t_tx = time.ticks_ms()

            while not (dwt.dwt_read32bitreg(R.SYS_STATUS_ID) & R.SYS_STATUS_TXFRS_BIT_MASK):
                pass  # spin until TX frame sent
            dwt.dwt_write32bitreg(R.SYS_STATUS_ID, R.SYS_STATUS_TXFRS_BIT_MASK)

            # Sleep; the sleep timer wakes the chip, the callback clears `sleeping`.
            sleeping = True
            dwt.dwt_entersleep(R.DWT_DW_IDLE)
            t_sleep = time.ticks_ms()

            warned = False
            while sleeping:    # the loop's back-jump is where the scheduled IRQ handler runs
                if not warned and time.ticks_diff(time.ticks_ms(), t_sleep) > WAKE_WATCHDOG_MS:
                    warned = True
                    print("WARNING: chip has not woken %d ms after going to sleep. IRQ pin level=%d "
                          "(0 = no wake-up event reached the Pico; check the IRQ wire)" %
                          (WAKE_WATCHDOG_MS, port.irq.value()))

            t_wake = time.ticks_ms()
            period = "" if t_prev is None else ", %d ms since previous frame" % time.ticks_diff(t_tx, t_prev)
            t_prev = t_tx
            print("frame %d sent; slept %d ms%s" % (tx_msg[BLINK_FRAME_SN_IDX], time.ticks_diff(t_wake, t_sleep), period))

            tx_msg[BLINK_FRAME_SN_IDX] = (tx_msg[BLINK_FRAME_SN_IDX] + 1) & 0xFF
    except KeyboardInterrupt:
        port.clear_dwic_isr()
        if sleeping:
            dwt.dwt_wakeup_ic()   # wake by CS (enabled above) so the chip can be told to stop
            time.sleep_ms(2)
            while not dwt.dwt_checkidlerc():
                pass
        dwt.dwt_forcetrxoff()
        print("\nStopped by user (Ctrl-C). Transceiver forced off.")


if __name__ == "__main__":
    main()
