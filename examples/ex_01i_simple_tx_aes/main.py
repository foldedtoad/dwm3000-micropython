"""
examples/ex_01i_simple_tx_aes/main.py

MicroPython port of the upstream ex_01i_simple_tx_aes example
(simple_tx_aes.c): transmit a frame whose payload is ENCRYPTED AND AUTHENTICATED
by the DW3000's built-in AES-GCM engine (16-byte tag, 128-bit key), in the
IEEE 802.15.8 frame format with 48-bit addresses. The header stays in clear text
(it is authenticated, not encrypted). Pair with ex_02i_simple_rx_aes, which has
the same key and decrypts.

The nonce is the 6-byte packet number PN (incremented for every frame, and
carried in the header) followed by the sender's 6-byte address. A nonce must
never repeat with the same key; a real device would keep PN in non-volatile
storage.

Frame on air (71 bytes): header 21 | encrypted payload 32 | MIC 16 | FCS 2.

The AES key below is the example key from upstream, for demonstration only.
"""

import utime as time

from platform.port import port
from decadriver import deca_device as dwt
from decadriver import deca_regs as R
from decadriver.deca_types import DwtConfig, DwtAesKey, DwtAesConfig, DwtAesJob
from config_options import txconfig_options
from MAC_802_15_8.mac_802_15_8 import make_header, SEQ_IDX, NONCE_IDX, SRC_ADDR_IDX, HEADER_LEN

APP_NAME = "AES TX (MicroPython) v0.1"

# 128-bit key (must match the receiver).
aes_key = DwtAesKey(0x41424344, 0x45464748, 0x49505152, 0x53545556,
                    0x00000000, 0x00000000, 0x00000000, 0x00000000)

aes_config = DwtAesConfig(
    key_load=R.AES_KEY_Load,
    key_size=R.AES_KEY_128bit,
    key_src=R.AES_KEY_Src_Register,
    mic=R.MIC_16,                       # 16-byte tag
    mode=R.AES_Encrypt,
    aes_core_type=R.AES_core_type_GCM,
    aes_key_otp_type=R.AES_key_RAM,
    key_addr=0,
)

# The payload to send encrypted (the trailing NUL is part of it, as in upstream's C string).
payload = bytearray(b"Good, This is the right message\x00")

# Communication configuration: non-STS, extended PHR not used.
config = DwtConfig(
    chan=5,
    txPreambLength=R.DWT_PLEN_128,
    rxPAC=R.DWT_PAC8,
    txCode=9,
    rxCode=9,
    sfdType=1,                 # non-standard 8 symbol SFD
    dataRate=R.DWT_BR_6M8,
    phrMode=R.DWT_PHRMODE_STD,
    phrRate=R.DWT_PHRRATE_STD,
    sfdTO=(129 + 8 - 8),
    stsMode=R.DWT_STS_MODE_OFF,
    stsLength=R.DWT_STS_LEN_64,
    pdoaMode=R.DWT_PDOA_M0,
)

# Inter-frame delay, milliseconds.
TX_DELAY_MS = 500


def main():
    print(APP_NAME)

    # 802.15.8 header: DATA frame, 48-bit source and destination addresses
    # (fc 0x50), not requesting ACK, frame encrypted (fc 0x40).
    header = make_header(fc=(0x50, 0x40), seq=0,
                         dst_addr=(0xA, 0xB, 0xC, 0xD, 0xE, 0xF),    # receiver 0x0F0E0D0C0B0A
                         src_addr=(0x1, 0x2, 0x3, 0x4, 0x5, 0x6))    # this device 0x060504030201

    mic_size = 0 if aes_config.mic == R.MIC_0 else aes_config.mic * 2 + 2     # 16 bytes for MIC_16

    port.spi_set_rate_high()
    port.reset_dwic()
    time.sleep_ms(2)  # INIT_RC -> IDLE_RC

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

    dwt.dwt_set_keyreg_128(aes_key)
    dwt.dwt_configure_aes(aes_config)

    # The job: encrypt the payload in the TX buffer, header left in clear.
    nonce = bytearray(12)
    nonce[6:12] = header[SRC_ADDR_IDX:SRC_ADDR_IDX + 6]
    job = DwtAesJob(nonce=nonce, header=header, payload=payload,
                    header_len=HEADER_LEN, payload_len=len(payload),
                    src_port=R.AES_Src_Tx_buf,       # plain text goes to the TX buffer
                    dst_port=R.AES_Dst_Tx_buf,       # and is replaced there by the encrypted frame
                    mode=aes_config.mode, mic_size=mic_size)

    # Frame length: header + payload + MIC + FCS.
    dwt.dwt_writetxfctrl(HEADER_LEN + len(payload) + mic_size + R.FCS_LEN, 0, 0)

    pn = 0            # packet number; may start at a random value, at most 6 bytes
    count = 0
    try:
        while True:
            # PN goes both in the nonce and into the header, low 6 bytes, LSB first.
            pn_bytes = (pn & 0xFFFFFFFFFFFF).to_bytes(6, "little")
            nonce[0:6] = pn_bytes
            header[NONCE_IDX:NONCE_IDX + 6] = pn_bytes

            status = dwt.dwt_do_aes(job, aes_config.aes_core_type)
            if status < 0:
                print("Length AES error (%d)" % status)       # header/payload length or mode problem
                break
            if status & R.AES_ERRORS:
                print("ERROR AES (status 0x%02X)" % status)
                break

            dwt.dwt_starttx(R.DWT_START_TX_IMMEDIATE)

            while not (dwt.dwt_read32bitreg(R.SYS_STATUS_ID) & R.SYS_STATUS_TXFRS_BIT_MASK):
                pass  # spin until TX frame sent
            dwt.dwt_write32bitreg(R.SYS_STATUS_ID, R.SYS_STATUS_TXFRS_BIT_MASK)

            pn = (pn + 1) % 0xFFFFFFFFFFFF
            header[SEQ_IDX] = (header[SEQ_IDX] + 1) & 0xFF

            print("AES TX OK: %d" % count)
            count += 1

            time.sleep_ms(TX_DELAY_MS)
    except KeyboardInterrupt:
        dwt.dwt_forcetrxoff()
        print("\nStopped by user (Ctrl-C). Transceiver forced off.")


if __name__ == "__main__":
    main()
