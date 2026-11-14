"""
examples/ex_02i_simple_rx_aes/main.py

MicroPython port of the upstream ex_02i_simple_rx_aes example
(simple_rx_aes.c): receive an AES-GCM encrypted and authenticated 802.15.8 frame
from ex_01i_simple_tx_aes and decrypt it in the DW3000's AES engine. The tag is
verified by the chip: a frame with a wrong key, a modified header or modified
payload fails with an AES error.

Differences from upstream:
  * The decrypted payload is printed (upstream only counts successful frames).
  * Upstream leaves its main loop on the first AES error. Here the error is
    printed and reception continues, which is more useful when experimenting
    (e.g. giving the two boards different keys to see the authentication fail).
"""

import utime as time

from platform.port import port
from decadriver import deca_device as dwt
from decadriver import deca_regs as R
from decadriver.deca_types import DwtConfig, DwtAesKey, DwtAesConfig, DwtAesJob
from MAC_802_15_8.mac_802_15_8 import rx_aes_802_15_8
from shared_data.shared_defines import (AES_RES_OK, AES_RES_ERROR_LENGTH, AES_RES_ERROR,
                                        AES_RES_ERROR_FRAME, AES_RES_ERROR_IGNORE_FRAME)

APP_NAME = "AES RX (MicroPython) v0.1"

# 128-bit key (must match the transmitter).
aes_key = DwtAesKey(0x41424344, 0x45464748, 0x49505152, 0x53545556,
                    0x00000000, 0x00000000, 0x00000000, 0x00000000)

aes_config = DwtAesConfig(
    key_load=R.AES_KEY_Load,
    key_size=R.AES_KEY_128bit,
    key_src=R.AES_KEY_Src_Register,
    mic=R.MIC_16,                       # 16-byte tag
    mode=R.AES_Decrypt,
    aes_core_type=R.AES_core_type_GCM,
    aes_key_otp_type=R.AES_key_RAM,
    key_addr=0,
)

# Communication configuration -- must match the transmitter.
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

PAYLOAD_BUF_LEN = 128

_AES_ERRORS = {
    AES_RES_ERROR_LENGTH: "Length AES error",
    AES_RES_ERROR: "ERROR AES (authentication/transfer error: wrong key, or frame modified)",
    AES_RES_ERROR_FRAME: "Error Frame (not the expected length)",
}


def main():
    print(APP_NAME)

    mic_size = 0 if aes_config.mic == R.MIC_0 else aes_config.mic * 2 + 2

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

    dwt.dwt_set_keyreg_128(aes_key)
    dwt.dwt_configure_aes(aes_config)

    # Take the encrypted frame from RX buffer 0 and decrypt it in place (this
    # destroys the original frame in the buffer).
    aes_job = DwtAesJob(src_port=R.AES_Src_Rx_buf_0, dst_port=R.AES_Dst_Rx_buf_0,
                        mode=aes_config.mode, mic_size=mic_size)
    payload = bytearray(PAYLOAD_BUF_LEN)

    good = 0
    bad = 0
    try:
        while True:
            dwt.dwt_rxenable(R.DWT_START_RX_IMMEDIATE)

            # Poll until a frame is properly received or an error occurs.
            while True:
                status_reg = dwt.dwt_read32bitreg(R.SYS_STATUS_ID)
                if status_reg & (R.SYS_STATUS_RXFCG_BIT_MASK | R.SYS_STATUS_ALL_RX_ERR):
                    break

            if status_reg & R.SYS_STATUS_RXFCG_BIT_MASK:
                finfo16 = dwt.dwt_read16bitoffsetreg(R.RX_FINFO_ID, 0)
                frame_len = finfo16 & R.RX_FINFO_RXFLEN_BIT_MASK

                # Decrypt the received packet.
                result = rx_aes_802_15_8(frame_len, aes_job, payload, aes_config.aes_core_type)
                if result == AES_RES_OK:
                    good += 1
                    n = aes_job.payload_len
                    print("AES RX OK %d: %s" % (good - 1, bytes(payload[:n])))
                elif result != AES_RES_ERROR_IGNORE_FRAME:
                    bad += 1
                    print("%s (len %d; %d good, %d bad)" % (_AES_ERRORS.get(result, "AES error %d" % result),
                                                            frame_len, good, bad))

                # Clear the good-RX-frame event.
                dwt.dwt_write32bitreg(R.SYS_STATUS_ID, R.SYS_STATUS_RXFCG_BIT_MASK)
            else:
                # Clear RX error events.
                dwt.dwt_write32bitreg(R.SYS_STATUS_ID, R.SYS_STATUS_ALL_RX_ERR)
    except KeyboardInterrupt:
        dwt.dwt_forcetrxoff()
        print("\nStopped by user (Ctrl-C). Transceiver forced off.")


if __name__ == "__main__":
    main()
