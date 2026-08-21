# Pinout: RP2040 (ArduPico) ↔ DWS3000 shield

The DWS3000 is an Arduino-form-factor shield. On the original Nordic
targets, `platform/port.c` configures these signals (from the upstream
project's boot log):

```
Configure WAKEUP pin
Configure RESET pin
Configure RX LED pin
Configure TX LED pin
Configure SPI Phase pin      (D-pin, sets DW3000 SPI CPHA via a GPIO strap)
Configure SPI Polarity pin   (D-pin, sets DW3000 SPI CPOL via a GPIO strap)
SPI bus open (hardware SPI)
```

Plus the standard Arduino SPI header lines (MOSI/MISO/SCK/CS) and an IRQ
line from the DW3000 to a host GPIO.

## Default RP2040 GPIO assignment used in `platform/port.py`

| Signal | RP2040 GPIO | Arduino header pin (typical) | Notes |
|---|---|---|---|
| SPI SCK | GP18 | D13 | RP2040 hardware `SPI0` default |
| SPI MOSI | GP19 | D11 | RP2040 hardware `SPI0` default |
| SPI MISO | GP16 | D12 | RP2040 hardware `SPI0` default |
| SPI CS (DW3000 chip select) | GP17 | D10 | Driven manually in software, not by hw SPI auto-CS |
| RSTn (DW3000 reset, open-drain) | GP7 | D7 | Confirmed on hardware. (GP20 is the ArduPico's own RP2040 reset line — do not use it for the DW3000.) |
| IRQ (DW3000 → host) | GP21 | D2 | **Verify** — must be a Pico GPIO, any pin works (no INT0 restriction on RP2040) |
| WAKEUP | GP22 | D8 | **Verify** — only needed once sleep modes are ported |

**These GPIO numbers assume your ArduPico wires Arduino D10-D13 straight
through to RP2040 GP16-GP19 (the Pico's native SPI0 pins), which is common
but not guaranteed.** Cross-check against your ArduPico's own pinout
diagram before wiring/trusting this. If your carrier maps the Arduino SPI
header to a different GPIO set (e.g. `SPI1` on GP10-GP15), update
`platform/port.py`'s `PINS` dict accordingly — nothing else needs to
change, since the rest of the driver only talks to that dict.

## Confirming your mapping without a scope

1. Set `PINS` to your best guess.
2. Run `examples/ex_00a_reading_dev_id/main.py`.
3. If it hangs or reads back `0x00000000` / `0xffffffff`, the SPI or CS
   line is wrong — check MOSI/MISO/SCK/CS first (dev-ID read doesn't
   depend on RESET/IRQ/WAKEUP being correct).
4. If it reads back a plausible-looking but non-`0xdeca....` ID, RESET
   may be wired wrong (chip never came out of reset cleanly) — check
   RSTn next.
