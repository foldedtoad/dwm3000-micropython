"""
shared_data/shared_functions.py

Port of the ranging-related helpers in upstream shared_data/shared_functions.c:
get_tx_timestamp_u64, get_rx_timestamp_u64, and the resp/final message
timestamp pack/unpack functions. (check_for_status_errors and the
get_rx_delay_time_* helpers are not needed yet and are not ported.)

Timestamps are 40-bit values. In the message timestamp fields the least
significant byte is at the lowest address, and only the low 32 bits are sent.
"""

from decadriver import deca_device as dwt
from shared_data.shared_defines import RESP_MSG_TS_LEN, FINAL_MSG_TS_LEN


def get_tx_timestamp_u64() -> int:
    """TX timestamp as an integer (assumes 40-bit timestamps)."""
    return int.from_bytes(dwt.dwt_readtxtimestamp(), "little")


def get_rx_timestamp_u64() -> int:
    """RX timestamp as an integer (assumes 40-bit timestamps)."""
    return int.from_bytes(dwt.dwt_readrxtimestamp(), "little")


def resp_msg_get_ts(buf, offset: int) -> int:
    """Read a 32-bit timestamp from a response message field (LSB first)."""
    return int.from_bytes(bytes(buf[offset:offset + RESP_MSG_TS_LEN]), "little")


def resp_msg_set_ts(buf, offset: int, ts: int):
    """Write the low 32 bits of `ts` into a response message field (LSB first)."""
    for i in range(RESP_MSG_TS_LEN):
        buf[offset + i] = (ts >> (i * 8)) & 0xFF


def final_msg_get_ts(buf, offset: int) -> int:
    """Read a 32-bit timestamp from a final message field (LSB first)."""
    return int.from_bytes(bytes(buf[offset:offset + FINAL_MSG_TS_LEN]), "little")


def final_msg_set_ts(buf, offset: int, ts: int):
    """Write the low 32 bits of `ts` into a final message field (LSB first)."""
    for i in range(FINAL_MSG_TS_LEN):
        buf[offset + i] = (ts >> (i * 8)) & 0xFF
