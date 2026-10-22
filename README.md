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
| `decadriver/` (register-level DW3000 driver) | `dwt_xfer3000` (all 3 SPI header modes), offset-register read/write/modify, OTP read, `dwt_initialise`, `dwt_configure` (non-SCP path), `dwt_configuretxrf`, TX path (`dwt_writetxdata`/`dwt_writetxfctrl`/`dwt_starttx`), `dwt_setleds`. Diagnostics: `dwt_configciadiag`, `dwt_readdiagnostics` (normal mode), `dwt_readaccdata`, `dwt_configeventcounters`, `dwt_readeventcounters`. RX path: `dwt_rxenable` (immediate + delayed), `dwt_readrxdata`, `dwt_setrxaftertxdelay`, `dwt_setrxtimeout`, interrupts (`dwt_setcallbacks`, `dwt_setinterrupt`, `dwt_isr`; single RX buffer only). Ranging, timestamps, STS/PDOA/AES **not yet ported**. |
| `shared_data/` | `FRAME_LEN_MAX` only so far; ranging structures to be added with `ex_05*`/`ex_06*` |
| `examples/ex_00a_reading_dev_id` | **Working, verified on hardware** — reads back `0xdeca0302` |
| `examples/ex_01a_simple_tx` | **Working, verified on hardware** — frames going out every 500ms, clean status polling, graceful Ctrl-C handling. |
| `examples/util_read_chip_id` | **Part ID verified on hardware.** Reads the DW3000's factory-unique Part ID / Lot ID from OTP. Not an upstream-numbered example (foldedtoad/dwm3000 doesn't have one); added as a small standalone utility. (An earlier EUI_64-based version of this utility was scrapped -- EUI_64 is a plain register, not OTP-backed, so it isn't actually factory-unique.) |
| `examples/ex_02a_simple_rx` | **Working, verified on hardware.** Receives frames in polled mode; pair with `ex_01a_simple_tx` on the second board. |
| `examples/ex_03b_rx_send_resp` | **Working, verified on hardware.** Receives the `ex_03a` blink frame and replies to its sender. |
| `examples/ex_03a_tx_wait_resp` | **Working, verified on hardware.** Sends a blink, then waits for the response (with timeout). Run `ex_03b` on the other board. |
| `examples/ex_03d_tx_wait_resp_interrupts` | **Success path verified on hardware** (timeout and Ctrl-C paths not yet tried). The ex_03a exchange driven by the DW3000 IRQ line. First use of the IRQ pin (GP2 on the Flexypin, from its schematic). Run `ex_03b` on the other board. |
| `examples/ex_02c_rx_diagnostics` | **Ported, passes unit + end-to-end tests. Not yet run on hardware.** Polled RX; after each good frame prints CIA diagnostics (first path, F1/F2/F3, peak, xtal offset) and raw accumulator bytes; after every event prints the event counters. Pair with `ex_01a_simple_tx`. |
| Remaining examples (`ex_02d`+, ranging, ...) | Not started |

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

```
python3 tests/run_all.py
```

Runs on a desktop CPython using fake `machine`/`utime` modules
(`tests/mocks/`), one process per suite. They check the **logic**:

- **SPI header bytes** for each kind of register access, compared against
  values worked out by hand from the header formula.
- **Driver functions** (RX, RX-after-TX delay/timeout, interrupt enable) --
  exact register values written, including that read-modify-write preserves
  bits outside the mask.
- **`dwt_isr`** -- every event class, with the register-clear vs callback
  *ordering* upstream relies on (RX/timeout/error events are cleared before
  their callback, SPI-ready after it).
- **IRQ dispatch** (`platform/port.py`) -- an interrupt landing mid-SPI
  transaction is deferred until it ends, handlers never nest, a line that
  stays high keeps being serviced (bounded), and a throwing handler can't
  wedge the dispatcher.
- **Whole examples** run end-to-end against a simulated DW3000 -- for
  ex_03d the real ISR and real dispatch run against a chip that holds status
  bits and only drops its IRQ line when software clears them.

None of this can catch a wrong GPIO number, an SPI-mode mismatch, or an RF
problem; only real hardware can. Hardware runs so far have found one real
issue the tests could not: MicroPython's slower turnaround made upstream's
5000 uus response window too short (see ex_03a/03b).

## Next steps (in order)

1. ~~Confirm pin mapping~~ — **done.** `platform/port.py` has a `BOARD`
   selector: `"ardupico"` (RSTn on GP7) or `"flexypin"` (RSTn on GP9, IRQ
   on GP2, WAKEUP on GP3). SPI is identical on both. The ArduPico's IRQ and
   WAKEUP pins are not yet known (see `docs/pinout.md`). On the ArduPico,
   GP20 is the RP2040's own reset line, not the DW3000's.
2. ~~`ex_00a_reading_dev_id`~~ — **done**, reads back `0xdeca0302`.
3. ~~`ex_01a_simple_tx`~~ — **done**, verified on hardware.
4. ~~`ex_02a_simple_rx`~~ — **done**, verified on hardware with `ex_01a`.
5. **Run `ex_03b_rx_send_resp` on one board and `ex_03a_tx_wait_resp` on the
   other** (start 03b first). First hardware run showed the sender timing out
   on every frame even though the responder replied every time, so both
   examples now print timing diagnostics: 03a prints the round trip (or how
   long it waited before timing out) and a one-time `setup:` line reading back
   the delay/timeout registers; 03b prints its host turnaround. Working
   theory: upstream's 5000 uus response window is sized for a C responder
   (~70 us turnaround), too short for MicroPython. 03a's window is now 50000
   uus, and 03b replies before printing. Not yet confirmed on hardware.
6. **Run `ex_03d_tx_wait_resp_interrupts`** (with `ex_03b` on the other
   board, started first). You should see, per exchange, `tx_conf_cb: frame
   sent` then `rx_ok_cb: len 21: ...` with a round-trip time. Stop 03b to see
   `rx_to_cb: timeout` instead. If nothing happens for 1 s the example prints
   a WARNING with the IRQ pin level and `SYS_STATUS`: level 0 with status
   bits set means the IRQ wire isn't reaching the Pico (check GP2 / Arduino
   D8).
7. **Run `ex_02c_rx_diagnostics`** with `ex_01a_simple_tx` on the other board. Each good frame prints a `first path idx` line (expect a value near the preamble position, plausibly typically a few hundred samples in; the exact value depends on the setup), `ipatov peak/F1/F2/F3`, and raw accumulator bytes; every event prints the counters (`CRCG` should climb with each received frame). Known upstream quirk kept as-is: the accumulator offset is passed as a byte offset, so accumulator bytes are shown raw, not decoded.
8. **Deferred:** improve interrupt latency (ex_03d round trip ~8-10 ms vs ~6-7 ms polled), possibly with PIO; measure where the overhead goes first.
9. Further examples (ranging `ex_05*`/`ex_06*`) — each pulls in more of `decadriver`
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
