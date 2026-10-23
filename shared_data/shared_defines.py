"""
shared_data/shared_defines.py

Port of upstream shared_data/shared_defines.h. Constants shared between
examples. Populated incrementally as examples need them (currently only
what ex_02a_simple_rx uses); the ranging structures for ex_05*/ex_06*
will be added when those are ported.
"""

# Maximum frame length handled by the examples: 127 bytes is the 802.15.4
# UWB standard maximum (the DW IC's extended 1023-byte mode is not used).
FRAME_LEN_MAX = 127

# --- added for the ranging examples (ex_05*, ex_06*) -----------------------
SPEED_OF_LIGHT = 299702547      # m/s, in air

RXFLEN_MASK = 0x0000007F        # receive frame length (0 to 127)
RXFL_MASK_1023 = 0x000003FF     # receive frame length, extended mode (0 to 1023)

RESP_MSG_TS_LEN = 4
FINAL_MSG_TS_LEN = 4

# UWB microsecond (uus) to device time unit (dtu, ~15.65 ps) conversion factor:
# 1 uus = 512 / 499.2 us and 1 us = 499.2 * 128 dtu.
UUS_TO_DWT_TIME = 63898

FRAME_LEN_MAX_EX = 1023         # extended frame length mode maximum
