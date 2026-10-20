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
