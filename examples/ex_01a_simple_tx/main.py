"""
examples/ex_01a_simple_tx/main.py

MicroPython port of the upstream ex_01a_simple_tx example. Faithfully
transliterated from simple_tx.c (see decadriver/deca_device.py's module
docstring for what's been verified against the real source).

Sends an 802.15.4e blink frame (0xC5, seq#, "DECAWAVE") once per
TX_DELAY_MS, polling SYS_STATUS for TXFRS (frame sent) rather than using
interrupts, exactly like the upstream example.
"""

import utime as time

from platform.port import port
from decadriver import deca_device as dwt
from decadriver import deca_regs as R
from decadriver.deca_types import DwtConfig
from config_options import txconfig_options

APP_NAME = "SIMPLE TX (MicroPython) v0.1"

# Default communication configuration -- non-STS DW mode, channel 5.
# Field values match the upstream example's `config` struct exactly.
config = DwtConfig(
    chan=5,
    txPreambLength=R.DWT_PLEN_128,
    rxPAC=R.DWT_PAC8,
    txCode=9,
    rxCode=9,
    sfdType=R.DWT_SFD_DW_8,
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

FRAME_LENGTH = len(tx_msg) + R.FCS_LEN  # actual length transmitted (CRC appended by IC)

TX_DELAY_MS = 500


def main():
    print(APP_NAME)

    # DW3000 supports up to 38 MHz SPI once initialised/configured.
    port.spi_set_rate_high()

    port.reset_dwic()

    time.sleep_ms(2)  # let DW3000 transition INIT_RC -> IDLE_RC

    while not dwt.dwt_checkidlerc():
        pass  # spin until IDLE_RC

    if dwt.dwt_initialise(R.DWT_DW_INIT) == R.DWT_ERROR:
        print("INIT FAILED")
        while True:
            pass

    # Enable LEDs (D1 LED flashes per TX on DW3000 red eval-shield boards).
    dwt.dwt_setleds(R.DWT_LEDS_ENABLE | R.DWT_LEDS_INIT_BLINK)

    if dwt.dwt_configure(config):
        print("CONFIG FAILED")
        while True:
            pass

    dwt.dwt_configuretxrf(txconfig_options)

    print("Sending started")

    try:
        while True:
            print("len %d:" % (FRAME_LENGTH - R.FCS_LEN), bytes(tx_msg))

            dwt.dwt_writetxdata(FRAME_LENGTH - R.FCS_LEN, bytes(tx_msg), 0)
            dwt.dwt_writetxfctrl(FRAME_LENGTH, 0, 0)
            dwt.dwt_starttx(R.DWT_START_TX_IMMEDIATE)

            while not (dwt.dwt_read32bitreg(R.SYS_STATUS_ID) & R.SYS_STATUS_TXFRS_BIT_MASK):
                pass  # spin until TX frame sent

            dwt.dwt_write32bitreg(R.SYS_STATUS_ID, R.SYS_STATUS_TXFRS_BIT_MASK)  # clear event

            time.sleep_ms(TX_DELAY_MS)

            tx_msg[BLINK_FRAME_SN_IDX] = (tx_msg[BLINK_FRAME_SN_IDX] + 1) & 0xFF
    except KeyboardInterrupt:
        # Put the transceiver into a known-off state rather than leaving it
        # wherever it happened to be mid-cycle (e.g. between starttx and the
        # TXFRS poll). Not part of the upstream C example -- there Ctrl-C
        # doesn't apply -- but appropriate for an interactive MicroPython
        # REPL session.
        dwt.dwt_forcetrxoff()
        print("\nStopped by user (Ctrl-C). Transceiver forced off.")


if __name__ == "__main__":
    main()
