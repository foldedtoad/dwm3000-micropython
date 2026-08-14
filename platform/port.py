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
# Pin assignment -- edit this dict to match your ArduPico + DWS3000 wiring.
# ---------------------------------------------------------------------------
PINS = {
    "spi_id":   0,      # RP2040 hardware SPI peripheral (SPI0)
    "sck":      18,
    "mosi":     19,
    "miso":     16,
    "cs":       17,     # driven manually (not by hw auto-CS) so decadriver
                         # can hold CS across multi-byte header+body transfers
    "rst":      20,     # DW3000 RSTn -- open-drain, active low
    "irq":      21,     # DW3000 IRQ -> host
    "wakeup":   22,     # DW3000 WAKEUP -- optional, only needed for sleep modes
}

# SPI clock speeds. The DW3000 requires a slow SPI clock (<3 MHz) until
# dwt_initialise() has completed (it reads OTP/tuning values at low speed),
# then can be run fast (~20-38 MHz depending on IC state) afterward.
SPI_SLOW_HZ = 2_000_000
SPI_FAST_HZ = 16_000_000


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
        self.irq = Pin(self.pins["irq"], Pin.IN)

        # --- WAKEUP line to DW3000 ---
        self.wakeup = Pin(self.pins["wakeup"], Pin.OUT, value=0)

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

    def wakeup_pulse(self):
        """Pulse WAKEUP to bring the DW3000 out of low-power sleep."""
        self.wakeup.value(1)
        time.sleep_us(500)
        self.wakeup.value(0)


# Module-level singleton, mirroring the single-device global state pattern
# used throughout the upstream C examples (one DW3000 per host in all
# ported examples so far).
port = Port()
