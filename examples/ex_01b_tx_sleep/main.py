"""
examples/ex_01b_tx_sleep/main.py

MicroPython port of the upstream ex_01b_tx_sleep example (tx_sleep.c and its
variant tx_sleep_idleRC.c): send a blink frame, put the DW3000 to sleep for
TX_DELAY_MS, wake it up, restore the configuration that sleep does not keep,
and send the next one.

Set USE_IDLE_RC = True for the tx_sleep_idleRC.c variant: the chip then stays
in IDLE_RC after waking (the auto INIT2IDLE bit is cleared), and the PLL is
started and calibrated again by the next transmission, which is slower to start
but uses less power while idle. Upstream uses a 1000 ms delay for the default
and 100 ms for the IDLE_RC variant; set below.

Waking: upstream pulses the WAKEUP pin for 500 us. Flexypin has that pin wired
(GP3). ArduPico's WAKEUP pin is unknown (see platform/port.py), so there
port.wakeup_pulse() holds CS low for 500 us instead; the chip honours that
because this example enables DWT_WAKE_CSN as well as DWT_WAKE_WUP.

To see it working, run ex_02a_simple_rx on the second board: the sequence
number it receives must advance by one per frame. The example prints how long
the wake-up took (pulse to IDLE_RC).

Not in upstream: Ctrl-C while the chip sleeps wakes it first so the final
dwt_forcetrxoff() has a chip to talk to.
"""

import utime as time

from platform.port import port
from decadriver import deca_device as dwt
from decadriver import deca_regs as R
from decadriver.deca_types import DwtConfig
from config_options import txconfig_options

APP_NAME = "TX SLEEP (MicroPython) v0.1"

USE_IDLE_RC = False

# Inter-frame delay in milliseconds (upstream: 1000, or 100 for the IDLE_RC variant).
TX_DELAY_MS = 100 if USE_IDLE_RC else 1000

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


def _wake():
    """Wake the chip and return once it is in IDLE_RC with its config restored."""
    t0 = time.ticks_us()
    dwt.dwt_wakeup_ic()
    time.sleep_ms(2)  # INIT_RC -> IDLE_RC
    while not dwt.dwt_checkidlerc():
        pass
    t_idle = time.ticks_diff(time.ticks_us(), t0)
    dwt.dwt_restoreconfig()
    return t_idle


def main():
    print(APP_NAME, "(IDLE_RC variant)" if USE_IDLE_RC else "")

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

    dwt.dwt_setlnapamode(R.DWT_LNA_ENABLE | R.DWT_PA_ENABLE)

    # Sleep and wake-up parameters: restore the AON configuration on wake, wake on
    # the WAKEUP pin or on chip select, keep SLEEP_EN set.
    dwt.dwt_configuresleep(R.DWT_CONFIG, R.DWT_PRES_SLEEP | R.DWT_WAKE_CSN | R.DWT_WAKE_WUP | R.DWT_SLP_EN)

    asleep = False
    try:
        while True:
            dwt.dwt_writetxdata(FRAME_LENGTH - R.FCS_LEN, bytes(tx_msg), 0)
            dwt.dwt_writetxfctrl(FRAME_LENGTH, 0, 0)
            dwt.dwt_starttx(R.DWT_START_TX_IMMEDIATE)

            while not (dwt.dwt_read32bitreg(R.SYS_STATUS_ID) & R.SYS_STATUS_TXFRS_BIT_MASK):
                pass  # spin until TX frame sent
            dwt.dwt_write32bitreg(R.SYS_STATUS_ID, R.SYS_STATUS_TXFRS_BIT_MASK)

            print("sent seq %d, sleeping %d ms" % (tx_msg[BLINK_FRAME_SN_IDX], TX_DELAY_MS))

            dwt.dwt_entersleep(R.DWT_DW_IDLE_RC if USE_IDLE_RC else R.DWT_DW_IDLE)
            asleep = True

            time.sleep_ms(TX_DELAY_MS)

            t_idle = _wake()
            asleep = False
            print("   woke: IDLE_RC after %d us" % t_idle)

            tx_msg[BLINK_FRAME_SN_IDX] = (tx_msg[BLINK_FRAME_SN_IDX] + 1) & 0xFF
    except KeyboardInterrupt:
        if asleep:
            _wake()
        dwt.dwt_forcetrxoff()
        print("\nStopped by user (Ctrl-C). Transceiver forced off.")


if __name__ == "__main__":
    main()
