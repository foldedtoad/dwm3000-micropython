# dwm3000-micropython

A MicroPython port of [foldedtoad/dwm3000](https://github.com/foldedtoad/dwm3000)
(Qorvo/Decawave DW3000 on the DWS3000 Arduino shield), targeting a
**Raspberry Pi Pico (RP2040) on an ArduPico or Flexypin Arduino-shield carrier**.

This is a MicroPython port of the upstream C project and mirrors its module
boundaries (`decadriver/`, `platform/`, `shared_data/`, `examples/`) so logic
and fixes can be cross-referenced against upstream. `platform/` is new code
for the RP2040. `decadriver/` is a line-by-line transliteration of the
upstream `deca_device.c` and its headers, so it is a derivative work (see
**License and attribution** below).

## Status (2026-10-19)

| Layer | State |
|---|---|
| `platform/` (GPIO, reset, SPI open) | Implemented for RP2040. Pin mapping **confirmed on real ArduPico hardware** (dev-ID read succeeds). |
| `decadriver/` (register-level DW3000 driver) | `dwt_xfer3000` (all 3 SPI header modes), offset-register read/write/modify, OTP read, `dwt_initialise`, `dwt_configure` (non-SCP path), `dwt_configuretxrf`, TX path (`dwt_writetxdata`/`dwt_writetxfctrl`/`dwt_starttx`), `dwt_setleds`. RX path: `dwt_rxenable` (immediate + delayed) and `dwt_readrxdata`. Ranging, timestamps, STS/PDOA/AES **not yet ported**. |
| `shared_data/` | `FRAME_LEN_MAX` only so far; ranging structures to be added with `ex_05*`/`ex_06*` |
| `examples/ex_00a_reading_dev_id` | **Working, verified on hardware** — reads back `0xdeca0302` |
| `examples/ex_01a_simple_tx` | **Working, verified on hardware** — frames going out every 500ms, clean status polling, graceful Ctrl-C handling. |
| `examples/util_read_chip_id` | **Ported, needs a hardware run.** Reads the DW3000's factory-unique Part ID / Lot ID from OTP. Not an upstream-numbered example (foldedtoad/dwm3000 doesn't have one); added as a small standalone utility. (An earlier EUI_64-based version of this utility was scrapped -- EUI_64 is a plain register, not OTP-backed, so it isn't actually factory-unique.) |
| `examples/ex_02a_simple_rx` | **Ported, passes end-to-end + SPI-level unit tests. Not yet run on hardware.** Receives frames in polled mode; pair with `ex_01a_simple_tx` on the second board. |
| Remaining examples (`ex_02c`+, `ex_03*`, ranging, ...) | Not started |

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

1. ~~Confirm pin mapping~~ — **done.** `platform/port.py` has a `BOARD`
   selector: `"ardupico"` (RSTn on GP7) or `"flexypin"` (RSTn on GP9);
   everything else is identical between the two carriers. On the ArduPico,
   GP20 is the RP2040's own reset line, not the DW3000's.
2. ~~`ex_00a_reading_dev_id`~~ — **done**, reads back `0xdeca0302`.
3. ~~`ex_01a_simple_tx`~~ — **done**, verified on hardware.
4. **Run `ex_02a_simple_rx` on one board while `ex_01a_simple_tx` runs on
   the other.** Expect `len 10: b'\xc5\x..DECAWAVE'` lines on the receiver,
   with the sequence byte incrementing. Both boards must use the same
   channel/preamble/PRF settings (the two examples' configs already match).
5. Further examples (`ex_02c_rx_diagnostics`, `ex_03*` TX-wait-response,
   ranging `ex_05*`/`ex_06*`) — each pulls in more of `decadriver`
   (timestamps, antenna delay, diagnostics) and `shared_data`.

## License and attribution

This project is a derivative work of
[foldedtoad/dwm3000](https://github.com/foldedtoad/dwm3000), which is
licensed under the **GNU General Public License v3.0**; this port is
distributed under the same license (see `LICENSE`).

The `decadriver/` code is ported from the Decawave/Qorvo DW3000 driver and
retains the original copyright notices:

- Copyright 2013-2020 (c) Decawave Ltd, Dublin, Ireland.
- Copyright 2016 (c) DecaWave Ltd; 2019 (c) Frederic Mes, RTLOC (platform layer).
- Copyright 2021 (c) Callender-Consulting, LLC (Zephyr port).

Register addresses, bit masks and control flow in `decadriver/` were taken
from the upstream sources; the MicroPython code, RP2040 `platform/` layer
and tests are new.
