import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "mocks"))
sys.path.insert(0, ROOT)
import io, contextlib, importlib.util
from decadriver import deca_device as dwt
from decadriver import deca_regs as R

assert R.SYS_STATUS_HI_CCA_FAIL_BIT_MASK == 0x1000 and R.SYS_STATUS_HI_ID == 0x48 and R.DWT_START_TX_CCA == 0x20

# driver: CCA TX is a fast command (CCA_TX, or CCA_TX_W4R with a response expected)
cap = []
dwt.writetospi = lambda h, b: cap.append((bytes(h).hex(), bytes(b).hex()))
dwt.dwt_starttx(R.DWT_START_TX_CCA)
dwt.dwt_starttx(R.DWT_START_TX_CCA | R.DWT_RESPONSE_EXPECTED)
assert len(cap) == 2 and all(c[1] == '' for c in cap) and cap[0][0] != cap[1][0], cap
assert cap[0][0] == '%02x' % (0x81 | (R.CMD_CCA_TX << 1)) and cap[1][0] == '%02x' % (0x81 | (R.CMD_CCA_TX_W4R << 1)), cap
print("  fast commands:", cap[0][0], cap[1][0])
# HI status register write for CCA_FAIL: file 0, offset 0x48 -> EAMRW header, 32-bit write of 0x1000
cap.clear(); dwt.dwt_write32bitreg(R.SYS_STATUS_HI_ID, R.SYS_STATUS_HI_CCA_FAIL_BIT_MASK)
# file 0 offset 0x48: addr 0x48<<2 = 0x120, |0x8000 write, 0x40 EAMRW flag -> c1 20; body 0x1000 little endian
assert cap == [('c120', '00100000')], cap
print("  CCA_FAIL clear:", cap)

def load(path):
    spec = importlib.util.spec_from_file_location("ex01e", path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

def run(outcomes, cycles=None):
    """outcomes: per attempt 'ok' (TXFRS), 'fail' (CCA_FAIL) or 'hang'."""
    ev = []
    st = {'i': 0}
    cur = lambda: outcomes[min(st['i'], len(outcomes) - 1)]
    dwt.dwt_checkidlerc = lambda: True
    dwt.dwt_initialise = lambda m: R.DWT_SUCCESS
    dwt.dwt_setleds = lambda m: None
    dwt.dwt_configure = lambda c: 0
    dwt.dwt_configuretxrf = lambda c: None
    dwt.dwt_setpreambledetecttimeout = lambda v: ev.append(('pto', v))
    dwt.dwt_writetxdata = lambda n, d, o: ev.append(('txdata', n, bytes(d), o))
    dwt.dwt_writetxfctrl = lambda n, o, r: ev.append(('txfctrl', n, o, r))
    dwt.dwt_starttx = lambda m: ev.append(('starttx', m)) or R.DWT_SUCCESS
    dwt.dwt_forcetrxoff = lambda: ev.append(('forceoff',))
    def r32(reg):
        o = cur()
        if reg == R.SYS_STATUS_ID: return R.SYS_STATUS_TXFRS_BIT_MASK if o == 'ok' else 0
        if reg == R.SYS_STATUS_HI_ID: return R.SYS_STATUS_HI_CCA_FAIL_BIT_MASK if o == 'fail' else 0
        return 0
    dwt.dwt_read32bitreg = r32
    dwt.dwt_write32bitreg = lambda r, v: ev.append(('w32', r, v))
    import utime
    real = utime.sleep_ms
    def sl(ms):
        if ms >= 100:                       # an inter-attempt delay
            ev.append(('delay', ms))
            st['i'] += 1
            if st['i'] >= len(outcomes): raise KeyboardInterrupt
    utime.sleep_ms = sl
    m = load(os.path.join(ROOT, "examples", "ex_01e_tx_with_cca", "main.py"))
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            m.main()
    finally:
        utime.sleep_ms = real
    return ev, buf.getvalue()

# --- sent, sent -> seq advances, 100 ms between attempts ----------------------
ev, out = run(['ok', 'ok'])
assert ('pto', 3) in ev
tx = [e for e in ev if e[0] == 'txdata']
assert tx[0][1] == 10 and tx[0][2][:2] == b'\xc5\x00' and tx[1][2][1] == 1
assert [e for e in ev if e[0] == 'txfctrl'][0] == ('txfctrl', 12, 0, 0)
assert all(e == ('starttx', R.DWT_START_TX_CCA) for e in ev if e[0] == 'starttx')
assert [e[1] for e in ev if e[0] == 'delay'] == [100, 100]
assert "frame 0 sent (1 sent, 0 CCA failures)" in out and "frame 1 sent (2 sent" in out
assert ('w32', R.SYS_STATUS_ID, R.SYS_STATUS_TXFRS_BIT_MASK) in ev
assert ('w32', R.SYS_STATUS_HI_ID, R.SYS_STATUS_HI_CCA_FAIL_BIT_MASK) not in ev

# --- fail, fail, ok, fail: back-off 400, 401, then reset after a success -------
ev, out = run(['fail', 'fail', 'ok', 'fail'])
assert [e[1] for e in ev if e[0] == 'delay'] == [400, 401, 100, 400], [e for e in ev if e[0] == 'delay']
tx = [e for e in ev if e[0] == 'txdata']
assert [t[2][1] for t in tx] == [0, 0, 0, 1]                         # seq unchanged until a frame is really sent
assert out.count("CCA: preamble detected") == 3 and "backing off 401 ms" in out
# stale CCA_FAIL is cleared on every failure (upstream never clears it)
assert [e for e in ev if e == ('w32', R.SYS_STATUS_HI_ID, R.SYS_STATUS_HI_CCA_FAIL_BIT_MASK)] == [('w32', R.SYS_STATUS_HI_ID, R.SYS_STATUS_HI_CCA_FAIL_BIT_MASK)] * 3
assert "frame 0 sent (1 sent, 2 CCA failures)" in out

# --- neither event: guard fires ---------------------------------------------------
import utime
t = {'now': 0}
utime.ticks_ms = lambda: t.__setitem__('now', t['now'] + 40) or t['now']      # 40 ms per call
ev, out = run(['hang', 'ok'])
assert ('forceoff',) in ev and "WARNING: neither TX done nor CCA fail" in out
print("CCA DRIVER + EX_01E TESTS PASSED")
