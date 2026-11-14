import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "mocks"))
sys.path.insert(0, ROOT)
import io, contextlib, importlib.util
from decadriver import deca_device as dwt
from decadriver import deca_regs as R
from decadriver.deca_types import DwtConfig

assert R.STS_STS_ID == 0x20008 and R.STS_STS_ACC_QUAL_BIT_MASK == 0xFFF
assert R.STS_ACC_CP_QUAL_SIGNTST == 0x800 and R.STS_ACC_CP_QUAL_SIGNEXT == 0xF000

# ---- dwt_readstsquality: threshold 57 for 64 symbols, sign extension ---------------
dwt.local.ststhreshold = 57
def q(raw):
    dwt.dwt_read16bitoffsetreg = lambda reg, off: (assert_args(reg, off), raw)[1]
def assert_args(reg, off):
    assert (reg, off) == (R.STS_STS_ID, 0)
for raw, want_index in ((64, 64), (57, 57), (56, 56), (0x1040, 64),          # upper bits are masked off
                        (0x0800, -2048), (0xFFF, -1)):                        # sign bit 11 -> negative
    q(raw)
    res, idx = dwt.dwt_readstsquality()
    assert idx == want_index and res == want_index - 57, (raw, res, idx)
q(57); assert dwt.dwt_readstsquality()[0] == 0          # exactly 90%: good (>= 0)
q(56); assert dwt.dwt_readstsquality()[0] < 0          # one below: bad

# ---- dwt_readstsstatus --------------------------------------------------------------
seen = []
def rd(reg, off):
    seen.append((reg, off)); return rd.value
dwt.dwt_read16bitoffsetreg = rd
dwt.local.dblbuffon = R.DBL_BUFF_OFF
rd.value = 0x0000; assert dwt.dwt_readstsstatus() == (R.DWT_SUCCESS, 0)
rd.value = 0x0100; assert dwt.dwt_readstsstatus(0) == (R.DWT_ERROR, 2)          # >>7 of 0x0100 = 2
rd.value = 0x0080; assert dwt.dwt_readstsstatus(1) == (R.DWT_ERROR, 1)
assert seen[0] == (R.STS_TOA_HI_ID, 2) and seen[-1] == (R.STS1_TOA_HI_ID, 2), seen
dwt.local.dblbuffon = R.DBL_BUFF_ACCESS_BUFFER_0
try:
    dwt.dwt_readstsstatus(); assert False
except NotImplementedError:
    pass
dwt.local.dblbuffon = R.DBL_BUFF_OFF

# ---- SPI headers of the two reads (fresh driver state) ---------------------------------
import subprocess
code = '''
import sys
sys.path[:0]=[%r, %r]
from decadriver import deca_device as dwt
cap=[]
dwt.readfromspi=lambda h,n:(cap.append((bytes(h).hex(),n)),bytes(n))[1]
dwt.local.ststhreshold=57
dwt.dwt_readstsquality(); dwt.dwt_readstsstatus(0); dwt.dwt_readstsstatus(1)
print(cap)
''' % (os.path.join(HERE, "mocks"), ROOT)
res = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, check=True).stdout.strip()
# STS_STS 0x20008 -> addr 0x420 | 0x4000 flag = 44 20 ; STS_TOA_HI 0xC000C+2 -> addr 0x1838 | 0x4000 = 58 38 ; STS1_TOA_HI 0xC0014+2 (offset 0x16) -> 0x1858 | 0x4000 = 58 58
assert res == "[('4420', 2), ('5838', 2), ('5858', 2)]", res
print("  SPI headers:", res)

# ---- the example ---------------------------------------------------------------------
def load():
    spec = importlib.util.spec_from_file_location("ex02g", os.path.join(ROOT, "examples", "ex_02g_simple_rx_sts_sdc", "main.py"))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

def run(frames):
    """frames: list of ('good', payload, qual, sts_status) or ('err', status_bits)."""
    from decadriver import deca_device as d
    ev = []
    st = {'i': 0}
    d.dwt_checkidlerc = lambda: True
    d.dwt_initialise = lambda m: R.DWT_SUCCESS
    d.dwt_setleds = lambda m: None
    d.dwt_configure = lambda c: ev.append(('configure', c)) or 0
    d.dwt_rxenable = lambda m: ev.append(('rxenable', m))
    def cur(): return frames[st['i']]
    def r32(reg):
        if reg == R.SYS_STATUS_ID:
            if st['i'] >= len(frames): raise KeyboardInterrupt
            f = cur()
            return R.SYS_STATUS_RXFCG_BIT_MASK if f[0] == 'good' else f[1]
        if reg == R.RX_FINFO_ID: return len(cur()[1]) + 2
        return 0
    d.dwt_read32bitreg = r32
    d.dwt_readrxdata = lambda n, off: ev.append(('readrx', n, off)) or cur()[1][:n]
    d.dwt_readstsquality = lambda: ev.append(('q',)) or (cur()[2] - 57, cur()[2])
    d.dwt_readstsstatus = lambda n=0: ev.append(('s', n)) or ((R.DWT_SUCCESS if cur()[3] == 0 else R.DWT_ERROR), cur()[3])
    def w32(reg, v):
        ev.append(('w32', reg, v)); st['i'] += 1
    d.dwt_write32bitreg = w32
    d.dwt_forcetrxoff = lambda: ev.append(('forceoff',))
    m = load()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        m.main()
    return ev, buf.getvalue(), m

PAY = bytes([0x41, 0x88, 0, 0xCA, 0xDE, 0x58, 0x52, 0x58, 0x54]) + b'DATA'
ev, out, m = run([('good', PAY, 64, 0), ('good', PAY, 40, 0), ('good', PAY, 64, 0x002), ('err', R.SYS_STATUS_RXFSL_BIT_MASK)])
cfg = [e for e in ev if e[0] == 'configure'][0][1]
assert cfg.sfdType == 3 and cfg.stsMode == 0x9 and cfg.stsLength == R.DWT_STS_LEN_64
assert [e for e in ev if e[0] == 'readrx'][0] == ('readrx', 13, 0)             # frame minus FCS
assert "STS is GOOD (quality 64, status 0x000; 1 good, 0 bad)" in out, out
assert "STS qual/status FAIL (quality 40, status 0x000; 1 good, 1 bad)" in out, out
assert "STS qual/status FAIL (quality 64, status 0x002; 1 good, 2 bad)" in out, out    # good quality but bad status still fails
assert "receive error: RXFSL" in out and "Stopped by user" in out
assert ('w32', R.SYS_STATUS_ID, R.SYS_STATUS_RXFCG_BIT_MASK) in ev and ('w32', R.SYS_STATUS_ID, R.SYS_STATUS_ALL_RX_ERR) in ev
# STS is read while the frame is still the current one: quality/status come BEFORE the RXFCG clear
ops = [e[0] for e in ev]
i = ops.index('q'); assert ops[i + 1] == 's' and ops[i + 2] == 'w32'
assert ops.count('rxenable') == 5        # one per frame/error, plus the re-arm the test then interrupts
print("STS RX (EX_02G) TESTS PASSED")
