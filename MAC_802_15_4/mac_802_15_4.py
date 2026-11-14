"""
MAC_802_15_4/mac_802_15_4.py

Port of upstream MAC_802_15_4/mac_802_15_4.c/.h: the IEEE 802.15.4 data frame
header used by the AES single-sided ranging examples (ex_06e / ex_06f), and the
helper that decrypts a received frame.

The header (MHR) is 27 bytes: frame control (2), sequence number (1),
destination PAN ID (2), destination address (8), source address (8) and the
auxiliary security header (security control 1, frame counter 4, key index 1).
It travels in clear text but is authenticated by the AES engine (CCM*).
"""

from decadriver import deca_device as dwt
from decadriver import deca_regs as R
from shared_data.shared_defines import (AES_RES_OK, AES_RES_ERROR_LENGTH, AES_RES_ERROR,
                                        AES_RES_ERROR_FRAME, AES_RES_ERROR_IGNORE_FRAME)

NUM_OF_KEY_OPTIONS = 3
HEADER_LEN = 27

# Byte offsets in the header
FC_IDX = 0
SEQ_IDX = 2
DEST_PAN_IDX = 3
DEST_ADDR_IDX = 5
SRC_ADDR_IDX = 13
SEC_CTRL_IDX = 21
FRAME_CNT_IDX = 22
KEY_ID_IDX = 26

FRAME_CNT_SIZE = 4
SECURITY_ENABLE_BIT_MASK = 0x8
MIC_ERROR = R.MIC_ERROR

# Security level (security control bits 0-2) 7 = encryption on, 16-byte MIC;
# key identifier mode 1 (bits 3-4) = key index; frame counter present (bit 5 = 0);
# frame counter generates the nonce (bit 6 = 0)  ->  0x0F
SECURITY_CTRL_DEFAULT = (7 << 0) | (1 << 3)


class MacFrame:
    """The MAC frame: `header` (bytearray of HEADER_LEN) and the payload buffer."""

    def __init__(self, header=None):
        self.header = bytearray(header) if header is not None else bytearray(HEADER_LEN)
        self.payload = None


def new_initiator_frame():
    """Header as in ssaes initiator: data frame, security enabled, seq number,
    64-bit addresses, frame version 2 (fc 0x09 0xEC), PAN 0x4321, security control 0x0F."""
    f = MacFrame()
    f.header[0] = 0x09
    f.header[1] = 0xEC
    f.header[DEST_PAN_IDX] = 0x21
    f.header[DEST_PAN_IDX + 1] = 0x43
    f.header[SEC_CTRL_IDX] = SECURITY_CTRL_DEFAULT
    return f


def set_pan_ids_and_addresses(frame, dest_pan_id, dest_addr, src_addr):
    """Set destination PAN ID and the 64-bit destination and source addresses (LSB first)."""
    h = frame.header
    h[SRC_ADDR_IDX:SRC_ADDR_IDX + 8] = (src_addr & 0xFFFFFFFFFFFFFFFF).to_bytes(8, "little")
    h[DEST_ADDR_IDX:DEST_ADDR_IDX + 8] = (dest_addr & 0xFFFFFFFFFFFFFFFF).to_bytes(8, "little")
    h[DEST_PAN_IDX] = dest_pan_id & 0xFF
    h[DEST_PAN_IDX + 1] = (dest_pan_id >> 8) & 0xFF


def update_sequence_number(frame, seq):
    frame.header[SEQ_IDX] = seq & 0xFF


def update_aux_frame_cnt(frame, cnt):
    frame.header[FRAME_CNT_IDX:FRAME_CNT_IDX + FRAME_CNT_SIZE] = (cnt & 0xFFFFFFFF).to_bytes(4, "little")


def get_aux_frame_cnt(frame):
    return int.from_bytes(bytes(frame.header[FRAME_CNT_IDX:FRAME_CNT_IDX + FRAME_CNT_SIZE]), "little")


def set_aux_key_identifier(frame, key_index):
    frame.header[KEY_ID_IDX] = key_index


def get_aux_key_identifier(frame):
    return frame.header[KEY_ID_IDX]


def get_nonce(frame):
    """13-byte CCM* nonce: source address (8), frame counter (4), security level (1)."""
    h = frame.header
    return (bytes(h[SRC_ADDR_IDX:SRC_ADDR_IDX + 8]) + bytes(h[FRAME_CNT_IDX:FRAME_CNT_IDX + 4]) +
            bytes([h[SEC_CTRL_IDX] & 0x7]))


def get_aux_mic_size(frame):
    """MIC length in bytes from security control bits 0-2 (MIC_ERROR for the reserved value 4)."""
    value = frame.header[SEC_CTRL_IDX] & 0x7
    if value == 0:
        return 0
    if value in (1, 5):
        return 4
    if value in (2, 6):
        return 8
    if value in (3, 7):
        return 16
    return MIC_ERROR


def get_security_state(frame):
    """True when the frame is protected (security enabled bit in frame control)."""
    return bool(frame.header[0] & SECURITY_ENABLE_BIT_MASK)


def get_src_and_dst_frame_addr(frame):
    h = frame.header
    src = int.from_bytes(bytes(h[SRC_ADDR_IDX:SRC_ADDR_IDX + 8]), "little")
    dst = int.from_bytes(bytes(h[DEST_ADDR_IDX:DEST_ADDR_IDX + 8]), "little")
    return src, dst


def rx_aes_802_15_4(frame, frame_length, aes_job, max_payload, aes_keys, exp_src_addr, exp_dst_addr, aes_config):
    """
    Decrypt the frame just received (it is in the RX buffer): read the header into
    `frame`, check security is on and the addresses match, then decrypt with the key
    selected by the key index in the header. The plain payload ends up in
    aes_job.payload (a bytearray of at least max_payload). Returns an AES_RES_* code.
    """
    if (frame_length - R.FCS_LEN) < aes_job.header_len:
        return AES_RES_ERROR_FRAME

    # Unencrypted header
    frame.header[:aes_job.header_len] = dwt.dwt_readrxdata(aes_job.header_len, 0)

    src_addr, dst_addr = get_src_and_dst_frame_addr(frame)
    if (not get_security_state(frame)) or exp_src_addr != src_addr or exp_dst_addr != dst_addr:
        return AES_RES_ERROR_IGNORE_FRAME      # not for us

    aes_job.mic_size = get_aux_mic_size(frame)
    if aes_job.mic_size == MIC_ERROR:
        return AES_RES_ERROR_FRAME

    payload_len = frame_length - (aes_job.header_len + aes_job.mic_size + R.FCS_LEN)
    if payload_len < 0 or payload_len > max_payload:
        return AES_RES_ERROR_FRAME

    aes_job.nonce = get_nonce(frame)
    aes_job.payload_len = payload_len
    aes_job.header = None                       # not needed for decryption
    aes_config.mic = dwt.dwt_mic_size_from_bytes(aes_job.mic_size)
    dwt.dwt_configure_aes(aes_config)

    key_index = frame.header[KEY_ID_IDX]
    if key_index < 1 or key_index > len(aes_keys):
        return AES_RES_ERROR_FRAME              # (upstream would index outside its key table)
    dwt.dwt_set_keyreg_128(aes_keys[key_index - 1])

    status = dwt.dwt_do_aes(aes_job, aes_config.aes_core_type)
    if status < 0:
        return AES_RES_ERROR_LENGTH
    if status & R.AES_ERRORS:
        return AES_RES_ERROR
    return AES_RES_OK
