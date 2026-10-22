import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "mocks"))
sys.path.insert(0, ROOT)
import io, contextlib, importlib.util
from decadriver import deca_device as dwt
from decadriver import deca_regs as R

# ---- constants spot-checked against the header values (hand-read) ----------
assert R.EVC_CTRL_ID == 0xF0000 and R.EVC_COUNT6_ID == 0xF001C
assert R.IP_TOA_LO_ID == 0xC0000 and R.STS_DIAG_4_ID == 0xD0000 and R.CIA_DIAG_1_ID == 0xC0024
assert R.ACC_MEM_ID == 0x150000 and R.ACC_BUFFER_MAX_LEN == 12289 and R.CIA_DIAGNOSTIC_OFF == 0x10

# ---- SPI-level: capture what actually goes on the wire --------------------
cap = []
dwt.writetospi = lambda h, b: cap.append(('W', bytes(h), bytes(b)))
dwt.readfromspi = lambda h, n: (cap.append(('R', bytes(h), n)), bytes(n))[1]

# event counters: clear (0x02) then enable (0x01) into EVC_CTRL (file 0xF, offset 0).
# FARW write header = 0x80 | (file << 1) = 0x9E
cap.clear(); dwt.dwt_configeventcounters(1)
assert cap == [('W', b'\x9e', b'\x02'), ('W', b'\x9e', b'\x01')], cap
cap.clear(); dwt.dwt_configeventcounters(0)
assert cap == [('W', b'\x9e', b'\x02')], cap

# configciadiag(1): AND_OR_8 on CIA_CONF(0xE0000) offset 2 with and=~0x10=0xEF, or=0
# then write RDB_DIAG_MODE (0x10028) = 1>>1 = 0
cap.clear(); dwt.dwt_configciadiag(1)
assert cap[0][0] == 'W' and cap[0][2] == bytes([0xEF, 0x00]), cap[0]
assert cap[1][2] == b'\x00' and dwt.local.cia_diagnostic == 1, cap
# configciadiag(0): or 0x10 (and 0xFF)
cap.clear(); dwt.dwt_configciadiag(0)
assert cap[0][2] == bytes([0xFF, 0x10]), cap[0]
assert dwt.local.cia_diagnostic == 0
dwt.dwt_configciadiag(1)

# accumulator read: clocks forced on, data read, clocks restored
cap.clear(); dwt.dwt_readaccdata(37, 5)
w = [c for c in cap if c[0] == 'W']; r = [c for c in cap if c[0] == 'R']
assert w[0][2] == (0xFFFF).to_bytes(2, 'little') + (0x8040).to_bytes(2, 'little'), w[0]
assert w[-1][2] == (0x7FBF).to_bytes(2, 'little') + (0).to_bytes(2, 'little'), w[-1]
assert len(r) == 1 and r[0][2] == 37 and len(w) == 2, cap
# offset above 127 -> indirect pointer A path (INDIRECT_ADDR_A <- 0x15, ADDR_OFFSET_A <- offset)
cap.clear(); dwt.dwt_readaccdata(7, 200)
assert len([c for c in cap if c[0] == 'R']) == 1 and len([c for c in cap if c[0] == 'W']) == 4, cap
# range check
try:
    dwt.dwt_readaccdata(100, R.ACC_BUFFER_MAX_LEN); raise SystemExit("no range error")
except ValueError:
    pass
assert cap and True
# clocks must be restored even on error
cap.clear()
try: dwt.dwt_readaccdata(100, R.ACC_BUFFER_MAX_LEN)
except ValueError: pass
assert cap[-1][2] == (0x7FBF).to_bytes(2, 'little') + bytes(2), cap

# ---- event counters: field extraction -------------------------------------
regs = {R.EVC_COUNT0_ID: 0x0123_0456,  # RSL=0x123 PHE=0x456
        R.EVC_COUNT1_ID: 0x0ABC_0DEF,  # CRCB=0xABC CRCG=0xDEF
        R.EVC_COUNT2_ID: 0x00_07_00_09,  # OVER=7 ARFE=9
        R.EVC_COUNT3_ID: 0x0111_0222,  # PTO=0x111 SFDTO=0x222
        R.EVC_COUNT4_ID: 0x0333_0044,  # TXF=0x333 RTO=0x44
        R.EVC_COUNT5_ID: 0x00_05_00_06,  # CRCE=5 HPW=6
        R.EVC_COUNT6_ID: 0xF555}       # PREJ=0x555 (upper nibble ignored)
dwt.dwt_read32bitoffsetreg = lambda reg, off: regs[reg]
c = dwt.dwt_readeventcounters()
assert (c.PHE, c.RSL, c.CRCG, c.CRCB) == (0x456, 0x123, 0xDEF, 0xABC)
assert (c.ARFE, c.OVER, c.PTO, c.SFDTO) == (9, 7, 0x111, 0x222)
assert (c.TXF, c.RTO, c.HPW, c.CRCE, c.PREJ) == (0x333, 0x44, 6, 5, 0x555)

# ---- diagnostics parsing against a synthetic register image ----------------
def put(img, addr, base, value, n):
    o = addr - base
    img[o:o + n] = value.to_bytes(n, 'little')
c0 = bytearray(0x6C)      # 0xC0000 space (108 bytes)
d0 = bytearray(0x6C)      # 0xD0000 space
put(c0, R.IP_TOA_LO_ID, 0xC0000, 0x0102030405, 5)
put(c0, R.STS_TOA_LO_ID, 0xC0000, 0x1112131415, 5)
put(c0, R.STS1_TOA_LO_ID, 0xC0000, 0x2122232425, 5)
put(c0, R.CIA_TDOA_0_ID, 0xC0000, 0x060504030201, 6)
c0[R.IP_TOA_HI_ID - 0xC0000 + 3] = 0x7E                      # ipatov status (CIA_I_STAT_OFFSET)
put(c0, R.IP_TOA_HI_ID + 1, 0xC0000, 0xBEEF, 2)               # ipatovPOA
put(c0, R.STS_TOA_HI_ID + 1, 0xC0000, 0x1234, 2)
put(c0, R.STS1_TOA_HI_ID + 1, 0xC0000, 0x5678, 2)
put(c0, R.CIA_TDOA_1_PDOA_ID + 2, 0xC0000, 0x2001, 2)         # pdoa: bit13 set -> negative
put(c0, R.CIA_DIAG_0_ID, 0xC0000, 0xFFFF, 2)                  # xtal masked to 13 bits
put(c0, R.CIA_DIAG_1_ID, 0xC0000, 0xFFFFFFFF, 4)              # masked to 29 bits
put(c0, R.IP_DIAG_0_ID, 0xC0000, 0xFFFFFFFF, 4)
put(c0, R.IP_DIAG_1_ID, 0xC0000, 0xFFFFFFFF, 4)
put(c0, R.IP_DIAG_2_ID, 0xC0000, 0xFFFFFFFF, 4)
put(c0, R.IP_DIAG_3_ID, 0xC0000, 0x00123456, 4)
put(c0, R.IP_DIAG_4_ID, 0xC0000, 0x00254321, 4)
put(c0, R.IP_DIAG_8_ID, 0xC0000, 0x1A40, 2)                   # fp index 0x1A40 -> int 105
put(c0, R.IP_DIAG_12_ID, 0xC0000, 0xF0A5, 2)                  # accum count masked 12 bits -> 0x0A5
put(c0, R.STS_DIAG_0_ID, 0xC0000, 0xFFFFFFFF, 4)
put(c0, R.STS_DIAG_1_ID, 0xC0000, 0xABCD, 2)
put(c0, R.STS_DIAG_2_ID, 0xC0000, 0x3FFFFF, 4)
put(c0, R.STS_DIAG_3_ID, 0xC0000, 0x000002, 4)
put(d0, R.STS_DIAG_4_ID, 0xD0000, 0x000003, 4)
put(d0, R.STS_DIAG_8_ID, 0xD0000, 0xFFFF, 2)                  # masked 15 bits
put(d0, R.STS_DIAG_12_ID, 0xD0000, 0xFFFF, 2)                 # masked 12 bits
put(d0, R.STS1_DIAG_0_ID, 0xD0000, 0xFFFFFFFF, 4)
put(d0, R.STS1_DIAG_1_ID, 0xD0000, 0x4321, 2)
put(d0, R.STS1_DIAG_4_ID, 0xD0000, 0x00FFFFFF, 4)

reads = []
def fake_read(reg, idx, length):
    reads.append((reg, idx, length))
    if reg == R.IP_TOA_LO_ID: return bytes(c0[:length])
    if reg == R.STS_DIAG_4_ID: return bytes(d0[:length])
    raise AssertionError(reg)
dwt.dwt_readfromdevice = fake_read

dwt.local.cia_diagnostic = 1
dg = dwt.dwt_readdiagnostics()
assert reads == [(0xC0000, 0, 108), (0xD0000, 0, 108)], reads
assert dg.ipatovRxTime == bytes([5, 4, 3, 2, 1]) and dg.stsRxTime[0] == 0x15 and dg.sts2RxTime[4] == 0x21
assert dg.tdoa == bytes([1, 2, 3, 4, 5, 6])
assert dg.ipatovRxStatus == 0x7E and dg.ipatovPOA == 0xBEEF and dg.stsPOA == 0x1234 and dg.sts2POA == 0x5678
assert dg.pdoa == 0x2001 - 0x4000 and dg.pdoa < 0               # sign-extended int16
assert dg.xtalOffset == 0x1FFF and dg.ciaDiag1 == 0x1FFFFFFF
assert dg.ipatovPeak == 0x7FFFFFFF and dg.ipatovPower == 0x1FFFF and dg.ipatovF1 == 0x3FFFFF
assert dg.ipatovF2 == 0x123456 and dg.ipatovF3 == 0x254321
assert dg.ipatovFpIndex == 0x1A40 and dg.ipatovAccumCount == 0x0A5
assert dg.stsPeak == 0x3FFFFFFF and dg.stsPower == 0xABCD and dg.stsF1 == 0x3FFFFF and dg.stsF2 == 2
assert dg.stsF3 == 3 and dg.stsFpIndex == 0x7FFF and dg.stsAccumCount == 0xFFF
assert dg.sts2Peak == 0x3FFFFFFF and dg.sts2Power == 0x4321 and dg.sts2F3 == 0x3FFFFF

# reduced logging: only the 40-byte block and the common fields
reads.clear(); dwt.local.cia_diagnostic = 0
dg = dwt.dwt_readdiagnostics()
assert reads == [(0xC0000, 0, 40)], reads
assert dg.xtalOffset == 0x1FFF and dg.ipatovPeak == 0 and dg.stsF3 == 0

# double-buffer not ported
dwt.local.dblbuffon = R.DBL_BUFF_ACCESS_BUFFER_1
try: dwt.dwt_readdiagnostics(); raise SystemExit("expected NotImplementedError")
except NotImplementedError: pass
dwt.local.dblbuffon = R.DBL_BUFF_OFF

# ---- whole example against stubs -------------------------------------------
def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

log = []
OK = R.SYS_STATUS_RXFCG_BIT_MASK
events = iter([0, OK, R.SYS_STATUS_RXPHE_BIT_MASK, OK])
st = {'n': 0}
def r32(reg):
    if reg == R.SYS_STATUS_ID:
        try: return next(events)
        except StopIteration: raise KeyboardInterrupt
    if reg == R.RX_FINFO_ID: return 15
    return 0
dwt.dwt_read32bitreg = r32
dwt.dwt_write32bitreg = lambda r, v: log.append(('clear', hex(v)))
dwt.dwt_checkidlerc = lambda: True
dwt.dwt_initialise = lambda m: R.DWT_SUCCESS
dwt.dwt_configure = lambda c: 0
dwt.dwt_configeventcounters = lambda e: log.append(('evc', e))
dwt.dwt_configciadiag = lambda e: log.append(('ciadiag', e))
dwt.dwt_rxenable = lambda m: log.append(('rxenable', m))
dwt.dwt_readrxdata = lambda n, off: (log.append(('readrx', n, off)), bytes(range(n)))[1]
from decadriver.deca_types import DwtRxDiag
def rd(diag=None):
    d = DwtRxDiag(); d.ipatovFpIndex = (1 << 6) | 32     # int part 1 -> offset must clamp to 0
    log.append(('readdiag',)); return d
dwt.dwt_readdiagnostics = rd
dwt.dwt_readaccdata = lambda n, off: (log.append(('readacc', n, off)), bytes(n))[1]
dwt.dwt_readeventcounters = lambda c=None: (log.append(('readevc',)), dwt.DwtDeviceEntCnts())[1]
dwt.dwt_forcetrxoff = lambda: log.append(('forceoff',))

m = load("ex02c", os.path.join(ROOT, "examples", "ex_02c_rx_diagnostics", "main.py"))
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    m.main()
out = buf.getvalue()
ops = [l[0] for l in log]
assert log[0] == ('evc', 1) and log[1] == ('ciadiag', 1), log[:2]
assert ops.count('rxenable') == 4 and ops.count('readevc') == 3          # counters after every event
assert ops.count('readdiag') == 2 and ops.count('readacc') == 2           # only after good frames
assert ('readacc', 37, 0) in log and ('readrx', 15, 0) in log
assert ('clear', hex(R.SYS_STATUS_RXFCG_BIT_MASK)) in log and ('clear', hex(R.SYS_STATUS_ALL_RX_ERR)) in log
assert ops[-1] == 'forceoff' and "rx error" in out and "first path idx 1.50" in out and "Stopped by user" in out, out
print("DIAGNOSTICS DRIVER + EX_02C TESTS PASSED")
