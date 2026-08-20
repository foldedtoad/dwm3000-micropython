# dwm3000-micropython

A MicroPython port of [foldedtoad/dwm3000](https://github.com/foldedtoad/dwm3000)
(Qorvo/Decawave DW3000 on the DWS3000 Arduino shield), targeting a
**Raspberry Pi Pico (RP2040) on an ArduPico Arduino-shield carrier**.

This is a from-scratch re-implementation, not a transliteration of the
original GPL-3.0 C sources. It mirrors the original project's module
boundaries so that logic and fixes can be cross-referenced against the
upstream C project as porting continues.

## Status (2026-08-20)

| Layer | State |
|---|---|
| `platform/` (GPIO, reset, SPI open) | Implemented for RP2040. Pin mapping **confirmed on real ArduPico hardware** (dev-ID read succeeds). |
| `decadriver/` (register-level DW3000 driver) | `dwt_xfer3000` (all 3 SPI header modes), offset-register read/write/modify, OTP read, `dwt_initialise`, `dwt_configure` (non-SCP path), `dwt_configuretxrf`, TX path (`dwt_writetxdata`/`dwt_writetxfctrl`/`dwt_starttx`), `dwt_setleds`. RX path and ranging **not yet ported**. |
| `shared_data/` | Placeholder — not needed until ranging examples (`ex_05*`, `ex_06*`) are ported |
| `examples/ex_00a_reading_dev_id` | **Working, verified on hardware** — reads back `0xdeca0302` |
| `examples/ex_01a_simple_tx` | **Ported, passes control-flow + SPI-header unit tests. Not yet run on hardware.** |
| `examples/ex_02a_simple_rx` and beyond | Not started |

The original `decadriver/deca_device.c` is ~5,000 lines of register-level
init/config/TX/RX/ranging logic. Every function used by `ex_00a` and
`ex_01a` was ported by pulling the actual upstream source (via
`codeload.github.com`, not search snippets or memory) and transliterating
it line-by-line — register addresses, bitmasks, and control flow all
traced back to the real `deca_regs.h` / `deca_device_api.h` /
`deca_vals.h` / `deca_device.c`. Nothing was guessed. Two independent
checks back this up (see `Testing` below): a control-flow smoke test of
the whole `dwt_initialise` → `dwt_configure` → TX sequence, and a direct
unit test of the SPI header byte-encoding against hand-worked expected
values. Neither replaces running it on your actual DW3000 -- do that
next.

RX path, STS/PDOA/AES variants, sleep modes, and ranging are still
unported. Port those the same way (against the real source) when the
next example needs them, rather than guessing at bit-level semantics.

## Testing

Because this environment can't drive real SPI hardware, `decadriver`'s
logic was validated two ways before being handed to you:

1. **Control-flow test** — the low-level register accessors are mocked
   with plausible register values (dev ID, status bits, OTP data), and
   the full `dwt_check_dev_id` → `dwt_initialise` → `dwt_setleds` →
   `dwt_configure` → `dwt_configuretxrf` → `dwt_writetxdata` →
   `dwt_writetxfctrl` → `dwt_starttx` chain is run end-to-end. Confirms
   no Python-level bugs (wrong arg counts, bad masks, missing branches)
   and exercises both the OTP-DGC and hard-coded-LUT code paths.
2. **SPI header unit test** — `dwt_xfer3000` is called directly for
   known register addresses (offset-0 FACRW case, nonzero-offset EAMRW
   case, a fast command, a plain write, an AND/OR modify) and the actual
   header bytes produced are checked against hand-computed expected
   values from the real `dwt_xfer3000` formula.

Both are testing logic, not electrical behavior — they can't catch a
wrong GPIO number or an SPI mode-0-vs-mode-3 mismatch. Only real hardware
can confirm those, which is why `ex_01a_simple_tx` is marked "not yet run
on hardware" above even though it passes both tests.

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

1. ~~Confirm pin mapping~~ — **done**, `platform/port.py` matches real
   hardware (RST is GP7, confirmed by hardware test; GP20 is the
   ArduPico's own RP2040 reset line, not the DW3000's).
2. ~~Run `ex_00a_reading_dev_id`~~ — **done**, reads back `0xdeca0302`.
3. **Run `examples/ex_01a_simple_tx/main.py` on hardware** — this is the
   next thing to do. It should print `Sending started` and then log each
   outgoing blink frame every 500ms. If you have a second DW3000 board
   (or a sniffer), confirm frames are actually going out over the air —
   the code can look successful (status register shows TXFRS) while
   still having an RF-level problem the status register can't see.
4. Port `ex_02a_simple_rx` — requires the RX path
   (`dwt_rxenable`, `dwt_readrxdata`, status-register polling/IRQ).
5. Ranging examples (`ex_05*`/`ex_06*`) pull in `shared_data` and
   timestamp/antenna-delay handling.
