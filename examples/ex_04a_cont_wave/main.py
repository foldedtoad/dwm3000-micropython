"""
examples/ex_04a_cont_wave/main.py

MicroPython port of upstream ex_04a_cont_wave (continuous_wave.c).

Transmits an unmodulated carrier on channel 5 (6489.6 MHz) for
CONT_WAVE_DURATION_MS, then soft-resets the chip to leave CW mode.
Intended for spectrum-analyser / regulatory testing; there is no visible
output except the printed messages. Ctrl-C also stops the carrier.
"""

import utime as time

from platform.port import port
from decadriver import deca_device as dwt
from decadriver import deca_regs as R
from decadriver.deca_types import DwtConfig
from config_options import txconfig_options

APP_NAME = "CONT WAVE (MicroPython) v0.1"

CONT_WAVE_DURATION_MS = 120000

config = DwtConfig(
    chan=5,
    txPreambLength=R.DWT_PLEN_1024,
    rxPAC=R.DWT_PAC32,
    txCode=9,
    rxCode=9,
    sfdType=1,
    dataRate=R.DWT_BR_850K,
    phrMode=R.DWT_PHRMODE_STD,
    phrRate=R.DWT_PHRRATE_STD,
    sfdTO=(1025 + 8 - 32),
    stsMode=R.DWT_STS_MODE_OFF,
    stsLength=R.DWT_STS_LEN_64,
    pdoaMode=R.DWT_PDOA_M0,
)


def main():
    print(APP_NAME)
    port.spi_set_rate_high()
    port.reset_dwic()
    time.sleep_ms(2)
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

    print("Continuous wave output for %d ms" % CONT_WAVE_DURATION_MS)
    dwt.dwt_configcwmode(config.chan)
    try:
        t0 = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), t0) < CONT_WAVE_DURATION_MS:
            time.sleep_ms(100)
    except KeyboardInterrupt:
        print("\nStopped by user (Ctrl-C).")
    # Soft reset leaves CW mode; chip is then on the slow clock, so use low SPI rate.
    dwt.dwt_softreset()
    port.spi_set_rate_low()
    print("done (chip reset; re-run initialise/configure for normal use)")


if __name__ == "__main__":
    main()
