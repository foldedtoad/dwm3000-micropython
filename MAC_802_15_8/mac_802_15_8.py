"""
MAC_802_15_8/mac_802_15_8.py

Port of upstream MAC_802_15_8/mac_802_15_8.c/.h: the 802.15.8 frame header used
by the AES examples (ex_01i / ex_02i) and the helper that decrypts a received
frame.

The frame is: header (21 bytes: 2 frame control, 1 sequence number, 6 destination
address, 6 source address, 6 nonce/packet number PN) | encrypted payload |
MIC (tag) | FCS. The header travels in the clear but is authenticated.
"""

from decadriver import deca_device as dwt
from decadriver import deca_regs as R
from shared_data.shared_defines import (AES_RES_OK, AES_RES_ERROR_LENGTH, AES_RES_ERROR,
                                        AES_RES_ERROR_FRAME)

# Layout of mac_frame_802_15_8_format_t
HEADER_LEN = 21
FC_IDX = 0
SEQ_IDX = 2
DST_ADDR_IDX = 3
SRC_ADDR_IDX = 9
NONCE_IDX = 15


def make_header(fc, seq, dst_addr, src_addr, nonce=b"\x00" * 6):
    """Build a 21-byte 802.15.8 header (bytearray)."""
    h = bytearray(HEADER_LEN)
    h[0], h[1] = fc[0], fc[1]
    h[SEQ_IDX] = seq
    h[DST_ADDR_IDX:DST_ADDR_IDX + 6] = bytes(dst_addr)
    h[SRC_ADDR_IDX:SRC_ADDR_IDX + 6] = bytes(src_addr)
    h[NONCE_IDX:NONCE_IDX + 6] = bytes(nonce)
    return h


def rx_aes_802_15_8(frame_length, aes_job, payload, core_type):
    """
    Decrypt the frame just received (it is in the RX buffer), as sent by the
    ex_01i transmitter. The AES key must be set and the block configured first.
    `payload` is a bytearray that receives the decrypted payload (its size is the
    receive buffer size); the payload length is derived from the frame length.

    Returns an AES_RES_* code (AES_RES_OK = 0, errors negative).
    """
    payload_len = frame_length - (HEADER_LEN + aes_job.mic_size + R.FCS_LEN)

    if 0 <= payload_len < len(payload):
        # Download the plain-text header (it holds the nonce part PN and the
        # sender address).
        header = dwt.dwt_readrxdata(HEADER_LEN, 0)

        # 802.15.8: the 96-bit nonce is the 6 bytes of PN followed by the 6
        # bytes of the sender address.
        nonce = bytes(header[NONCE_IDX:NONCE_IDX + 6]) + bytes(header[SRC_ADDR_IDX:SRC_ADDR_IDX + 6])

        aes_job.nonce = nonce
        aes_job.header_len = HEADER_LEN
        aes_job.payload_len = payload_len
        aes_job.header = None
        aes_job.payload = payload
        status = dwt.dwt_do_aes(aes_job, core_type)    # afterwards payload holds the received data

        # "status" is the last read of AES_STS (see the DW3000 user manual).
        if status < 0:                  # problem with header/payload length or mode
            return AES_RES_ERROR_LENGTH
        if status & R.AES_ERRORS:       # one of the error status bits was raised
            return AES_RES_ERROR
        return AES_RES_OK
    return AES_RES_ERROR_FRAME
