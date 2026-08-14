"""
examples/ex_00a_reading_dev_id/main.py

MicroPython port of the upstream ex_00a_reading_dev_id example.
Simplest possible example: reset the DW3000, read back DEV_ID over SPI,
and check it against the expected value. This exercises exactly the
platform layer (reset, SPI) plus the single register read implemented
so far in decadriver -- deliberately nothing else, so it's a clean test
of your wiring/pin mapping (see docs/pinout.md) before building anything
on top.

Copy this whole project onto the Pico's filesystem (e.g. with `mpremote
cp` or Thonny) preserving the directory structure, then run this file
(or rename it to boot.py/main.py at the filesystem root).
"""

import utime as time

from platform.port import port
from decadriver.deca_device import dwt_readdevid
from decadriver.deca_regs import EXPECTED_DEV_ID

APP_NAME = "READING DEV ID (MicroPython) v0.1"


def main():
    print(APP_NAME)

    port.reset_dwic()
    print("reset_DWIC done")

    # dwt_initialise() has not been ported yet, so we skip straight to a
    # raw register read -- DEV_ID is readable immediately after reset,
    # before any configuration, which is exactly why it's example #1.
    dev_id = dwt_readdevid()
    print("DEV_ID: 0x%08x" % dev_id)

    if dev_id == EXPECTED_DEV_ID:
        print("OK: matches expected DW3000/DW3110 DEV_ID (0x%08x)" % EXPECTED_DEV_ID)
    elif dev_id in (0x00000000, 0xFFFFFFFF):
        print(
            "FAIL: read all-zero/all-one -- almost certainly a wiring "
            "problem (SPI MOSI/MISO/SCK/CS). See docs/pinout.md."
        )
    else:
        print(
            "WARN: got a nonzero-but-unexpected DEV_ID (0x%08x). Could be "
            "a different DW3000-family variant, or RESET not wired "
            "correctly. See docs/pinout.md." % dev_id
        )


if __name__ == "__main__":
    main()
