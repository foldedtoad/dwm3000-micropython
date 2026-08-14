"""
decadriver/deca_regs.py

DW3000 register file IDs. Only the registers actually used by the
examples ported so far are defined here -- add more as later examples
are ported (cross-reference decadriver/deca_regs.h in the upstream
project rather than guessing).

Register addressing on the DW3000 is (register_file, offset_within_file),
both encoded into a combined 24-bit "regFileID" value in the upstream
driver as regFileID = (file << 16) | offset. We keep that convention
here so the SPI-header math in deca_device.py matches the source
project's dwt_xfer3000().
"""

def _reg(file_id: int, offset: int = 0) -> int:
    return (file_id << 16) | offset


# General configuration file (file 0x00). DEV_ID is the very first
# register: file 0, offset 0, 4 bytes. Reset value for DW3000/DW3110
# family parts is 0xDECA0302 (byte order handled in deca_device.py).
GEN_CFG_FILE_ID = 0x00
DEV_ID_ID = _reg(GEN_CFG_FILE_ID, 0x00)
DEV_ID_LEN = 4

EXPECTED_DEV_ID = 0xDECA0302
