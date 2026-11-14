"""
examples/ex_06f_aes_ss_twr_responder/main.py

MicroPython port of upstream ex_06f_aes_ss_twr_responder (ss_aes_twr_responder.c):
single-sided two-way ranging with AES-encrypted frames, responder side. Waits for
the initiator's encrypted poll (ex_06e), decrypts it with the key named by the
key index in the header (index 1), and answers with an encrypted response sent at
a precise hardware-timed instant, carrying the poll RX and response TX timestamps
inside the encrypted payload; the response uses key index 2. Start this one first.

See ex_06e for why POLL_RX_TO_RESP_TX_DLY_UUS is 50000 instead of 2000 (first hardware run: the host needs 30-45 ms) (here the
host reads and decrypts the poll and encrypts the reply within that delay; the
host turnaround is printed so you can see the margin). Change it in BOTH files.
The received header is reused for the reply (addresses swapped, sequence number
and frame counter incremented), as upstream.
"""

import utime as time

from platform.port import port
from decadriver import deca_device as dwt
from decadriver import deca_regs as R
from decadriver.deca_types import DwtConfig, DwtAesKey, DwtAesConfig, DwtAesJob
from config_options import txconfig_options
from shared_data.shared_defines import (SPEED_OF_LIGHT, RXFLEN_MASK, UUS_TO_DWT_TIME, AES_RES_OK,
                                        AES_RES_ERROR_LENGTH, AES_RES_ERROR, AES_RES_ERROR_FRAME,
                                        AES_RES_ERROR_IGNORE_FRAME)
from shared_data.shared_functions import resp_msg_get_ts, resp_msg_set_ts, get_rx_timestamp_u64
from MAC_802_15_4 import mac_802_15_4 as mac

APP_NAME = "SS AES TWR RESP (MicroPython) v0.1"

config = DwtConfig(
    chan=5,
    txPreambLength=R.DWT_PLEN_128,
    rxPAC=R.DWT_PAC8,
    txCode=9,
    rxCode=9,
    sfdType=1,
    dataRate=R.DWT_BR_6M8,
    phrMode=R.DWT_PHRMODE_STD,
    phrRate=R.DWT_PHRRATE_STD,
    sfdTO=(129 + 8 - 8),
    stsMode=R.DWT_STS_MODE_OFF,
    stsLength=R.DWT_STS_LEN_64,
    pdoaMode=R.DWT_PDOA_M0,
)

# AES engine: CCM* core, 128-bit key held in registers (keys below are the
# upstream example keys, for demonstration only).
aes_config = DwtAesConfig(
    key_load=R.AES_KEY_Load,
    key_size=R.AES_KEY_128bit,
    key_src=R.AES_KEY_Src_Register,
    mic=R.MIC_16,
    mode=R.AES_Encrypt,
    aes_core_type=R.AES_core_type_CCM,
    aes_key_otp_type=R.AES_key_RAM,
    key_addr=0,
)

# Keys selected by the key index in the auxiliary security header (index 0 is not allowed on air).
keys_options = [
    DwtAesKey(0x00010203, 0x04050607, 0x08090A0B, 0x0C0D0E0F),
    DwtAesKey(0x11223344, 0x55667788, 0x99AABBCC, 0xDDEEFF00),
    DwtAesKey(0xFFEEDDCC, 0xBBAA9988, 0x77665544, 0x33221100),
]

TX_ANT_DLY = 16385
RX_ANT_DLY = 16385

DEST_PAN_ID = 0x4321
RX_BUF_LEN = 127
RESP_MSG_POLL_RX_TS_IDX = 0     # index in the MAC payload
RESP_MSG_RESP_TX_TS_IDX = 4
DWT_TIME_UNITS = R.DWT_TIME_UNITS

# Addresses (upstream's naming): SRC_ADDR is this responder, DEST_ADDR the initiator.
SRC_ADDR = 0x1122334455667788
DEST_ADDR = 0x8877665544332211

# MAC payloads. Response: first 8 bytes are filled with poll RX time and response TX time.
rx_poll_msg = b"Poll message"
tx_resp_msg = bytearray([0, 0, 0, 0, 0, 0, 0, 0]) + bytearray(b"Response")

RESPONDER_KEY_INDEX = 2

# Delay from poll RX to response TX, uus. Must equal the initiator's. Upstream: 2000.
POLL_RX_TO_RESP_TX_DLY_UUS = 50000


def aes_error(status):
    if status == AES_RES_ERROR_LENGTH:
        return "AES length error"
    if status == AES_RES_ERROR:
        return "ERROR AES (authentication failed?)"
    if status == AES_RES_ERROR_FRAME:
        return "Error Frame"
    return "AES error %d" % status


def main():
    print(APP_NAME)

    port.spi_set_rate_high()
    port.reset_dwic()
    time.sleep_ms(2)
    while not dwt.dwt_checkidlerc():
        pass
    if dwt.dwt_initialise(R.DWT_DW_INIT) == R.DWT_ERROR:
        print("INIT FAILED")
        while True:
            pass
    dwt.dwt_setleds(R.DWT_LEDS_ENABLE | R.DWT_LEDS_INIT_BLINK)
    if dwt.dwt_configure(config):
        print("CONFIG FAILED")
        while True:
            pass
    dwt.dwt_configuretxrf(txconfig_options)
    dwt.dwt_setrxantennadelay(RX_ANT_DLY)
    dwt.dwt_settxantennadelay(TX_ANT_DLY)
    dwt.dwt_setlnapamode(R.DWT_LNA_ENABLE | R.DWT_PA_ENABLE)

    mac_frame = mac.MacFrame()
    rx_buffer = bytearray(RX_BUF_LEN)

    aes_job_rx = DwtAesJob(mode=R.AES_Decrypt, src_port=R.AES_Src_Rx_buf_0, dst_port=R.AES_Dst_Rx_buf_0,
                           header=mac_frame.header, header_len=mac.HEADER_LEN, payload=rx_buffer)
    aes_job_tx = DwtAesJob(mode=R.AES_Encrypt, src_port=R.AES_Src_Tx_buf, dst_port=R.AES_Dst_Tx_buf,
                           header=mac_frame.header, header_len=mac.HEADER_LEN,
                           payload=tx_resp_msg, payload_len=len(tx_resp_msg))

    print("Responder ready (reply delay %d uus)" % POLL_RX_TO_RESP_TX_DLY_UUS)

    try:
        while True:
            dwt.dwt_rxenable(R.DWT_START_RX_IMMEDIATE)
            while True:
                status_reg = dwt.dwt_read32bitreg(R.SYS_STATUS_ID)
                if status_reg & (R.SYS_STATUS_RXFCG_BIT_MASK | R.SYS_STATUS_ALL_RX_ERR):
                    break

            if not (status_reg & R.SYS_STATUS_RXFCG_BIT_MASK):
                dwt.dwt_write32bitreg(R.SYS_STATUS_ID, R.SYS_STATUS_ALL_RX_ERR)
                continue

            t_rx = time.ticks_us()
            dwt.dwt_write32bitreg(R.SYS_STATUS_ID, R.SYS_STATUS_RXFCG_BIT_MASK)
            frame_len = dwt.dwt_read32bitreg(R.RX_FINFO_ID) & RXFLEN_MASK

            # Decrypt the poll: header (addresses, security, key index) then payload.
            aes_config.mode = R.AES_Decrypt
            status = mac.rx_aes_802_15_4(mac_frame, frame_len, aes_job_rx, len(rx_buffer),
                                         keys_options, DEST_ADDR, SRC_ADDR, aes_config)
            if status == AES_RES_ERROR_IGNORE_FRAME:
                continue            # wrong addresses or not secure: not for us
            if status != AES_RES_OK:
                print(aes_error(status))
                break

            t_dec = time.ticks_diff(time.ticks_us(), t_rx)
            if bytes(rx_buffer[:aes_job_rx.payload_len]) != rx_poll_msg:
                print("decrypted frame is not the expected poll")
                continue

            # Poll reception timestamp (40-bit) and response TX time (high 32 bits).
            poll_rx_ts = get_rx_timestamp_u64()
            resp_tx_time = ((poll_rx_ts + POLL_RX_TO_RESP_TX_DLY_UUS * UUS_TO_DWT_TIME) >> 8) & 0xFFFFFFFF
            dwt.dwt_setdelayedtrxtime(resp_tx_time)
            resp_tx_ts = ((resp_tx_time & 0xFFFFFFFE) << 8) + TX_ANT_DLY

            resp_msg_set_ts(tx_resp_msg, RESP_MSG_POLL_RX_TS_IDX, poll_rx_ts)
            resp_msg_set_ts(tx_resp_msg, RESP_MSG_RESP_TX_TS_IDX, resp_tx_ts)

            # Encrypt the response: our key index, next sequence number and frame counter.
            dwt.dwt_set_keyreg_128(keys_options[RESPONDER_KEY_INDEX - 1])
            mac.set_aux_key_identifier(mac_frame, RESPONDER_KEY_INDEX)
            mac.update_sequence_number(mac_frame, mac_frame.header[mac.SEQ_IDX] + 1)
            mac.update_aux_frame_cnt(mac_frame, mac.get_aux_frame_cnt(mac_frame) + 1)

            aes_job_tx.mic_size = mac.get_aux_mic_size(mac_frame)
            aes_config.mode = R.AES_Encrypt
            aes_config.mic = dwt.dwt_mic_size_from_bytes(aes_job_tx.mic_size)
            dwt.dwt_configure_aes(aes_config)

            # The received header is reused: swap to our addresses, then build the nonce.
            mac.set_pan_ids_and_addresses(mac_frame, DEST_PAN_ID, DEST_ADDR, SRC_ADDR)
            aes_job_tx.nonce = mac.get_nonce(mac_frame)

            status = dwt.dwt_do_aes(aes_job_tx, aes_config.aes_core_type)
            if status < 0:
                print("AES length error"); break
            if status & R.AES_ERRORS:
                print("ERROR AES (status 0x%02X)" % status); break

            t_enc = time.ticks_diff(time.ticks_us(), t_rx)
            dwt.dwt_writetxfctrl(aes_job_tx.header_len + aes_job_tx.payload_len + aes_job_tx.mic_size + R.FCS_LEN, 0, 1)
            ret = dwt.dwt_starttx(R.DWT_START_TX_DELAYED)
            t_queued = time.ticks_us()

            if ret == R.DWT_SUCCESS:
                while not (dwt.dwt_read32bitreg(R.SYS_STATUS_ID) & R.SYS_STATUS_TXFRS_BIT_MASK):
                    pass
                dwt.dwt_write32bitreg(R.SYS_STATUS_ID, R.SYS_STATUS_TXFRS_BIT_MASK)
                print("replied (host turnaround %d us of %d us allowed; decrypted after %d us, encrypted after %d us)" %
                      (time.ticks_diff(t_queued, t_rx), POLL_RX_TO_RESP_TX_DLY_UUS * 5120 // 4992, t_dec, t_enc))
            else:
                print("TOO LATE: host took %d us (decrypted after %d us, encrypted after %d us); raise "
                      "POLL_RX_TO_RESP_TX_DLY_UUS (here and in ex_06e)" % (time.ticks_diff(t_queued, t_rx), t_dec, t_enc))
    except KeyboardInterrupt:
        pass
    dwt.dwt_forcetrxoff()
    print("Stopped. Transceiver forced off.")


if __name__ == "__main__":
    main()
