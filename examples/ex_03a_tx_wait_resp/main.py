"""
examples/ex_03a_tx_wait_resp/main.py

MicroPython port of the upstream ex_03a_tx_wait_resp example (tx_wait_resp.c).

Sends a blink frame, then turns the receiver on automatically
(DWT_RESPONSE_EXPECTED) to wait for a response. The response is only
displayed, not checked. After the response arrives, or the wait times out,
it sends the next frame. Run ex_03b_rx_send_resp on the other board, which
expects exactly the frame this example sends and replies to it.
"""

import utime as time

from platform.port import port
from decadriver import deca_device as dwt
from decadriver import deca_regs as R
from decadriver.deca_types import DwtConfig
from config_options import txconfig_options
from shared_data.shared_defines import FRAME_LEN_MAX

APP_NAME = "TX WAITRESP (MicroPython) v0.1"

# Default communication configuration -- non-STS DW mode. Must match the
# responder (ex_03b) exactly.
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

# Blink frame, ISO/IEC 24730-62:2013 style, 14 bytes:
#   byte 0      0xC5 = multipurpose frame, 64-bit addressing
#   byte 1      sequence number, incremented per frame
#   bytes 2-9   device ID ("DECAWAVE" here; a real product needs a unique ID)
#   byte 10     0x43 = encoding header, no extended ID/temperature/battery
#   byte 11     0x02 = tag is listening for a response right after this frame
#   bytes 12-13 frame check sequence slot, filled in by the DW IC
tx_msg = bytearray([0xC5, 0, ord('D'), ord('E'), ord('C'), ord('A'), ord('W'),
                    ord('A'), ord('V'), ord('E'), 0x43, 0x02, 0, 0])
BLINK_FRAME_SN_IDX = 1

TX_DELAY_MS = 1000          # inter-frame delay

# Delay from end of transmission to turning the receiver on, in UWB
# microseconds (1 uus = 512/499.2 us). The responder needs ~70 us to turn
# around, so 60 keeps the receiver on only as long as necessary.
TX_TO_RX_DELAY_UUS = 60

# Response wait timeout, in UWB microseconds (1 uus = 1.0256 us; the register
# is 20 bits wide, so up to ~1 s is possible).
#
# Upstream uses 5000 here, sized for a C responder that turns around in ~70 us.
# A MicroPython responder is far slower: every SPI register access passes
# through several Python layers, so its turnaround is measured in milliseconds.
# 50000 (~51 ms) leaves comfortable room; the cost is only a longer wait when
# no responder is running. The measured round trip printed below shows how
# much of this window is actually used.
RX_RESP_TO_UUS = 50000

# Print the DW3000 register values the wait-for-response feature depends on,
# once at startup, so a misconfiguration is visible rather than guessed at.
SHOW_SETUP = True

_RX_ERRORS = (
    (R.SYS_STATUS_RXPHE_BIT_MASK, "RXPHE"),
    (R.SYS_STATUS_RXFCE_BIT_MASK, "RXFCE"),
    (R.SYS_STATUS_RXFSL_BIT_MASK, "RXFSL"),
    (R.SYS_STATUS_RXSTO_BIT_MASK, "RXSTO"),
    (R.SYS_STATUS_ARFE_BIT_MASK, "ARFE"),
    (R.SYS_STATUS_CIAERR_BIT_MASK, "CIAERR"),
)


def hexdump(data):
    return " ".join("%02x" % b for b in data)


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

    dwt.dwt_setleds(R.DWT_LEDS_ENABLE | R.DWT_LEDS_INIT_BLINK)

    if dwt.dwt_configure(config):
        print("CONFIG FAILED")
        while True:
            pass

    dwt.dwt_configuretxrf(txconfig_options)

    # Receiver turn-on delay after TX, and response timeout.
    dwt.dwt_setrxaftertxdelay(TX_TO_RX_DELAY_UUS)
    dwt.dwt_setrxtimeout(RX_RESP_TO_UUS)

    if SHOW_SETUP:
        print("setup: ACK_RESP=0x%08x (W4R delay=%d uus)  RX_FWTO=%d  SYS_CFG=0x%08x (RXWTOE=%d)" % (
            dwt.dwt_read32bitreg(R.ACK_RESP_ID),
            dwt.dwt_read32bitreg(R.ACK_RESP_ID) & R.ACK_RESP_W4R_TIM_BIT_MASK,
            dwt.dwt_read32bitreg(R.RX_FWTO_ID),
            dwt.dwt_read32bitreg(R.SYS_CFG_ID),
            1 if dwt.dwt_read32bitreg(R.SYS_CFG_ID) & R.SYS_CFG_RXWTOE_BIT_MASK else 0))

    print("Sending started")

    try:
        while True:
            print("msg len %d: %s" % (len(tx_msg), hexdump(tx_msg)))

            dwt.dwt_writetxdata(len(tx_msg), bytes(tx_msg), 0)
            dwt.dwt_writetxfctrl(len(tx_msg), 0, 0)  # no ranging

            # Start TX; the receiver is enabled automatically when it ends.
            dwt.dwt_starttx(R.DWT_START_TX_IMMEDIATE | R.DWT_RESPONSE_EXPECTED)
            t_tx = time.ticks_us()

            # Poll for a good frame, a timeout, or an error.
            events = R.SYS_STATUS_RXFCG_BIT_MASK | R.SYS_STATUS_ALL_RX_TO | R.SYS_STATUS_ALL_RX_ERR
            while True:
                status_reg = dwt.dwt_read32bitreg(R.SYS_STATUS_ID)
                if status_reg & events:
                    break
            t_evt = time.ticks_us()

            if status_reg & R.SYS_STATUS_RXFCG_BIT_MASK:
                frame_len = dwt.dwt_read32bitreg(R.RX_FINFO_ID) & R.RX_FINFO_RXFLEN_BIT_MASK
                rx_buffer = b""
                if frame_len <= FRAME_LEN_MAX:
                    rx_buffer = dwt.dwt_readrxdata(frame_len, 0)
                # Round trip = our blink's airtime + responder turnaround + the
                # response's airtime, as seen from this host (polling granularity
                # adds a few hundred us).
                print("resp len %d: %s  [round trip %d us]" % (
                    frame_len, hexdump(rx_buffer), time.ticks_diff(t_evt, t_tx)))
                dwt.dwt_write32bitreg(R.SYS_STATUS_ID, R.SYS_STATUS_RXFCG_BIT_MASK)
            else:
                # (The upstream example clears these silently; reporting them
                # makes it obvious whether the responder is running.)
                if status_reg & R.SYS_STATUS_RXFTO_BIT_MASK:
                    print("no response (frame-wait timeout after %d us)" % time.ticks_diff(t_evt, t_tx))
                if status_reg & R.SYS_STATUS_RXPTO_BIT_MASK:
                    print("no response (preamble-detect timeout after %d us)" % time.ticks_diff(t_evt, t_tx))
                for mask, name in _RX_ERRORS:
                    if status_reg & mask:
                        print("receive error:", name)
                dwt.dwt_write32bitreg(R.SYS_STATUS_ID, R.SYS_STATUS_ALL_RX_TO | R.SYS_STATUS_ALL_RX_ERR)

            time.sleep_ms(TX_DELAY_MS)

            tx_msg[BLINK_FRAME_SN_IDX] = (tx_msg[BLINK_FRAME_SN_IDX] + 1) & 0xFF
    except KeyboardInterrupt:
        dwt.dwt_forcetrxoff()
        print("\nStopped by user (Ctrl-C). Transceiver forced off.")


if __name__ == "__main__":
    main()
