import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "mocks"))
sys.path.insert(0, ROOT)
import io, contextlib, importlib.util
from decadriver import deca_device as dwt
from decadriver import deca_regs as R
from platform.port import port

assert R.AON_ADDR_ID == 0xA000C and R.AON_RDATA_ID == 0xA0008 and R.AON_WDATA_ID == 0xA0010
assert (R.AON_SLPCNT_LO, R.AON_SLPCNT_HI, R.AON_SLPCNT_CAL_CTRL, R.AON_SLPCNT_CAL_LO, R.AON_SLPCNT_CAL_HI) == (0x102, 0x103, 0x104, 0x10E, 0x10F)
assert R.DWT_SLEEP == 2 and R.XTAL_FREQ_HZ == 38400000
assert (R.AON_CTRL_DCA_ENAB_BIT_MASK, R.AON_CTRL_DCA_WRITE_HI_EN_BIT_MASK,
        R.AON_CTRL_DCA_WRITE_EN_BIT_MASK, R.AON_CTRL_DCA_READ_EN_BIT_MASK) == (0x80, 0x20, 0x10, 0x08)

# ---- AON accessors at SPI level --------------------------------------------
# AON_ADDR 0xA000C: file 0xA offset 0xC: addr (0xA<<9)|(0xC<<2)=0x1430 |0x8000, 0x40 flag -> d4 30
# AON_WDATA 0xA0010: offset 0x10 -> 0x1440 -> d4 40 ; AON_CTRL 0xA0004 -> d4 10 ; AON_RDATA 0xA0008 (read) -> 0x1420
cap = []
dwt.writetospi = lambda h, b: cap.append(('W', bytes(h).hex(), bytes(b).hex()))
dwt.readfromspi = lambda h, n: (cap.append(('R', bytes(h).hex(), n)), bytes([0x5A]) * n)[1]
cap.clear(); dwt.dwt_aon_write(0x102, 0x06)         # sleep counter registers are >= 0x100: WRITE_HI bit set (0x80|0x20|0x10 = 0xB0)
assert cap == [('W', 'd430', '0201'), ('W', 'd440', '06'), ('W', 'd410', 'b0'), ('W', 'd410', '00')], cap
cap.clear(); dwt.dwt_aon_write(0x0FF, 0x01)         # below 0x100: no HI bit
assert cap[2] == ('W', 'd410', '90'), cap
cap.clear(); v = dwt.dwt_aon_read(0x10E)
assert v == 0x5A and cap[0] == ('W', 'd430', '0e01') and cap[1] == ('W', 'd410', '88') and cap[2] == ('W', 'd410', '00'), cap
assert cap[3][0] == 'R' and cap[3][2] == 1, cap

# ---- configuresleepcnt: low byte to 0x102, high byte to 0x103 ----------------
writes = []
dwt.dwt_aon_write = lambda a, d: writes.append((a, d))
dwt.dwt_configuresleepcnt(0x0A06)
assert writes == [(0x102, 0x06), (0x103, 0x0A)], writes
# (restore the real one for later tests)

# ---- calibratesleepcnt: sequence and 16-bit assembly -------------------------
seq = []
dwt.dwt_or8bitoffsetreg = lambda a, o, v: seq.append(('or8', a, o, v))
dwt.dwt_and8bitoffsetreg = lambda a, o, v: seq.append(('and8', a, o, v))
dwt.dwt_aon_write = lambda a, d: seq.append(('aonw', a, d))
dwt.dwt_aon_read = lambda a: (seq.append(('aonr', a)), {0x10E: 0x34, 0x10F: 0x04}[a])[1]
cal = dwt.dwt_calibratesleepcnt()
assert cal == 0x0434 == 1076, cal                                   # 38.4 MHz / 1076 = 35.7 kHz
assert seq == [('or8', R.LDO_CTRL_ID, 0, R.LDO_CTRL_LDO_VDDPLL_EN_BIT_MASK),
               ('aonw', 0x104, 0x00), ('aonw', 0x104, 0x04), ('aonr', 0x10E), ('aonr', 0x10F),
               ('aonw', 0x104, 0x00), ('and8', R.LDO_CTRL_ID, 0, (~R.LDO_CTRL_LDO_VDDPLL_EN_BIT_MASK) & 0xFF)], seq

# ---- the example --------------------------------------------------------------
def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

def run(cal_value=1920, wake=True, irq_present=True, cycles=2, ctrl_c_asleep=False):
    log = {'ev': []}
    ev = log['ev']
    st = {'cb': None, 'n': 0}
    dwt.dwt_checkidlerc = lambda: True
    dwt.dwt_initialise = lambda m: R.DWT_SUCCESS
    dwt.dwt_configure = lambda c: ev.append(('configure',)) or 0
    dwt.dwt_configuretxrf = lambda c: None
    dwt.dwt_calibratesleepcnt = lambda: ev.append(('cal',)) or cal_value
    dwt.dwt_configuresleepcnt = lambda n: ev.append(('sleepcnt', n))
    dwt.dwt_configuresleep = lambda m, w: ev.append(('cfgsleep', m, w))
    dwt.dwt_setcallbacks = lambda *a: (ev.append(('callbacks',) + tuple(x is not None for x in a)), st.update(cb=a[5]))
    dwt.dwt_restoreconfig = lambda: ev.append(('restore',))
    dwt.dwt_writetxdata = lambda n, d, o: ev.append(('txdata', n, bytes(d), o))
    dwt.dwt_writetxfctrl = lambda n, o, r: ev.append(('txfctrl', n, o, r))
    dwt.dwt_starttx = lambda m: ev.append(('starttx', m)) or R.DWT_SUCCESS
    dwt.dwt_read32bitreg = lambda r: R.SYS_STATUS_TXFRS_BIT_MASK
    dwt.dwt_write32bitreg = lambda r, v: ev.append(('w32', r, v))
    dwt.dwt_wakeup_ic = lambda: ev.append(('cswake',))
    dwt.dwt_forcetrxoff = lambda: ev.append(('forceoff',))
    def entersleep(i):
        ev.append(('sleep', i))
        st['n'] += 1
        if st['n'] > cycles:
            raise KeyboardInterrupt           # Ctrl-C while the chip is asleep
        if wake: st['cb'](None)               # the sleep timer fires: SPI-ready callback
    dwt.dwt_entersleep = entersleep
    port.set_dwic_isr = lambda h: ev.append(('isr', h))
    port.clear_dwic_isr = lambda: ev.append(('isr_off',))
    saved_irq = port.irq
    if not irq_present: port.irq = None
    m = load("ex01d", os.path.join(ROOT, "examples", "ex_01d_tx_timed_sleep", "main.py"))
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            m.main()
    finally:
        port.irq = saved_irq
    return ev, buf.getvalue(), m

# cal 1920 -> LP OSC 20 kHz; sleep_cnt = ((990*20000)//1000)>>12 = 19800>>12 = 4 ; nominal 4*4096/20000 = 819 ms
ev, out, m = run(cal_value=1920)
ops = [e[0] for e in ev]
assert ('sleepcnt', 4) in ev, ev
assert "LP oscillator 20000 Hz" in out and "sleep counter 4" in out and "nominal sleep 819 ms" in out, out
# setup order (upstream): clear SPIRDY/RCINIT, install ISR, calibrate, program counter, THEN configure, txrf, cfgsleep, callbacks
w = ev[0]; assert w == ('w32', R.SYS_STATUS_ID, R.SYS_STATUS_RCINIT_BIT_MASK | R.SYS_STATUS_SPIRDY_BIT_MASK), ev[:2]
assert ops.index('isr') < ops.index('cal') < ops.index('sleepcnt') < ops.index('configure') < ops.index('cfgsleep') < ops.index('callbacks')
assert ('cfgsleep', R.DWT_CONFIG, 0x20 | 0x8 | 0x2 | 0x1) in ev          # PRES_SLEEP | WAKE_CSN | SLEEP | SLP_EN
assert ('callbacks', False, False, False, False, False, True) in ev       # only the SPI-ready callback
# each cycle: TX, clear TXFRS, enter sleep (idle, not IDLE_RC), callback restores, next frame
assert ('sleep', R.DWT_DW_IDLE) in ev
i = ops.index('sleep'); assert ops[i - 2:i] == ['starttx', 'w32'] and ops[i + 1] == 'restore'
tx = [e for e in ev if e[0] == 'txdata']
assert tx[0][1] == 12 and tx[0][2][:2] == b'\xc5\x00' and tx[1][2][1] == 1 and tx[2][2][1] == 2   # seq advances after each wake
assert [e for e in ev if e[0] == 'txfctrl'][0] == ('txfctrl', 12, 0, 0)
assert "frame 0 sent" in out and "frame 1 sent" in out and "since previous frame" in out
# Ctrl-C while asleep: stop IRQ handler, wake by CS, force off
assert ops[-4:] == ['isr_off', 'cswake', 'forceoff'][-3:] or ops[-3:] == ['isr_off', 'cswake', 'forceoff'], ops[-4:]
assert "Stopped by user" in out
# callback itself: waits for IDLE_RC, restores, clears `sleeping`
m.sleeping = True; calls = []
dwt.dwt_checkidlerc = lambda: (calls.append('idle'), len(calls) >= 3)[1]
dwt.dwt_restoreconfig = lambda: calls.append('restore')
m.spi_ready_cb(None)
assert calls == ['idle', 'idle', 'idle', 'restore'] and m.sleeping is False
# counter would be zero
ev, out, _ = run(cal_value=38400)            # 1 kHz LP osc: (990*1000//1000)>>12 = 0
assert "Sleep counter would be 0" in out and 'configure' not in [e[0] for e in ev]
# no IRQ pin
ev, out, _ = run(irq_present=False)
assert "needs the DW3000 IRQ line" in out and not ev
print("TIMED SLEEP DRIVER + EX_01D TESTS PASSED")
