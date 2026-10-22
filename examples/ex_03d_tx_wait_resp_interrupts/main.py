"""
examples/ex_03d_tx_wait_resp_interrupts/main.py

MicroPython port of the upstream ex_03d_tx_wait_resp_interrupts example
(tx_wait_resp_int.c).

The same exchange as ex_03a (send a blink, wait for the response), but driven
by the DW3000's IRQ line instead of polling SYS_STATUS. The ISR (dwt_isr)
calls back into this file for TX-done, RX-good-frame, RX-timeout and RX-error
events; the callbacks set `tx_delay_ms`, which is also how they signal the main
loop that the exchange has finished. Run ex_03b_rx_send_resp on the other board.

This is the first example that uses the IRQ pin (Flexypin: GP2 = Arduino D8).
"""

import utime as time

from platform.port import port
from decadriver import deca_device as dwt
from decadriver import deca_regs as R
from decadriver.deca_types import DwtConfig
from config_options import txconfig_options
from shared_data.shared_defines import FRAME_LEN_MAX

APP_NAME = "TX WAITRESP INT (MicroPython) v0.1"

# Must match the responder (ex_03b) exactly.
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

# Blink frame (see ex_03a for the byte layout).
tx_msg = bytearray([0xC5, 0, ord('D'), ord('E'), ord('C'), ord('A'), ord('W'),
                    ord('A'), ord('V'), ord('E'), 0x43, 0x02, 0, 0])
BLINK_FRAME_SN_IDX = 1

# Delay from end of transmission to turning the receiver on, in UWB microseconds.
TX_TO_RX_DELAY_UUS = 60

# Response wait timeout, in UWB microseconds. Upstream uses 10000 for this
# example; a MicroPython responder needs ~5-6 ms just to turn around (measured
# on ex_03a/03b), so this is widened the same way as in ex_03a.
RX_RESP_TO_UUS = 50000

# Default inter-frame delay, in milliseconds.
DFLT_TX_DELAY_MS = 1000

# After an RX timeout, assume the receiver is absent and slow the blink rate.
RX_TO_TX_DELAY_MS = 3000

# After an RX error, assume the receiver is present but its response was lost,
# and retry immediately.
RX_ERR_TX_DELAY_MS = 0

# If no interrupt at all arrives this long after a transmission, something is
# wrong (the response window is ~51 ms), so say so rather than hang silently.
IRQ_WATCHDOG_MS = 1000

# Current inter-frame delay. Also the mechanism the callbacks use to signal the
# main loop: -1 means "no event yet"; the callbacks set it to a delay (>= 0).
tx_delay_ms = -1

# Time the last transmission was started, for latency reporting.
t_tx = 0

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


# --------------------------------------------------------------------------
# Callbacks, called by dwt_isr() (as scheduled soft IRQs). Each receives a
# DwtCbData. They only record what happened and set tx_delay_ms.
# --------------------------------------------------------------------------
def rx_ok_cb(cb_data):
    """RX good frame."""
    global tx_delay_ms
    t_evt = time.ticks_us()

    if cb_data.datalength <= FRAME_LEN_MAX:
        rx_buffer = dwt.dwt_readrxdata(cb_data.datalength, 0)
        print("rx_ok_cb: len %d: %s  [round trip %d us]" % (
            cb_data.datalength, hexdump(rx_buffer), time.ticks_diff(t_evt, t_tx)))

    tx_delay_ms = DFLT_TX_DELAY_MS


def rx_to_cb(cb_data):
    """RX timeout (frame-wait or preamble-detect)."""
    global tx_delay_ms
    print("rx_to_cb: timeout (%d us after TX start)" % time.ticks_diff(time.ticks_us(), t_tx))
    tx_delay_ms = RX_TO_TX_DELAY_MS


def rx_err_cb(cb_data):
    """RX error."""
    global tx_delay_ms
    names = [name for mask, name in _RX_ERRORS if cb_data.status & mask]
    print("rx_err_cb: error", " ".join(names))
    tx_delay_ms = RX_ERR_TX_DELAY_MS


def tx_conf_cb(cb_data):
    """
    TX confirmation. Nothing to do in this example: the receiver is turned on
    automatically by the DW3000 (DWT_RESPONSE_EXPECTED). Upstream leaves its log
    line commented out; it is enabled here because it is the visible proof that
    the TX-done interrupt arrived.
    """
    print("tx_conf_cb: frame sent")


def main():
    global tx_delay_ms, t_tx

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

    if dwt.dwt_configure(config):
        print("CONFIG FAILED")
        while True:
            pass

    dwt.dwt_setleds(R.DWT_LEDS_ENABLE | R.DWT_LEDS_INIT_BLINK)
    dwt.dwt_configuretxrf(txconfig_options)

    # Register the callbacks (SPI error and SPI ready callbacks are not used).
    dwt.dwt_setcallbacks(tx_conf_cb, rx_ok_cb, rx_to_cb, rx_err_cb, None, None)

    # Enable the interrupts we want: TX confirmation, RX good frames, RX
    # timeouts and RX errors.
    dwt.dwt_setinterrupt(
        R.SYS_ENABLE_LO_TXFRS_ENABLE_BIT_MASK |
        R.SYS_ENABLE_LO_RXFCG_ENABLE_BIT_MASK |
        R.SYS_ENABLE_LO_RXFTO_ENABLE_BIT_MASK |
        R.SYS_ENABLE_LO_RXPTO_ENABLE_BIT_MASK |
        R.SYS_ENABLE_LO_RXPHE_ENABLE_BIT_MASK |
        R.SYS_ENABLE_LO_RXFCE_ENABLE_BIT_MASK |
        R.SYS_ENABLE_LO_RXFSL_ENABLE_BIT_MASK |
        R.SYS_ENABLE_LO_RXSTO_ENABLE_BIT_MASK,
        0,
        R.DWT_ENABLE_INT)

    # Clear the SPI-ready interrupt left over from power-up.
    dwt.dwt_write32bitreg(R.SYS_STATUS_ID, R.SYS_STATUS_RCINIT_BIT_MASK | R.SYS_STATUS_SPIRDY_BIT_MASK)

    # Install the DW IC IRQ handler.
    port.set_dwic_isr(dwt.dwt_isr)
    print("IRQ handler installed on GP%d" % port.pins["irq"])

    dwt.dwt_setrxaftertxdelay(TX_TO_RX_DELAY_UUS)
    dwt.dwt_setrxtimeout(RX_RESP_TO_UUS)

    print("Sending started")

    try:
        while True:
            print("msg len %d: %s" % (len(tx_msg), hexdump(tx_msg)))

            dwt.dwt_writetxdata(len(tx_msg), bytes(tx_msg), 0)
            dwt.dwt_writetxfctrl(len(tx_msg), 0, 0)  # no ranging

            # Start TX; the receiver turns on automatically when it ends.
            dwt.dwt_starttx(R.DWT_START_TX_IMMEDIATE | R.DWT_RESPONSE_EXPECTED)
            t_tx = time.ticks_us()

            # Wait for any RX event (set by a callback). The loop's back-jump
            # is where MicroPython runs the scheduled interrupt handler.
            t_wait = time.ticks_ms()
            warned = False
            while tx_delay_ms == -1:
                if not warned and time.ticks_diff(time.ticks_ms(), t_wait) > IRQ_WATCHDOG_MS:
                    warned = True
                    print("WARNING: no interrupt %d ms after TX. IRQ pin GP%d level=%d, SYS_STATUS=0x%08x"
                          % (IRQ_WATCHDOG_MS, port.pins["irq"], port.irq.value(),
                             dwt.dwt_read32bitreg(R.SYS_STATUS_ID)))
                    print("         (level 0 with status bits set = IRQ wire not reaching the Pico)")

            # Execute the defined delay before the next transmission.
            if tx_delay_ms > 0:
                time.sleep_ms(tx_delay_ms)

            tx_msg[BLINK_FRAME_SN_IDX] = (tx_msg[BLINK_FRAME_SN_IDX] + 1) & 0xFF

            # Reset the event signal, ready for the next exchange.
            tx_delay_ms = -1
    except KeyboardInterrupt:
        port.clear_dwic_isr()
        dwt.dwt_forcetrxoff()
        print("\nStopped by user (Ctrl-C). Interrupt handler removed, transceiver forced off.")


if __name__ == "__main__":
    main()
