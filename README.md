# dwm3000-micropython

A MicroPython port of [foldedtoad/dwm3000](https://github.com/foldedtoad/dwm3000)
(Qorvo/Decawave DW3000 on the DWS3000 Arduino shield), targeting a
**Raspberry Pi Pico (RP2040) on an ArduPico Arduino-shield carrier**.

This is a from-scratch re-implementation, not a transliteration of the
original GPL-3.0 C sources. It mirrors the original project's module
boundaries so that logic and fixes can be cross-referenced against the
upstream C project as porting continues.

## Status (2026-08-14)

| Layer | State |
|---|---|
| `platform/` (GPIO, reset, SPI open) | Implemented for RP2040 |
| `decadriver/` (register-level DW3000 driver) | **Minimal subset only** — SPI transaction primitive + device-ID / generic register read. `dwt_initialise`, `dwt_configure`, TX/RX paths **not yet ported**. |
| `shared_data/` | Placeholder — not needed until ranging examples (`ex_05*`, `ex_06*`) are ported |
| `examples/ex_00a_reading_dev_id` | **Working** |
| `examples/ex_01a_simple_tx` and beyond | Not started |

The original `decadriver/deca_device.c` is ~5,000+ lines of register-level
init/config/TX/RX/ranging logic. Porting it correctly (rather than
guessing at bit-level register semantics) requires working line-by-line
against the real source and, ideally, a scope/logic analyzer against real
hardware. This scaffold gives you a correct, working foundation
(SPI transaction layer + reset/GPIO layer + dev-ID readback) to build the
rest onto example-by-example, rather than a fabricated "full driver" that
looks complete but silently gets register semantics wrong.

## Directory layout (mirrors the C project)

```
dwm3000-micropython/
├── platform/           # board I/O: SPI open, GPIO, reset — RP2040/ArduPico specific
│   ├── port.py
│   └── deca_spi.py
├── decadriver/          # DW3000 register-level driver — target independent
│   ├── deca_regs.py     # register file / offset constants
│   └── deca_device.py   # dwt_xfer3000 + the small set of dwt_* calls ported so far
├── shared_data/          # ranging/shared structures (placeholder, future examples)
├── examples/
│   └── ex_00a_reading_dev_id/
│       └── main.py
└── docs/
    └── pinout.md
```

## Hardware: Pico + ArduPico + DWS3000 shield

See `docs/pinout.md`. **The GPIO numbers in `platform/port.py` are a
starting configuration you must verify against your specific ArduPico
board's silkscreen/schematic** — "ArduPico" is used by more than one
vendor and the Arduino-header-to-RP2040-GPIO mapping is not universal.
The SPI pins (GP16/17/18/19) match the RP2040's default hardware `SPI0`
pins, which most Arduino-shield-for-Pico adapters wire straight through
to the Arduino D10-D13 (SS/MOSI/MISO/SCK) positions — but confirm before
trusting it with real hardware.

## Next steps (in order)

1. Confirm/edit pin mapping in `platform/port.py` for your ArduPico
   against real hardware (scope the SCK line, or just confirm dev-ID
   read succeeds).
2. Run `examples/ex_00a_reading_dev_id/main.py` — should print
   `DEV_ID: 0xdeca0302` (DW3110/DW3000 default) or a Qorvo `0xdeca...`
   variant ID.
3. Port `ex_01a_simple_tx` — requires `dwt_initialise`, `dwt_configure`,
   `dwt_writetxdata`, `dwt_writetxfctrl`, `dwt_starttx`. This is the
   next chunk of `decadriver` that needs a faithful, verified port.
4. Port `ex_02a_simple_rx` — requires the RX path
   (`dwt_rxenable`, `dwt_readrxdata`, status-register polling/IRQ).
5. Ranging examples (`ex_05*`/`ex_06*`) pull in `shared_data` and
   timestamp/antenna-delay handling.
