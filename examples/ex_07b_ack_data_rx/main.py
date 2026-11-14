"""
examples/ex_07b_ack_data_rx/main.py

MicroPython port of upstream ex_07b_ack_data_rx (ack_data_rx.c).

Receives data frames addressed to PAN 0xDECA / short address 0x5258, with
frame filtering (data frames only) and the chip's automatic acknowledgement
enabled: the chip itself replies with an ACK as soon as a frame that requests
one is received. Companion of ex_07a_ack_data_tx (start this one first).
"""

import utime as time

from platform.port import port
from decadriver import deca_device as dwt
from decadriver import deca_regs as R
from decadriver.deca_types import DwtConfig
from config_options import txconfig_options

APP_NAME = "ACK DATA RX (MicroPython) v0.1"

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

PAN_ID = 0xDECA
SHORT_ADDR = 0x5258  # "RX"
FCTRL_ACK_REQ_MASK = 0x20
FRAME_LEN_MAX = 127


def main():
    print(APP_NAME)
    port.spi_set_rate_high()
    port.reset_dwic()
    time.sleep_ms(2)
    while not dwt.dwt_checkidlerc():
        pass
    if dwt.dwt_initialise(R.DWT_DW_IDLE) == R.DWT_ERROR:
        print("INIT FAILED")
        while True:
            pass
    if dwt.dwt_configure(config):
        print("CONFIG FAILED")
        while True:
            pass
    dwt.dwt_configuretxrf(txconfig_options)

    dwt.dwt_setpanid(PAN_ID)
    dwt.dwt_setaddress16(SHORT_ADDR)
    # Frame filtering must be enabled for auto-ACK to work; data frames only.
    dwt.dwt_configureframefilter(R.DWT_FF_ENABLE_802_15_4, R.DWT_FF_DATA_EN)
    # ACK as soon as possible after reception.
    dwt.dwt_enableautoack(0, 1)
    dwt.dwt_setlnapamode(R.DWT_LNA_ENABLE | R.DWT_PA_ENABLE)
    dwt.dwt_setleds(R.DWT_LEDS_ENABLE | R.DWT_LEDS_INIT_BLINK)

    dwt.dwt_write32bitreg(R.SYS_STATUS_ID, R.SYS_STATUS_RXFCG_BIT_MASK)
    count = 0
    try:
        while True:
            dwt.dwt_rxenable(R.DWT_START_RX_IMMEDIATE)
            while True:
                status_reg = dwt.dwt_read32bitreg(R.SYS_STATUS_ID)
                if status_reg & (R.SYS_STATUS_RXFCG_BIT_MASK | R.SYS_STATUS_ALL_RX_ERR):
                    break

            if status_reg & R.SYS_STATUS_RXFCG_BIT_MASK:
                dwt.dwt_write32bitreg(R.SYS_STATUS_ID, R.SYS_STATUS_RXFCG_BIT_MASK)
                frame_len = dwt.dwt_read32bitreg(R.RX_FINFO_ID) & R.EXT_FRAME_LEN
                rx_buffer = dwt.dwt_readrxdata(frame_len, 0) if frame_len <= FRAME_LEN_MAX else b""
                count += 1

                # Frame requested an ACK: the chip sends it by itself; wait for it to finish.
                acked = False
                if rx_buffer and (rx_buffer[0] & FCTRL_ACK_REQ_MASK):
                    while not (dwt.dwt_read32bitreg(R.SYS_STATUS_ID) & R.SYS_STATUS_TXFRS_BIT_MASK):
                        pass
                    dwt.dwt_write32bitreg(R.SYS_STATUS_ID, R.SYS_STATUS_TXFRS_BIT_MASK)
                    acked = True
                print("frame %d: len %d seq %s %s" %
                      (count, frame_len, rx_buffer[2] if len(rx_buffer) > 2 else "?",
                       "-> ACK sent" if acked else "(no ACK requested)"))
            else:
                dwt.dwt_write32bitreg(R.SYS_STATUS_ID, R.SYS_STATUS_ALL_RX_ERR)
    except KeyboardInterrupt:
        dwt.dwt_forcetrxoff()
        print("\nStopped by user (Ctrl-C). Transceiver forced off.")


if __name__ == "__main__":
    main()
