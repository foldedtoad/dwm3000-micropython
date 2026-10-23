# Pinout: RP2040 carrier boards ↔ DWS3000 shield

The DWS3000 is an Arduino-form-factor shield. Per upstream's shield
definition (`boards/shields/qorvo_dwm3000`), it uses:

| Signal | Arduino pin | Direction |
|---|---|---|
| SPI SCK / MISO / MOSI / CS | D13 / D12 / D11 / D10 | host ↔ DW3000 |
| RSTn (open-drain, active low) | D7 | host → DW3000 |
| IRQ | D8 | DW3000 → host |
| WAKEUP | D9 | host → DW3000 |

## Per-board GPIO mapping (`platform/port.py`, set with `BOARD`)

| Signal | Flexypin | ArduPico |
|---|---|---|
| SCK (D13) | GP18 | GP18 |
| MISO (D12) | GP16 | GP16 |
| MOSI (D11) | GP19 | GP19 |
| CS (D10), driven in software | GP17 | GP17 |
| RSTn (D7) | **GP9** | **GP7** |
| IRQ (D8) | **GP2** | *unverified — not yet checked* |
| WAKEUP (D9) | **GP3** | *unverified — not yet checked* |

**How each was established:**

- *Flexypin:* every entry is read from the board's KiCad schematic/PCB
  (`solderparty/flexypin_adapters_hw`, `rpi_pico_uno_flexypin`): header pads
  D13/D12/D11/D10 → GP18/16/19/17, D7 → GP9, D8 → GP2, D9 → GP3. SPI and RSTn
  are also confirmed working on hardware; IRQ and WAKEUP are from the
  schematic and have not been exercised yet (ex_03d is the first example
  to use IRQ, and prints a diagnostic if the line never fires).
- *ArduPico:* SPI and RSTn (GP7) are confirmed on hardware. IRQ and WAKEUP are
  `None` in `port.py` rather than guessed — code that needs them raises a clear
  error. To fill them in, check which RP2040 GPIO the ArduPico routes to
  Arduino D8 and D9.

**Notes**

- On the ArduPico, GP20 is the RP2040's own reset line — not the DW3000's.
- An earlier version of this file guessed IRQ = GP21 and WAKEUP = GP22. Both
  were wrong: GP21 is the Arduino SCL pin and GP22 is not on the Arduino
  headers. Polled-mode examples never use IRQ, so nothing exposed it.

## Diagnosing a bad dev-ID read

1. All-zero / all-one: SPI problem — check MOSI/MISO/SCK/CS first.
2. Plausible but not `0xdeca....`: RSTn probably wrong — the chip never came
   out of reset cleanly.
