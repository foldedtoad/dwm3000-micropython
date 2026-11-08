"""
examples/ex_01c_tx_sleep_auto/main.py

MicroPython port of the upstream ex_01c_tx_sleep_auto example
(tx_sleep_auto.c): the DW3000 is told to go to sleep BY ITSELF as soon as it
has sent a frame (dwt_entersleepaftertx), so the host never polls for "frame
sent" -- after dwt_starttx() the chip cannot be talked to. The host simply
waits TX_DELAY_MS, wakes the chip, restores its configuration, and sends the
next frame.

Difference from ex_01b: no TXFRS polling or explicit dwt_entersleep(); the
chip's sleep-after-TX bit does it. The IRQ line must not be asserted or the chip
will not sleep, hence the interrupt/status clearing before the loop.

Upstream enables wake on the WAKEUP pin only; DWT_WAKE_CSN is added here as well
so that a board without a WAKEUP pin (ArduPico) can wake by CS (see
port.wakeup_pulse()), exactly as in ex_01b.

Pair with ex_02a_simple_rx on a second board: its sequence numbers must advance
by one per frame. Unlike ex_01b nothing is printed after the frame is sent by the
chip itself, since the host cannot see it; "sent" means "TX started".
"""

import utime as time

from platform.port import port
from decadriver import deca_device as dwt
from decadriver import deca_regs as R
from decadriver.deca_types import DwtConfig
from config_options import txconfig_options

APP_NAME = "TX AUTO SLP (MicroPython) v0.1"

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

    # Sleep and wake-up parameters (upstream: PRES_SLEEP | WAKE_WUP | SLP_EN).
    dwt.dwt_configuresleep(R.DWT_CONFIG, R.DWT_PRES_SLEEP | R.DWT_WAKE_CSN | R.DWT_WAKE_WUP | R.DWT_SLP_EN)

    # Go to sleep automatically after each transmitted frame.
    dwt.dwt_entersleepaftertx(1)

    reg = dwt.dwt_read32bitreg(R.SYS_STATUS_ID)

    # The chip will not sleep while the interrupt line is high.
    dwt.dwt_setinterrupt(R.SYS_ENABLE_LO_SPIRDY_ENABLE_BIT_MASK, 0, R.DWT_DISABLE_INT)

    dwt.dwt_write32bitreg(R.SYS_STATUS_ID, reg)  # clear interrupt/events

    asleep = False
    try:
        while True:
            dwt.dwt_writetxdata(len(tx_msg), bytes(tx_msg), 0)
            dwt.dwt_writetxfctrl(len(tx_msg), 0, 0)
            dwt.dwt_starttx(R.DWT_START_TX_IMMEDIATE)
            asleep = True   # from here the chip sleeps on its own after the frame

            print("TX started seq %d; chip will sleep itself, waiting %d ms" % (tx_msg[BLINK_FRAME_SN_IDX], TX_DELAY_MS))

            # The chip cannot be polled; just wait.
            time.sleep_ms(TX_DELAY_MS)

            t0 = time.ticks_us()
            dwt.dwt_wakeup_ic()
            time.sleep_ms(2)  # INIT_RC -> IDLE_RC
            while not dwt.dwt_checkidlerc():
                pass
            t_idle = time.ticks_diff(time.ticks_us(), t0)
            dwt.dwt_restoreconfig()
            asleep = False
            print("   woke: IDLE_RC after %d us" % t_idle)

            tx_msg[BLINK_FRAME_SN_IDX] = (tx_msg[BLINK_FRAME_SN_IDX] + 1) & 0xFF
    except KeyboardInterrupt:
        if asleep:
            dwt.dwt_wakeup_ic()
            time.sleep_ms(2)
            while not dwt.dwt_checkidlerc():
                pass
        dwt.dwt_forcetrxoff()
        print("\nStopped by user (Ctrl-C). Transceiver forced off.")


if __name__ == "__main__":
    main()
