"""
platform/port.py

RP2040 (Pico) + ArduPico carrier board-specific I/O for the DWS3000 shield.
MicroPython equivalent of the upstream project's platform/port.c —
GPIO setup, reset control, and hardware SPI object creation.

This is the ONLY file that should need editing to retarget a different
carrier board / pin mapping. Everything else (decadriver, examples) talks
to the objects this module exposes, not to raw pin numbers.

*** VERIFY THE PINS BELOW AGAINST YOUR ARDUPICO BOARD. *** See
docs/pinout.md. "ArduPico" is not a single standardized pinout across
vendors.
"""

from machine import Pin, SPI
import utime as time

# ---------------------------------------------------------------------------
# Board selection. The ArduPico and Flexypin carriers are near-clones; the
# only difference found so far is which Pico GPIO drives the DW3000's RSTn.
# (On the ArduPico, GP20 is the RP2040's own reset line -- not the DW3000's.)
#
# DWS3000 shield signals (from upstream boards/shields/qorvo_dwm3000):
#   RESET = Arduino D7, IRQ = D8, WAKEUP = D9.
# Flexypin (verified from its KiCad schematic): D7 = GP9, D8 = GP2, D9 = GP3.
# ArduPico: only RESET (GP7) is known. IRQ/WAKEUP are None until someone
# checks that board's schematic -- anything needing them raises, rather than
# silently driving some unrelated GPIO.
# ---------------------------------------------------------------------------
BOARD = "flexypin"          # "ardupico" or "flexypin"

_RST_GPIO_BY_BOARD = {
    "ardupico": 7,
    "flexypin": 9,
}

_IRQ_GPIO_BY_BOARD = {
    "ardupico": None,       # unverified -- fill in from the ArduPico schematic (D8)
    "flexypin": 2,          # D8
}

_WAKEUP_GPIO_BY_BOARD = {
    "ardupico": None,       # unverified -- fill in from the ArduPico schematic (D9)
    "flexypin": 3,          # D9
}

# ---------------------------------------------------------------------------
# Pin assignment -- edit this dict if your wiring differs.
# ---------------------------------------------------------------------------
PINS = {
    "spi_id":   0,      # RP2040 hardware SPI peripheral (SPI0)
    "sck":      18,
    "mosi":     19,
    "miso":     16,
    "cs":       17,     # driven manually (not by hw auto-CS) so decadriver
                         # can hold CS across multi-byte header+body transfers
    "rst":      _RST_GPIO_BY_BOARD[BOARD],  # DW3000 RSTn -- open-drain, active low
    "irq":      _IRQ_GPIO_BY_BOARD[BOARD],        # DW3000 IRQ -> host (None = unknown)
    "wakeup":   _WAKEUP_GPIO_BY_BOARD[BOARD],     # DW3000 WAKEUP (None = unknown)
}

# SPI clock speeds. The DW3000 requires a slow SPI clock (<3 MHz) until
# dwt_initialise() has completed (it reads OTP/tuning values at low speed),
# then can be run fast (~20-38 MHz depending on IC state) afterward.
SPI_SLOW_HZ = 2_000_000
SPI_FAST_HZ = 16_000_000

# Safety valve for the interrupt servicing loop (see Port.service_irq).
_ISR_MAX_PASSES = 16


class Port:
    """
    Owns the physical pins/peripheral for one DW3000 device.
    Equivalent of the global state set up by platform/port.c's port_init()
    style functions in the upstream project.
    """

    def __init__(self, pins=None):
        self.pins = pins or PINS

        # --- SPI bus, opened slow initially (matches deca_spi.c openspi()) ---
        self.spi = SPI(
            self.pins["spi_id"],
            baudrate=SPI_SLOW_HZ,
            polarity=0,
            phase=0,
            bits=8,
            firstbit=SPI.MSB,
            sck=Pin(self.pins["sck"]),
            mosi=Pin(self.pins["mosi"]),
            miso=Pin(self.pins["miso"]),
        )

        # --- chip select, manual (active low) ---
        self.cs = Pin(self.pins["cs"], Pin.OUT, value=1)

        # --- reset line: open-drain, so "asserting reset" = drive low,
        #     "released" = switch to input (let external pull-up take it high) ---
        self._rst_pin_no = self.pins["rst"]
        self.rst = Pin(self._rst_pin_no, Pin.IN)  # released by default

        # --- IRQ from DW3000 ---
        # The DW3000 drives this line push-pull (active high); the pull-down
        # only stops it floating (and raising phantom interrupts) if the
        # shield is absent.
        self.irq = (Pin(self.pins["irq"], Pin.IN, Pin.PULL_DOWN)
                    if self.pins["irq"] is not None else None)

        # --- interrupt dispatch state (see set_dwic_isr) ---
        self.spi_busy = False       # True while an SPI transaction is in flight
        self.isr_pending = False    # an IRQ arrived mid-transaction / mid-ISR
        self.isr_overruns = 0       # times the line was still high after the pass limit
        self._isr = None
        self._in_service = False

        # --- WAKEUP line to DW3000 ---
        self.wakeup = (Pin(self.pins["wakeup"], Pin.OUT, value=0)
                       if self.pins["wakeup"] is not None else None)

    # -----------------------------------------------------------------
    # SPI speed control -- decadriver calls this after dwt_initialise()
    # -----------------------------------------------------------------
    def spi_set_rate_low(self):
        self.spi.init(baudrate=SPI_SLOW_HZ)

    def spi_set_rate_high(self):
        self.spi.init(baudrate=SPI_FAST_HZ)

    # -----------------------------------------------------------------
    # Reset -- equivalent of reset_DWIC() in the upstream examples
    # -----------------------------------------------------------------
    def reset_dwic(self):
        """
        Hard-reset the DW3000. Open-drain drive low, hold briefly, release,
        then wait for the IC to complete its internal boot/OTP-load sequence.
        """
        rst = Pin(self._rst_pin_no, Pin.OUT, value=0)
        time.sleep_ms(2)
        rst = Pin(self._rst_pin_no, Pin.IN)  # release -> pulled high externally
        self.rst = rst
        time.sleep_ms(2)  # allow DW3000 internal boot to complete

    # -----------------------------------------------------------------
    # IRQ handling -- equivalent of port_set_dwic_isr() in upstream port.c
    # -----------------------------------------------------------------
    def set_dwic_isr(self, handler):
        """
        Install `handler` (normally decadriver's dwt_isr) to run whenever the
        DW3000 raises its IRQ line (rising edge, as upstream).

        The pin handler is a *soft* IRQ (hard=False): MicroPython schedules it
        to run between bytecodes, which is what allows `handler` to do SPI
        transfers and allocate memory.
        """
        if self.irq is None:
            raise RuntimeError("IRQ pin not defined for board %r (see port.py)" % BOARD)
        self._isr = handler
        self.isr_pending = False
        self.irq.irq(handler=self._irq_entry, trigger=Pin.IRQ_RISING, hard=False)

    def clear_dwic_isr(self):
        """Stop calling the handler (e.g. on exit)."""
        if self.irq is not None:
            self.irq.irq(handler=None)
        self._isr = None

    def _irq_entry(self, pin):
        # Runs as a scheduled soft IRQ. If it landed in the middle of a
        # main-loop SPI transaction, don't interleave with it: note it, and
        # deca_spi services it the moment that transaction ends.
        if self.spi_busy:
            self.isr_pending = True
            return
        self.service_irq()

    def service_irq(self):
        """
        Run the installed handler. Repeats while the IRQ line is still high or
        another IRQ arrived meanwhile: the line is level-like (it stays high
        while any enabled event is pending), so an event that arrives while the
        handler is already running produces no fresh edge to trigger on.
        """
        if self._isr is None:
            return
        if self._in_service:        # re-entered from inside the handler's own SPI
            self.isr_pending = True
            return
        self._in_service = True
        try:
            for _ in range(_ISR_MAX_PASSES):
                self.isr_pending = False
                self._isr()
                if not (self.isr_pending or self.irq.value()):
                    return
            self.isr_overruns += 1  # still asserted after the limit
        finally:
            self._in_service = False

    def wakeup_pulse(self):
        """Pulse WAKEUP to bring the DW3000 out of low-power sleep."""
        if self.wakeup is None:
            raise RuntimeError("WAKEUP pin not defined for board %r (see port.py)" % BOARD)
        self.wakeup.value(1)
        time.sleep_us(500)
        self.wakeup.value(0)


# Module-level singleton, mirroring the single-device global state pattern
# used throughout the upstream C examples (one DW3000 per host in all
# ported examples so far).
port = Port()
