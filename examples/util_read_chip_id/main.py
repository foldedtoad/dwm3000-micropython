"""
examples/util_read_chip_id/main.py

Not one of foldedtoad/dwm3000's numbered examples (there isn't an
upstream one for this) -- a small standalone utility for reading the
DW3000's factory-unique Part ID and Lot ID, useful for telling boards
apart once you have more than one.

An earlier version of this utility read EUI_64 instead. That register
is plain writable RAM-style storage on the DW3000, not OTP/fuse-backed,
so it reads back all-zero on an unprogrammed module -- it is NOT a
factory-unique identifier unless something has explicitly called
dwt_seteui() to set one. Part ID and Lot ID, read from OTP below, are
the real per-die factory-unique values.

Only needs reset + OTP reads -- no dwt_initialise()/dwt_configure()
required (dwt_initialise() itself reads these same OTP locations as one
of its first steps, before doing anything else).
"""

from platform.port import port
from decadriver.deca_device import dwt_getpartid, dwt_getlotid

APP_NAME = "READING PART ID / LOT ID (MicroPython) v0.1"


def main():
    print(APP_NAME)

    port.reset_dwic()
    print("reset_DWIC done")

    part_id = dwt_getpartid()
    lot_id = dwt_getlotid()

    print("PART ID (OTP, factory-unique): 0x%08X" % part_id)
    print("LOT ID  (OTP, factory-unique): 0x%08X" % lot_id)

    if part_id == 0 and lot_id == 0:
        print(
            "Both read as zero -- unlike EUI_64, Part ID/Lot ID are fuse "
            "OTP values on real DW3000 silicon, so all-zero here would be "
            "unexpected on real hardware (as opposed to an unprogrammed "
            "register). Worth double-checking the SPI/reset wiring if you "
            "see this."
        )


if __name__ == "__main__":
    main()
