import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "mocks"))
sys.path.insert(0, ROOT)
import io, contextlib, importlib.util
from decadriver import deca_device as dwt
from decadriver import deca_regs as R

# constants (hand-read from the headers)
assert R.RX_SNIFF_ID == 0x11001A and R.RX_SNIFF_SNIFF_OFF_BIT_MASK == 0xFF00 and R.RX_SNIFF_SNIFF_ON_BIT_MASK == 0xF
assert R.GPIO_PIN6_EXTRX == 0x40000 and R.GPIO_PIN4_EXTDA == 0x1000 and R.GPIO_PIN5_EXTTX == 0x8000
assert R.GPIO_PIN0_EXTTXE == 0x2 and R.GPIO_PIN1_EXTRXE == 0x10

cap = []
dwt.writetospi = lambda h, b: cap.append(('W', bytes(h), bytes(b)))
dwt.readfromspi = lambda h, n: (cap.append(('R', bytes(h), n)), bytes(n))[1]

# sniff on: on=2, off=16 -> 0x1002 little-endian = 02 10, written to file 0x11 offset 0x1A.
# Offset != 0 -> 2-byte header: byte0 = 0x80 | (0x11 << 1) | 1 (extended), byte1 = (0x1A << 2).
cap.clear(); dwt.dwt_setsniffmode(1, 2, 16)
assert len(cap) == 1 and cap[0][2] == b'\x02\x10', cap
h = cap[0][1]; print("sniff header:", h.hex())
assert len(h) == 2 and h == b'\xe2\x68', h.hex()
# out-of-range values are masked: on 0x1F -> 0xF, off 0x1FF -> 0xFF
cap.clear(); dwt.dwt_setsniffmode(1, 0x1F, 0x1FF)
assert cap[0][2] == b'\x0f\xff', cap
# disable writes zero
cap.clear(); dwt.dwt_setsniffmode(0, 5, 5)
assert cap[0][2] == b'\x00\x00', cap

# LNA/PA: clears GPIO0/1/4/5/6 fields (0x1FF03F) preserving the rest
for start, mode, expect in (
        (0xFFFFFFFF, 0,                                      0xFFE00FC0),
        (0xFFFFFFFF, R.DWT_LNA_ENABLE | R.DWT_PA_ENABLE,     0xFFE49FC0),
        (0x00000000, R.DWT_LNA_ENABLE,                       0x00040000),
        (0x00000000, R.DWT_TXRX_EN,                          0x00000012),
        (0x12345678, 0,                                      0x12345678 & ~0x1FF03F & 0xFFFFFFFF)):
    dwt.dwt_read32bitreg = lambda reg, v=start: v
    wr = []
    dwt.dwt_write32bitreg = lambda reg, v: wr.append((reg, v))
    dwt.dwt_setlnapamode(mode)
    assert wr == [(R.GPIO_MODE_ID, expect)], (hex(start), mode, [(hex(a), hex(b)) for a, b in wr])

# ---- whole example against stubs -------------------------------------------
def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

log = []
OK = R.SYS_STATUS_RXFCG_BIT_MASK
events = iter([OK, R.SYS_STATUS_RXPHE_BIT_MASK])
def r32(reg):
    if reg == R.SYS_STATUS_ID:
        try: return next(events)
        except StopIteration: raise KeyboardInterrupt
    if reg == R.RX_FINFO_ID: return 14
    return 0
dwt.dwt_read32bitreg = r32
dwt.dwt_write32bitreg = lambda r, v: log.append(('clear', hex(v)))
dwt.dwt_checkidlerc = lambda: True
dwt.dwt_initialise = lambda m: R.DWT_SUCCESS
dwt.dwt_configure = lambda c: (log.append(('configure',)), 0)[1]
dwt.dwt_setlnapamode = lambda m: log.append(('lnapa', m))
dwt.dwt_setsniffmode = lambda e, on, off: log.append(('sniff', e, on, off))
dwt.dwt_rxenable = lambda m: log.append(('rxenable', m))
dwt.dwt_readrxdata = lambda n, off: (log.append(('readrx', n, off)), bytes(range(n)))[1]
dwt.dwt_forcetrxoff = lambda: log.append(('forceoff',))

m = load("ex02d", os.path.join(ROOT, "examples", "ex_02d_rx_sniff", "main.py"))
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    m.main()
out = buf.getvalue()
ops = [l[0] for l in log]
# order: lna/pa (before configure, as upstream), configure, then sniff, then RX
assert ops[:3] == ['lnapa', 'configure', 'sniff'], ops[:4]
assert log[0] == ('lnapa', R.DWT_LNA_ENABLE | R.DWT_PA_ENABLE) and log[2] == ('sniff', 1, 2, 16)
assert ('readrx', 14, 0) in log                   # whole frame, FCS included (as upstream 02d)
assert ('clear', hex(OK)) in log and ('clear', hex(R.SYS_STATUS_ALL_RX_ERR)) in log
assert "len 14:" in out and "receive error: RXPHE" in out and ops[-1] == 'forceoff', out
print("SNIFF DRIVER + EX_02D TESTS PASSED")
