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
| `decadriver/` (register-level DW3000 driver) | `dwt_xfer3000` (all 3 SPI header modes), offset-register read/write/modify, OTP read, `dwt_initialise`, `dwt_configure` (non-SCP path), `dwt_configuretxrf`, TX path (`dwt_writetxdata`/`dwt_writetxfctrl`/`dwt_starttx`), `dwt_setleds`. Diagnostics: `dwt_configciadiag`, `dwt_readdiagnostics` (normal mode), `dwt_readaccdata`, `dwt_configeventcounters`, `dwt_readeventcounters`. SNIFF mode: `dwt_setsniffmode`, `dwt_setlnapamode`. Crystal trim: `dwt_setxtaltrim`, `dwt_getxtaltrim`, `dwt_readclockoffset`. Ranging: `dwt_setrx/txantennadelay`, `dwt_readtx/rxtimestamp` (+`lo32`), `dwt_setdelayedtrxtime`; `shared_data/shared_functions.py` has the timestamp helpers. RX path: `dwt_rxenable` (immediate + delayed), `dwt_readrxdata`, `dwt_setrxaftertxdelay`, `dwt_setrxtimeout`, interrupts (`dwt_setcallbacks`, `dwt_setinterrupt`, `dwt_isr`; single RX buffer only). Ranging, timestamps, STS/PDOA/AES **not yet ported**. |
| `shared_data/` | Frame-length and timing constants, plus the ranging timestamp helpers used by `ex_05*`/`ex_06*` |
| `examples/ex_00a_reading_dev_id` | **Working, verified on hardware** — reads back `0xdeca0302` |
| `examples/ex_01a_simple_tx` | **Working, verified on hardware** — frames going out every 500ms, clean status polling, graceful Ctrl-C handling. |
| `examples/util_read_chip_id` | **Part ID verified on hardware.** Reads the DW3000's factory-unique Part ID / Lot ID from OTP. Not an upstream-numbered example (foldedtoad/dwm3000 doesn't have one); added as a small standalone utility. (An earlier EUI_64-based version of this utility was scrapped -- EUI_64 is a plain register, not OTP-backed, so it isn't actually factory-unique.) |
| `examples/ex_02a_simple_rx` | **Working, verified on hardware.** Receives frames in polled mode; pair with `ex_01a_simple_tx` on the second board. |
| `examples/ex_03b_rx_send_resp` | **Working, verified on hardware.** Receives the `ex_03a` blink frame and replies to its sender. |
| `examples/ex_03a_tx_wait_resp` | **Working, verified on hardware.** Sends a blink, then waits for the response (with timeout). Run `ex_03b` on the other board. |
| `examples/ex_03d_tx_wait_resp_interrupts` | **Success path verified on hardware** (timeout and Ctrl-C paths not yet tried). The ex_03a exchange driven by the DW3000 IRQ line. First use of the IRQ pin (GP2 on the Flexypin, from its schematic). Run `ex_03b` on the other board. |
| `examples/ex_02c_rx_diagnostics` | **Working, verified on hardware** (CRCG counts frames, first-path index steady ~740, no errors). Polled RX; after each good frame prints CIA diagnostics (first path, F1/F2/F3, peak, xtal offset) and raw accumulator bytes; after every event prints the event counters. Pair with `ex_01a_simple_tx`. |
| `examples/ex_02d_rx_sniff` | **Working, verified on hardware** (consecutive sequence numbers received, no errors at the default 50% duty cycle). Same polled RX as ex_02a with SNIFF mode (receiver duty-cycled ~50%). Pair with `ex_01a_simple_tx`. |
| `examples/ex_02f_rx_with_crystal_trim` | **Working, verified on hardware** (trim 33 -> 30 on the first frame, then offset settled at about -2.3 ppm with no further adjustment). Receiver measures the transmitter's clock offset after each good frame and trims its own crystal until the offset is 2-4 ppm. Prints offset and trim per frame. Pair with `ex_01a_simple_tx`. |
| `examples/ex_06a_ss_twr_initiator`, `ex_06b_ss_twr_responder` | **Verified on hardware** (round trip 20000.27 uus for a 20000 uus reply delay; at a measured 30 cm the corrected readings averaged ~0.34 m, so the clock-offset sign is right and the default antenna delays are close; scatter about +/-0.3 m from the clock-offset estimate. At a measured 1.00 m, 14 readings: mean 1.15 m, median 1.17 m, sd 0.23 m (about +10-15 cm bias, likely uncalibrated antenna delay). The scatter is noise in the per-frame clock-offset reading (+/-0.1 ppm, ~3 m per ppm), not real clock jitter: the uncorrected distance is smooth. Smoothing the offset over ~8 exchanges would likely cut the scatter to ~10 cm; not implemented yet) After a 5 minute warm-up (14 readings at 1.00 m): mean 1.01 m, median 1.03 m, sd 0.15 m (0.08 m excluding one 0.55 m outlier that coincided with a -1.22 ppm offset spike); the uncorrected distance went flat, so the early drift and most of the +15 cm bias were crystal warm-up. Let the boards warm up for a few minutes before judging accuracy). Single-sided two-way ranging. Start 06b first. The responder's reply delay is 20000 uus instead of upstream's 650 (MicroPython can't turn a poll around that fast); the distance math uses exact integers because RP2040 MicroPython floats are 32-bit. |
| `examples/ex_05a_ds_twr_initiator`, `ex_05b_ds_twr_responder` | **Verified on hardware** (antennas 30 cm apart: readings 0.23-0.27 m, spread about +/-0.02 m versus +/-0.3 m for ex_06; Ra/Rb/Da/Db all 15000.24-15000.27 uus; host turnaround 6.7-6.8 ms initiator, 8.6-8.8 ms responder against 15.4 ms allowed; about 5 cm low, i.e. antenna delay not calibrated). Double-sided two-way ranging (poll, response, final). The responder computes and prints the distance. Start 05b first. Reply delays are 15000 uus instead of upstream's ~700-900, windows widened by 1000 uus, preamble timeout off (same reasons as ex_06). Double-sided ranging cancels the clock offset to first order, so the long delays should hurt much less than in ex_06. The distance math uses exact integers. |
| `examples/ex_01b_tx_sleep` | **Default variant verified on hardware** (Flexypin, WAKEUP pin; receiver ex_02a saw sequence numbers 0-10 consecutively, no frame lost after any wake; wake to IDLE_RC took 3.64-3.67 ms each cycle). The `USE_IDLE_RC = True` variant and the ArduPico CS-wake fallback are not yet tested. TX a blink, sleep, wake, restore config, repeat (set `USE_IDLE_RC = True` for upstream's `tx_sleep_idleRC.c` variant). Wake uses the WAKEUP pin (Flexypin GP3); on ArduPico, where that pin is unknown, `port.wakeup_pulse()` holds CS low 500 us instead (the example enables wake-on-CS). `dwt_restoreconfig` fixes an upstream bug in the TX-code test that stopped the DGC restore from ever running. |
| `examples/ex_01c_tx_sleep_auto` | **Verified on hardware** (Flexypin WAKEUP pin; receiver ex_02a saw consecutive sequence numbers 0-6 while the TX board was at seq 5; wake to IDLE_RC 3.75-3.82 ms). The CS-wake fallback for ArduPico is not tested. The chip sleeps by itself after each transmitted frame (`dwt_entersleepaftertx`); the host just waits 1 s, wakes it and restores the config. Upstream wakes on the WAKEUP pin only; CS wake is also enabled here for ArduPico. |
| `examples/ex_01d_tx_timed_sleep` | **Verified on hardware** (LP oscillator 24150 Hz, sleep counter 5, nominal 848 ms; measured sleep 854-855 ms, frame period 860-861 ms; receiver saw consecutive sequence numbers 0-7). Chip sleeps and wakes itself from its sleep timer; an SPI-ready interrupt (IRQ line, Flexypin GP2) reports the wake. Prints the calibrated low-power oscillator frequency, sleep counter, nominal sleep time and measured frame period. Sleep time is quantised (about 120-273 ms steps) and rounded down, so frames come somewhat faster than 1 s. Needs the IRQ pin, so not usable on ArduPico until its IRQ pin is known. |
| `examples/ex_01e_tx_with_cca` | **Quiet-channel path verified on hardware** (13 frames sent, 0 CCA failures, receiver saw consecutive sequence numbers); the CCA-failure/back-off path has only been tested on the desktop, since no second transmitter was on the air. Transmit with clear channel assessment (`DWT_START_TX_CCA`, 3-PAC preamble timeout); on CCA failure the host backs off (400 ms, +1 ms per consecutive failure). Fixes an upstream flaw: the CCA_FAIL status bit is cleared after a failure (upstream never clears it, so later attempts would all look failed). |
| `examples/ex_01g_simple_tx_sts_sdc` | **Verified on hardware** (with ex_02g as receiver; a non-STS receiver such as ex_02a will not decode these frames). TX with STS packet configuration 1 and SDC, SFD type 3, 64-symbol STS. Testing it exposed a bug in the earlier `dwt_configure` port: the STS minimum threshold was computed from the wrong factor and came out 0 instead of 0x10 (fixed; only matters when STS is enabled). |
| `examples/ex_02g_simple_rx_sts_sdc` | **Verified on hardware** together with ex_01g (all frames `STS is GOOD`, quality 61-62 against the threshold of 57, status 0x000, sequence numbers consecutive, 0 bad). Receives ex_01g's STS+SDC frames and checks STS quality (must reach 90% of 64 = 57) and STS status after each frame; prints `STS is GOOD`/`STS qual/status FAIL` with the quality index and running good/bad counts. New driver functions `dwt_readstsquality` and `dwt_readstsstatus` return `(result, value)` tuples instead of using output pointers. |
| `examples/ex_01h_simple_tx_pdoa`, `ex_02h_simple_rx_pdoa` | **Skipped for now**: phase-difference-of-arrival needs a receiver with two antennas, and the boards in use have one, so the results could not be checked. |
| `examples/ex_01i_simple_tx_aes`, `ex_02i_simple_rx_aes` | **Verified on hardware**: with matching keys 15+ frames decrypted correctly to `Good, This is the right message` (key and nonce byte order right); with one digit of the key changed on the receiver every frame gave `ERROR AES` (0 good, N bad), so the tag check works. AES-GCM encrypted and authenticated 802.15.8 frame (21-byte clear header, 32-byte encrypted payload, 16-byte tag), done by the DW3000's AES engine. The receiver prints the decrypted payload (upstream does not) and keeps listening after an AES error (upstream exits). New: AES driver functions, `MAC_802_15_8/` helper package. A wrong key or any changed byte of header, payload or tag must give `ERROR AES`. |
| `examples/ex_04a_cont_wave` | Awaiting hardware test (needs a spectrum analyser to see the carrier). Unmodulated carrier on channel 5 for 120 s, then soft reset. Ctrl-C stops it early. |
| `examples/ex_04b_cont_frame` | Awaiting hardware test. Repeats a blink frame about every 1 ms for 120 s, then soft reset; a board running `ex_02a_simple_rx` should see the frames. |
| Remaining examples: `ex_05c/05d` (DS-TWR with STS), `ex_06e/06f` (SS-TWR with AES), `ex_07a/07b/07c` (ACK data), `ex_11a` (SPI CRC), `ex_13a` (GPIO), `ex_14a` (OTP write), `ex_15a/15b` (LE pending) | Not started |

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
7. ~~`ex_02c_rx_diagnostics`~~ — **done**, verified on hardware. (Original note:) Run it with `ex_01a_simple_tx` on the other board. Each good frame prints a `first path idx` line (expect a value near the preamble position, plausibly typically a few hundred samples in; the exact value depends on the setup), `ipatov peak/F1/F2/F3`, and raw accumulator bytes; every event prints the counters (`CRCG` should climb with each received frame). Known upstream quirk kept as-is: the accumulator offset is passed as a byte offset, so accumulator bytes are shown raw, not decoded.
8. ~~`ex_02d_rx_sniff`~~ — **done**, verified on hardware. (Original note:) Run it with `ex_01a_simple_tx` on the other board. Expect frames like ex_02a, but possibly with some missed frames or `receive error:` lines, since the receiver is only on part of the time. Compare how many of the TX's sequence numbers arrive versus ex_02a.
9. ~~`ex_02f_rx_with_crystal_trim`~~ — **done**, verified on hardware. (Original note:) Run it with `ex_01a_simple_tx` on the other board. Each frame prints `seq N  offset +X.XX ppm  trim T`. Your boards' offset was ~0.5 ppm in ex_02c, which is below the 2 ppm minimum, so the first frames should show `(adjusted)` with the trim value dropping by about 3, after which the offset should settle to a magnitude between 2 and 4 ppm and stop adjusting. Only the receiver's crystal is trimmed; the transmitter is untouched.
10. **Run `ex_06b_ss_twr_responder` (first) and `ex_06a_ss_twr_initiator`.** Responder prints `replied (host turnaround N us of ~20500 us allowed)`; if it prints `TOO LATE`, raise `POLL_RX_TO_RESP_TX_DLY_UUS` in BOTH files. Initiator prints `dist X.XX m (offset +Y.YY ppm, uncorrected Z.ZZ m)` once a second. A second line shows the chip-measured round trip in uus (should be about the reply delay, 20000) and how long the host waited. **First hardware run:** responder worked (replies 6-7.6 ms after the poll) but the initiator's 400 uus frame-wait window missed every response (`RXFTO`), so `WINDOW_MARGIN_UUS = 1000` now widens the window by 1 ms each side; the round-trip printout will show the true arrival time so the margin can be narrowed again. Put the boards a known distance apart (e.g. 1 m, antennas facing) and compare. Expect a fixed error from the uncalibrated antenna delay (a few tens of cm up to ~1 m) plus tens of cm of noise from the long reply delay. **Observed so far:** corrected 0.14-0.69 m vs uncorrected 2.4-2.6 m at about -0.7 ppm offset. Noise scales with the reply delay; averaging, a shorter delay (turnaround was 6.3-7.6 ms) or ex_05 reduce it. **Sign check:** the clock-offset correction is large here (about 3 m per ppm of offset at this delay), so if the corrected distance is wildly wrong (metres off) while the `uncorrected` one is close, the offset sign convention needs flipping, which the first run will make obvious.
11. **Deferred:** improve interrupt latency (ex_03d round trip ~8-10 ms vs ~6-7 ms polled), possibly with PIO; measure where the overhead goes first.
12. **Run `ex_05b_ds_twr_responder` (first) and `ex_05a_ds_twr_initiator`.** Files to copy to the boards since the last delivery: `decadriver/deca_regs.py`, `decadriver/deca_device.py`, `shared_data/shared_defines.py`, plus the two new example folders. The responder prints `dist X.XX m` and a line with Ra/Rb/Da/Db (all should be close to 15000 uus plus the flight time) and the host turnaround; the initiator prints `final sent (...)`. If either prints `TOO LATE`, raise the matching delay constant in BOTH files. Compare the spread with ex_06: it should be much smaller, since no clock-offset correction is involved.
13. **Run `ex_01b_tx_sleep`** on one board and `ex_02a_simple_rx` on the other (start the receiver first). Files to copy since the last delivery: `decadriver/deca_regs.py`, `decadriver/deca_device.py`, `platform/port.py`, plus the new example folder. The TX board prints `sent seq N, sleeping 1000 ms` then `woke: IDLE_RC after ~2500 us`; the receiver must see the sequence number go up by exactly one per frame (a frame missing after each wake would mean the config was not restored). Then set `USE_IDLE_RC = True` and repeat (100 ms period).
14. **Run `ex_01c_tx_sleep_auto`** with `ex_02a_simple_rx` on the second board (receiver first). Files to copy since the last delivery: `decadriver/deca_regs.py`, `decadriver/deca_device.py`, plus the new example folder. The TX board prints `TX started seq N; chip will sleep itself, waiting 1000 ms` then `woke: IDLE_RC after ~3600 us`; the receiver must see consecutive sequence numbers. If frames stop after the first, the chip probably failed to sleep or wake (an asserted IRQ line prevents sleep).
15. **Run `ex_01d_tx_timed_sleep`** with `ex_02a_simple_rx` on the second board (receiver first). Files to copy since the last delivery: `decadriver/deca_regs.py`, `decadriver/deca_device.py`, plus the new example folder. First line shows the LP oscillator (expect 15-34 kHz), counter and nominal sleep; then one `frame N sent; slept X ms, Y ms since previous frame` line per cycle. The receiver must see consecutive sequence numbers. If you see `WARNING: chip has not woken`, the wake interrupt never reached the Pico (check the IRQ wire as in ex_03d).
16. **Run `ex_01e_tx_with_cca`** on one board with `ex_02a_simple_rx` on the other (receiver first). Files to copy since the last delivery: `decadriver/deca_regs.py`, plus the new example folder. On a quiet channel it prints `frame N sent (N sent, 0 CCA failures)` every 100 ms and the receiver sees consecutive numbers. A CCA failure only appears if something else is transmitting at the same moment: with only two boards, try running `ex_01a_simple_tx` on the receiver board instead of the receiver (nothing then receives, but the CCA counts show whether the two transmitters ever overlapped). Optional, to see failures reliably: temporarily raise `CCA_PREAMBLE_TIMEOUT_PACS` (longer listening) or shorten `TX_DELAY_MS` on both boards.
17. **Verify `ex_01g_simple_tx_sts_sdc`**: it can run now (the TX board prints `frame sent, next seq N` every 500 ms and the transmission shows up on a spectrum/RF tool if you have one), but it can only be properly verified with its receiver `ex_02g_simple_rx_sts_sdc`, which is the natural next example. Files to copy: `decadriver/deca_device.py` (STS threshold fix), plus the new example folder.
18. **Run `ex_02g_simple_rx_sts_sdc`** (receiver first) with `ex_01g_simple_tx_sts_sdc` on the other board. This also verifies ex_01g. Files to copy since the last delivery: `decadriver/deca_regs.py`, `decadriver/deca_device.py`, plus the new example folder (and ex_01g's folder if not already on the TX board). Expect `len 15: b'A\x88\x00\xca\xdeXRXTDATA\x00\x00' | STS is GOOD (quality ~61-62, status 0x000; N good, 0 bad)` twice a second, with the sequence byte counting up. Quality should be close to 64; if every frame says `FAIL`, paste a few lines: the quality value tells whether the STS was found but weak, or not found.
19. **Run `ex_02i_simple_rx_aes`** (receiver first) with `ex_01i_simple_tx_aes` on the other board. Files to copy since the last delivery: `decadriver/deca_regs.py`, `decadriver/deca_device.py`, `decadriver/deca_types.py`, `shared_data/shared_defines.py`, the new `MAC_802_15_8/` folder (needs its `__init__.py`), plus the two example folders. Expect `AES TX OK: N` on the TX board and `AES RX OK N: b'Good, This is the right message\x00'` on the RX board, twice a second. Then check authentication (done, passed): change one digit of `aes_key` in ex_02i and run again; every frame must now give `ERROR AES`. The desktop tests drive the driver against a toy stand-in for the AES engine (they check offsets, lengths, nonce and key plumbing, not AES itself), so real AES behaviour (byte order of the key and nonce) is only confirmed by this hardware run.
20. **Run `ex_04a_cont_wave` and `ex_04b_cont_frame`** (one at a time; each runs 120 s, Ctrl-C stops early). Files to copy since the last delivery: `decadriver/deca_regs.py`, `decadriver/deca_device.py`, plus the two new example folders. They print `Continuous wave output for 120000 ms` / `Continuous frames for 120000 ms`, then `done`. Without a spectrum analyser, run ex_04b with `ex_02a_simple_rx` on the other board: it should print a blink frame roughly every millisecond. ex_04a has no visible effect without an analyser (carrier at 6489.6 MHz). After either, the chip is soft-reset and the program ends; re-run the board's normal example afterwards.
21. Further examples (ranging `ex_05*`/`ex_06*`) — each pulls in more of `decadriver`
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
