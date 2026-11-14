import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "mocks"))
sys.path.insert(0, ROOT)
import io, contextlib, importlib.util
from decadriver import deca_device as dwt
from decadriver import deca_regs as R

# ---- what dwt_configure does for STS mode 1 + SDC -------------------------------
spec = importlib.util.spec_from_file_location("ex01g", os.path.join(ROOT, "examples", "ex_01g_simple_tx_sts_sdc", "main.py"))
ex = importlib.util.module_from_spec(spec); spec.loader.exec_module(ex)
cfg = ex.config
assert cfg.sfdType == 3 and cfg.stsMode == (R.DWT_STS_MODE_1 | R.DWT_STS_MODE_SDC) == 0x9 and cfg.stsLength == R.DWT_STS_LEN_64

log = []
def rec(name):
    return lambda *a: (log.append((name,) + a), 0)[1]
for n in ('dwt_modify32bitoffsetreg', 'dwt_modify16bitoffsetreg', 'dwt_modify8bitoffsetreg', 'dwt_write8bitoffsetreg',
          'dwt_write16bitoffsetreg', 'dwt_write32bitoffsetreg', 'dwt_or8bitoffsetreg', 'dwt_and8bitoffsetreg',
          'dwt_or16bitoffsetreg', 'dwt_and16bitoffsetreg', 'dwt_or32bitoffsetreg', 'dwt_and32bitoffsetreg',
          'dwt_writefastCMD', 'dwt_setplenfine'):
    setattr(dwt, n, rec(n))
dwt.dwt_read8bitoffsetreg = lambda r, o: 1
dwt.dwt_read16bitoffsetreg = lambda r, o: 0
dwt.dwt_read32bitoffsetreg = lambda r, o: 0
dwt.dwt_read32bitreg = lambda r: 0xFFFFFFFF
dwt.dwt_pll_chx_auto_cal = lambda *a: 0 if True else 0
dwt.local.sleep_mode = 0
try:
    dwt.dwt_configure(cfg)
except Exception as e:                       # (calibration internals are not under test here)
    print("  note: configure stopped after the STS part:", type(e).__name__, e)

sys_cfg = [l for l in log if l[0] == 'dwt_modify32bitoffsetreg' and l[1] == R.SYS_CFG_ID]
assert len(sys_cfg) == 1, sys_cfg
_, _, _, and_mask, or_val = sys_cfg[0]
# CP_SPC (STS mode) bits take mode&0xB = 0x9 : STS mode 1 (value 1) with SDC (bit 3) ...
assert (or_val & R.SYS_CFG_CP_SPC_BIT_MASK) >> R.SYS_CFG_CP_SPC_BIT_OFFSET == 0x1
assert or_val & R.SYS_CFG_CP_SDC_BIT_MASK, hex(or_val)
print("  SYS_CFG: CP_SPC=1, CP_SDC set, or_val=0x%08x" % or_val)
assert and_mask & R.SYS_CFG_CP_SDC_BIT_MASK == 0 and and_mask & R.SYS_CFG_CP_SPC_BIT_MASK == 0     # those fields are cleared first
assert dwt.local.stsconfig == 0x9
# STS length 64 -> STS_CFG0 = 64/8 - 1 = 7
assert ('dwt_write8bitoffsetreg', R.STS_CFG0_ID, 0, 7) in log, [l for l in log if l[0] == 'dwt_write8bitoffsetreg']
# SFD type 3 reaches CHAN_CTRL
ch = [l for l in log if l[0] == 'dwt_write32bitoffsetreg' and l[1] == R.CHAN_CTRL_ID]
assert ch and (ch[0][3] & R.CHAN_CTRL_SFD_TYPE_BIT_MASK) >> R.CHAN_CTRL_SFD_TYPE_BIT_OFFSET == 3, ch
# STS acceptance threshold written (STS_CONFIG_LO upper half): preamble + 64*8? -> long ops table kicked? (<256 symbols => short)
mn = [l for l in log if l[0] == 'dwt_modify16bitoffsetreg' and l[1] == R.STS_CONFIG_LO_ID]
assert len(mn) == 1 and mn[0][2] == 2
assert mn[0][4] == 0x10, hex(mn[0][4])          # 64-symbol STS, PDOA mode 0: the chip default 0x10
print("  STS manual threshold written:", hex(mn[0][4]))
# threshold per STS length (upstream: sqrt(len/16)*2048 factor, x default 0x10, x sqrt(2) in PDOA M0/M1)
g = dwt._get_sts_mnth
F = dwt._STS_LENGTH_FACTORS
assert F == (1024, 1448, 2048, 2896, 4096, 5793, 8192)
assert [g(F[i], R.CIA_MANUALLOWERBOUND_TH_64, 3) for i in range(7)] == [11, 16, 23, 32, 45, 64, 91], [g(F[i], R.CIA_MANUALLOWERBOUND_TH_64, 3) for i in range(7)]
assert [g(F[i], R.CIA_MANUALLOWERBOUND_TH_64, 4) for i in range(7)] == [8, 11, 16, 23, 32, 45, 64]       # PDOA M3: half the accumulated symbols
print("  thresholds by STS length OK")

# ---- the example loop ----------------------------------------------------------
import importlib
def run(cycles=3):
    ev = []
    dwt.dwt_checkidlerc = lambda: True
    dwt.dwt_initialise = lambda m: R.DWT_SUCCESS
    dwt.dwt_setleds = lambda m: None
    dwt.dwt_configure = lambda c: ev.append(('configure', c)) or 0
    dwt.dwt_configuretxrf = lambda c: None
    dwt.dwt_setlnapamode = lambda m: None
    dwt.dwt_writetxdata = lambda n, d, o: ev.append(('txdata', n, bytes(d), o))
    dwt.dwt_writetxfctrl = lambda n, o, r: ev.append(('txfctrl', n, o, r))
    dwt.dwt_starttx = lambda m: ev.append(('starttx', m)) or R.DWT_SUCCESS
    dwt.dwt_read32bitreg = lambda r: R.SYS_STATUS_TXFRS_BIT_MASK
    dwt.dwt_write32bitreg = lambda r, v: ev.append(('w32', r, v))
    dwt.dwt_forcetrxoff = lambda: ev.append(('forceoff',))
    import utime
    n = {'i': 0}
    real = utime.sleep_ms
    def sl(ms):
        if ms == 500:
            n['i'] += 1
            if n['i'] > cycles: raise KeyboardInterrupt
    utime.sleep_ms = sl
    spec = importlib.util.spec_from_file_location("ex01g_b", os.path.join(ROOT, "examples", "ex_01g_simple_tx_sts_sdc", "main.py"))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            m.main()
    finally:
        utime.sleep_ms = real
    return ev, buf.getvalue()

ev, out = run()
assert [e for e in ev if e[0] == 'configure'][0][1].stsMode == 0x9
tx = [e for e in ev if e[0] == 'txdata']
assert tx[0][1] == 15 and tx[0][2][:9] == bytes([0x41, 0x88, 0, 0xCA, 0xDE, 0x58, 0x52, 0x58, 0x54]) and tx[0][2][9:13] == b'DATA'
assert [t[2][2] for t in tx] == [0, 1, 2, 3]                                          # sequence number advances
assert [e for e in ev if e[0] == 'txfctrl'][0] == ('txfctrl', 17, 0, 0)                # 15 bytes + 2 FCS
assert "Stopped by user" in out
print("STS TX (EX_01G) TESTS PASSED")
