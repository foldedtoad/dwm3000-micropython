"""
examples/ex_06e_aes_ss_twr_initiator/main.py

MicroPython port of upstream ex_06e_aes_ss_twr_initiator (ss_aes_twr_initiator.c):
single-sided two-way ranging with the poll and response frames encrypted and
authenticated by the chip's AES engine (CCM*, 128-bit key, 16-byte MIC), in an
IEEE 802.15.4 frame with 64-bit addresses and an auxiliary security header.
The initiator uses key index 1 and the responder key index 2; each side picks the
decryption key from the key index in the received header. Distance is printed by
this side. Start ex_06f_aes_ss_twr_responder first.

Differences from upstream, forced by MicroPython being slower than C (same
reasoning as ex_06a/ex_06b, see there):
  * The responder has to read the poll, decrypt it and encrypt the response over
    SPI before the reply is due; that takes several ms, so the reply delay is
    50000 uus here instead of upstream's 2000 (a first hardware run showed the
    host needs 30-45 ms to decrypt the poll and encrypt the reply; 50000 uus is
    about 51 ms, and must stay under ~65000 uus, the 32-bit timestamp wrap). The reply is still sent at a
    precise hardware-timed instant. The receive window is derived from it
    (upstream relation: window opens 280 uus before the reply, 250 uus long),
    widened by WINDOW_MARGIN_UUS each side. Change POLL_RX_TO_RESP_TX_DLY_UUS in
    BOTH ex_06e and ex_06f.
  * Single-sided ranging error grows with the reply delay (clock offset x delay);
    the clock-offset correction is applied as upstream, with exact integer math
    since float32 cannot hold ~10^9 device ticks.
  * Errors (AES failure etc.) stop the program with a message instead of spinning.
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

APP_NAME = "SS AES TWR INIT (MicroPython) v0.1"

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

# Addresses (upstream): DEST is the responder, SRC is this initiator.
DEST_ADDR = 0x1122334455667788
SRC_ADDR = 0x8877665544332211

RNG_DELAY_MS = 1000

# MAC payloads. Response: first 8 bytes carry poll RX time and response TX time.
tx_poll_msg = bytearray(b"Poll message")
rx_resp_msg = bytes([0, 0, 0, 0, 0, 0, 0, 0]) + b"Response"
START_RECEIVE_DATA_LOCATION = 8

# Key index 0 is forbidden on air, so index 1 is the first key.
INITIATOR_KEY_INDEX = 1

# --- timing (uus) -------------------------------------------------------------
# Delay from poll RX to response TX. Must equal the responder's (ex_06f). Upstream: 2000.
POLL_RX_TO_RESP_TX_DLY_UUS = 50000
# Slack each side of the expected response. Wide (5 ms) for now: the first
# hardware run with 1 ms missed the reply, and the printed round trip then
# shows where the reply really arrives so the margin can be narrowed.
WINDOW_MARGIN_UUS = 5000
# End of poll TX -> receiver on (upstream 1720 for a 2000 reply delay).
POLL_TX_TO_RESP_RX_DLY_UUS = POLL_RX_TO_RESP_TX_DLY_UUS - 280 - WINDOW_MARGIN_UUS
# Receiver on time waiting for the response (upstream 250).
RESP_RX_TIMEOUT_UUS = 250 + 2 * WINDOW_MARGIN_UUS


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
    dwt.dwt_setrxaftertxdelay(POLL_TX_TO_RESP_RX_DLY_UUS)
    dwt.dwt_setrxtimeout(RESP_RX_TIMEOUT_UUS)
    dwt.dwt_setlnapamode(R.DWT_LNA_ENABLE | R.DWT_PA_ENABLE)

    # The same MAC frame object holds both transmitted and received headers
    # (as upstream), so addresses are set again before each transmission.
    mac_frame = mac.new_initiator_frame()
    rx_buffer = bytearray(RX_BUF_LEN)

    # TX job: encrypt the poll into the TX buffer; RX job: decrypt the response in the RX buffer.
    aes_job_tx = DwtAesJob(mode=R.AES_Encrypt, src_port=R.AES_Src_Tx_buf, dst_port=R.AES_Dst_Tx_buf,
                           header=mac_frame.header, header_len=mac.HEADER_LEN,
                           payload=tx_poll_msg, payload_len=len(tx_poll_msg))
    aes_job_rx = DwtAesJob(mode=R.AES_Decrypt, src_port=R.AES_Src_Rx_buf_0, dst_port=R.AES_Dst_Rx_buf_0,
                           header=mac_frame.header, header_len=mac.HEADER_LEN, payload=rx_buffer)

    print("Initiator ready (reply delay %d uus, rx on after %d uus, timeout %d uus)" %
          (POLL_RX_TO_RESP_TX_DLY_UUS, POLL_TX_TO_RESP_RX_DLY_UUS, RESP_RX_TIMEOUT_UUS))

    frame_cnt = 0
    seq_cnt = 0x0A
    try:
        while True:
            # Key for this frame, and its index in the header.
            dwt.dwt_set_keyreg_128(keys_options[INITIATOR_KEY_INDEX - 1])
            mac.set_aux_key_identifier(mac_frame, INITIATOR_KEY_INDEX)

            # Header addresses, and the 13-byte nonce built from the header.
            mac.set_pan_ids_and_addresses(mac_frame, DEST_PAN_ID, DEST_ADDR, SRC_ADDR)
            aes_job_tx.nonce = mac.get_nonce(mac_frame)
            aes_job_tx.mic_size = mac.get_aux_mic_size(mac_frame)
            aes_config.mode = R.AES_Encrypt
            aes_config.mic = dwt.dwt_mic_size_from_bytes(aes_job_tx.mic_size)
            dwt.dwt_configure_aes(aes_config)

            # The AES job writes the encrypted frame into the TX buffer.
            status = dwt.dwt_do_aes(aes_job_tx, aes_config.aes_core_type)
            if status < 0:
                print("AES length error"); break
            if status & R.AES_ERRORS:
                print("ERROR AES (status 0x%02X)" % status); break

            dwt.dwt_writetxfctrl(aes_job_tx.header_len + aes_job_tx.payload_len + aes_job_tx.mic_size + R.FCS_LEN, 0, 1)
            t_tx = time.ticks_us()
            dwt.dwt_starttx(R.DWT_START_TX_IMMEDIATE | R.DWT_RESPONSE_EXPECTED)

            while True:
                status_reg = dwt.dwt_read32bitreg(R.SYS_STATUS_ID)
                if status_reg & (R.SYS_STATUS_RXFCG_BIT_MASK | R.SYS_STATUS_ALL_RX_TO | R.SYS_STATUS_ALL_RX_ERR):
                    break
            t_evt = time.ticks_diff(time.ticks_us(), t_tx)

            # Sequence number and frame counter advance after each poll.
            seq_cnt = (seq_cnt + 1) & 0xFF
            mac.update_sequence_number(mac_frame, seq_cnt)
            frame_cnt += 1
            mac.update_aux_frame_cnt(mac_frame, frame_cnt)

            if status_reg & R.SYS_STATUS_RXFCG_BIT_MASK:
                dwt.dwt_write32bitreg(R.SYS_STATUS_ID, R.SYS_STATUS_RXFCG_BIT_MASK)
                frame_len = dwt.dwt_read32bitreg(R.RX_FINFO_ID) & RXFLEN_MASK

                # Read the header, check it is secure and for us, decrypt.
                aes_config.mode = R.AES_Decrypt
                status = mac.rx_aes_802_15_4(mac_frame, frame_len, aes_job_rx, len(rx_buffer),
                                             keys_options, DEST_ADDR, SRC_ADDR, aes_config)
                if status == AES_RES_ERROR_IGNORE_FRAME:
                    print("frame not for us, ignored")
                    time.sleep_ms(RNG_DELAY_MS)
                    continue
                if status != AES_RES_OK:
                    print(aes_error(status)); break

                payload_len = aes_job_rx.payload_len
                if bytes(rx_buffer[START_RECEIVE_DATA_LOCATION:payload_len]) == rx_resp_msg[START_RECEIVE_DATA_LOCATION:payload_len]:
                    poll_tx_ts = dwt.dwt_readtxtimestamplo32()
                    resp_rx_ts = dwt.dwt_readrxtimestamplo32()
                    clock_offset_raw = dwt.dwt_readclockoffset()

                    poll_rx_ts = resp_msg_get_ts(rx_buffer, RESP_MSG_POLL_RX_TS_IDX)
                    resp_tx_ts = resp_msg_get_ts(rx_buffer, RESP_MSG_RESP_TX_TS_IDX)

                    rtd_init = (resp_rx_ts - poll_tx_ts) & 0xFFFFFFFF
                    rtd_resp = (resp_tx_ts - poll_rx_ts) & 0xFFFFFFFF

                    # tof = (rtd_init - rtd_resp * (1 - clockOffsetRatio)) / 2, in device ticks
                    diff = rtd_init - rtd_resp
                    corr = (rtd_resp * clock_offset_raw) / (1 << 26)
                    tof = ((diff + corr) / 2.0) * DWT_TIME_UNITS
                    distance = tof * SPEED_OF_LIGHT
                    dist_uncorrected = (diff / 2.0) * DWT_TIME_UNITS * SPEED_OF_LIGHT
                    print("dist %3.2f m   (offset %+.2f ppm, uncorrected %3.2f m)  [AES ok, resp counter %d]" %
                          (distance, clock_offset_raw * 1e6 / (1 << 26), dist_uncorrected,
                           mac.get_aux_frame_cnt(mac_frame)))
                    print("   round trip %.2f uus (reply delay %d uus), response seen after %d us" %
                          (rtd_init / UUS_TO_DWT_TIME, POLL_RX_TO_RESP_TX_DLY_UUS, t_evt))
                else:
                    print("decrypted frame is not the expected response")
            else:
                dwt.dwt_write32bitreg(R.SYS_STATUS_ID, R.SYS_STATUS_ALL_RX_TO | R.SYS_STATUS_ALL_RX_ERR)
                print("no valid response, status 0x%08X, gave up after %d us (window closes at ~%d us)" %
                      (status_reg & (R.SYS_STATUS_ALL_RX_TO | R.SYS_STATUS_ALL_RX_ERR), t_evt,
                       (POLL_TX_TO_RESP_RX_DLY_UUS + RESP_RX_TIMEOUT_UUS) * 5120 // 4992))

            time.sleep_ms(RNG_DELAY_MS)
    except KeyboardInterrupt:
        pass
    dwt.dwt_forcetrxoff()
    print("Stopped. Transceiver forced off.")


if __name__ == "__main__":
    main()
