import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "mocks"))
sys.path.insert(0, ROOT)
import io, contextlib, importlib.util
from decadriver import deca_device as dwt
from decadriver import deca_regs as R

assert R.XTAL_ID == 0x90014 and R.XTAL_TRIM_BIT_MASK == 0x3F and R.CIA_DIAG_0_ID == 0xC0020
assert abs(R.CLOCK_OFFSET_PPM_TO_RATIO - 1 / 67108864) < 1e-18

cap = []
dwt.writetospi = lambda h, b: cap.append(('W', bytes(h), bytes(b)))
dwt.readfromspi = lambda h, n: (cap.append(('R', bytes(h), n)), bytes(n))[1]

# setxtaltrim: XTAL_ID = file 9, offset 0x14 -> addr (9<<9)|(0x14<<2) = 0x1250;
# |0x8000 = 0x9250; EAMRW 0x40 in byte0 -> d2 50. Body = value masked to 0x3F.
cap.clear(); dwt.dwt_setxtaltrim(0x2E)
assert cap == [('W', b'\xd2\x50', b'\x2e')], cap
assert dwt.dwt_getxtaltrim() == 0x2E
cap.clear(); dwt.dwt_setxtaltrim(0xFF)           # masked to 6 bits
assert cap[0][2] == b'\x3f' and dwt.dwt_getxtaltrim() == 0x3F

# readclockoffset: CIA_DIAG_0 low 13 bits, bit 12 = sign
def with_reg(v):
    dwt.dwt_read16bitoffsetreg = lambda reg, off: (reg == R.CIA_DIAG_0_ID and off == 0) and v or (_ for _ in ()).throw(AssertionError(reg))
for raw, expect in ((0x0024, 36), (0x0000, 0), (0x0FFF, 4095), (0x1000, -4096), (0x1FFF, -1),
                    (0xE024, 36),            # upper 3 bits ignored
                    (0x1FFC, -4)):
    dwt.dwt_read16bitoffsetreg = lambda reg, off, v=raw: v
    assert dwt.dwt_readclockoffset() == expect, (hex(raw), dwt.dwt_readclockoffset(), expect)
dwt.local.dblbuffon = R.DBL_BUFF_ACCESS_BUFFER_1
try: dwt.dwt_readclockoffset(); raise SystemExit("expected NotImplementedError")
except NotImplementedError: pass
dwt.local.dblbuffon = R.DBL_BUFF_OFF

# ---- whole example against stubs -------------------------------------------
def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

def run_example(start_trim, offsets):
    log = []
    OK = R.SYS_STATUS_RXFCG_BIT_MASK
    ev = iter([OK] * len(offsets) + [R.SYS_STATUS_RXPHE_BIT_MASK])
    offs = iter(offsets)
    def r32(reg):
        if reg == R.SYS_STATUS_ID:
            try: return next(ev)
            except StopIteration: raise KeyboardInterrupt
        if reg == R.RX_FINFO_ID: return 14
        return 0
    dwt.dwt_read32bitreg = r32
    dwt.dwt_write32bitreg = lambda r, v: log.append(('clear', hex(v)))
    dwt.dwt_checkidlerc = lambda: True
    dwt.dwt_initialise = lambda m: R.DWT_SUCCESS
    dwt.dwt_setleds = lambda m: None
    dwt.dwt_configure = lambda c: 0
    dwt.dwt_getxtaltrim = lambda: start_trim
    dwt.dwt_setxtaltrim = lambda v: log.append(('trim', v))
    dwt.dwt_readclockoffset = lambda: next(offs)
    dwt.dwt_rxenable = lambda m: log.append(('rxenable', m))
    seq = {'n': 0}
    def rd(n, off):
        seq['n'] += 1; return bytes([0xC5, seq['n'] & 0xFF]) + bytes(n - 2)
    dwt.dwt_readrxdata = rd
    dwt.dwt_forcetrxoff = lambda: log.append(('forceoff',))
    m = load("ex02f", os.path.join(ROOT, "examples", "ex_02f_rx_with_crystal_trim", "main.py"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        m.main()
    return log, buf.getvalue()

# Raw 36 -> 0.536 ppm (<2, adjust): delta=(3+0.536)*64/77=2.939 -> 46-2.939=43.06 -> 43
# Raw 200 -> 2.98 ppm and -200 -> -2.98 ppm: inside 2..4, no adjustment
# Raw 400 -> 5.96 ppm (>4, adjust): delta=(3+5.96)*64/77=7.448 -> 43-7.448=35.55 -> 35
log, out = run_example(46, [36, 200, -200, 400])
trims = [l[1] for l in log if l[0] == 'trim']
assert trims == [43, 35], (trims, out)
assert out.count("(adjusted)") == 2 and "initial xtal trim: 46" in out
assert "offset +0.54 ppm" in out and "offset -2.98 ppm" in out and "seq 1 " in out, out
assert [l[0] for l in log][-1] == 'forceoff'
assert ('clear', hex(R.SYS_STATUS_RXFCG_BIT_MASK)) in log and ('clear', hex(R.SYS_STATUS_ALL_RX_ERR)) in log

# C semantics: negative result truncates toward zero then wraps in 6 bits.
# trim 1, offset 0.54 ppm -> 1-2.939=-1.939 -> trunc -1 -> &0x3F = 63
log, out = run_example(1, [36])
assert [l[1] for l in log if l[0] == 'trim'] == [63], (log, out)
# negative offset below range (|-1| ppm < 2): delta=(3-1)*0.8312=1.66 -> 10-1.66=8.34 -> 8
log, out = run_example(10, [-67])        # -67/2^26*1e6 = -0.998 ppm
assert [l[1] for l in log if l[0] == 'trim'] == [8], (log, out)
print("XTAL TRIM DRIVER + EX_02F TESTS PASSED")
