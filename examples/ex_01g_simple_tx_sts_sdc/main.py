"""
examples/ex_01g_simple_tx_sts_sdc/main.py

MicroPython port of the upstream ex_01g_simple_tx_sts_sdc example
(simple_tx_sts_sdc.c): send an IEEE 802.15.4z frame with a STS (Scrambled
Timestamp Sequence) in packet configuration 1 (preamble, SFD, STS, PHR, data) and
with SDC (Super Deterministic Code) enabled -- the STS then comes from a fixed
code rather than from the AES key/IV, so a receiver needs no shared secret.
Pair it with ex_02g_simple_rx_sts_sdc (not ported yet) or look at the frames
with any receiver configured the same (SFD type 3, STS mode 1 + SDC, 64-symbol
STS) -- a receiver without STS (ex_02a) will not decode these frames.

The STS itself is generated and sent by the chip; there is nothing to do for it
in the host code except configure it (dwt_configure does everything: SYS_CFG
CP_SPC/CP_SDC bits, STS length, STS acceptance threshold, and the longer
preamble-based OPS table).

Note the frame is 15 bytes plus the 2-byte FCS: the two zero bytes at the end
of tx_msg are part of the payload (upstream's sizeof() includes them).
"""

import utime as time

from platform.port import port
from decadriver import deca_device as dwt
from decadriver import deca_regs as R
from decadriver.deca_types import DwtConfig
from config_options import txconfig_options

APP_NAME = "SIMPLE TX STS SDC (MicroPython) v0.1"

# Communication configuration with STS packet configuration 1 and SDC.
config = DwtConfig(
    chan=5,
    txPreambLength=R.DWT_PLEN_128,
    rxPAC=R.DWT_PAC8,
    txCode=9,
    rxCode=9,
    sfdType=3,                          # 4z 8 symbol SFD
    dataRate=R.DWT_BR_6M8,
    phrMode=R.DWT_PHRMODE_STD,
    phrRate=R.DWT_PHRRATE_STD,
    sfdTO=(129 + 8 - 8),
    stsMode=R.DWT_STS_MODE_1 | R.DWT_STS_MODE_SDC,
    stsLength=R.DWT_STS_LEN_64,
    pdoaMode=R.DWT_PDOA_M0,
)

# IEEE 802.15.4 data frame: frame control 0x8841 (16-bit addressing, PAN ID
# compression, no ACK), sequence number, PAN ID 0xDECA, destination "XR", source
# "XT", then the payload "DATA" and two (zero) bytes upstream also counts.
tx_msg = bytearray([0x41, 0x88, 0, 0xCA, 0xDE, ord('X'), ord('R'), ord('X'), ord('T'),
                    ord('D'), ord('A'), ord('T'), ord('A'), 0, 0])

FRAME_SN_IDX = 2
FRAME_PAYLOAD_IDX = 9

FRAME_LENGTH = len(tx_msg) + R.FCS_LEN      # length actually transmitted (FCS appended by the IC)

# Inter-frame delay period, in milliseconds.
TX_DELAY_MS = 500


def main():
    print(APP_NAME)

    port.spi_set_rate_high()
    port.reset_dwic()
    time.sleep_ms(2)  # INIT_RC -> IDLE_RC

    while not dwt.dwt_checkidlerc():
        pass

    if dwt.dwt_initialise(R.DWT_DW_INIT) == R.DWT_ERROR:
        print("INIT FAILED")
        while True:
            pass

    # LEDs for debug: D1 flashes on each TX on the red DW3000 eval shields.
    dwt.dwt_setleds(R.DWT_LEDS_ENABLE | R.DWT_LEDS_INIT_BLINK)

    if dwt.dwt_configure(config):
        print("CONFIG FAILED")
        while True:
            pass

    dwt.dwt_configuretxrf(txconfig_options)

    # TX/RX state output on GPIOs 5 and 6, for debug.
    dwt.dwt_setlnapamode(R.DWT_LNA_ENABLE | R.DWT_PA_ENABLE)

    try:
        while True:
            dwt.dwt_writetxdata(FRAME_LENGTH - R.FCS_LEN, bytes(tx_msg), 0)
            dwt.dwt_writetxfctrl(FRAME_LENGTH, 0, 0)    # zero offset, no ranging
            dwt.dwt_starttx(R.DWT_START_TX_IMMEDIATE)

            while not (dwt.dwt_read32bitreg(R.SYS_STATUS_ID) & R.SYS_STATUS_TXFRS_BIT_MASK):
                pass  # spin until TX frame sent
            dwt.dwt_write32bitreg(R.SYS_STATUS_ID, R.SYS_STATUS_TXFRS_BIT_MASK)

            time.sleep_ms(TX_DELAY_MS)

            tx_msg[FRAME_SN_IDX] = (tx_msg[FRAME_SN_IDX] + 1) & 0xFF
            print("frame sent, next seq %d" % tx_msg[FRAME_SN_IDX])
    except KeyboardInterrupt:
        dwt.dwt_forcetrxoff()
        print("\nStopped by user (Ctrl-C). Transceiver forced off.")


if __name__ == "__main__":
    main()
