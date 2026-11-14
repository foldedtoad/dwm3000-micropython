import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "mocks"))
sys.path.insert(0, ROOT)
import io, contextlib, importlib.util
from decadriver import deca_device as dwt
from decadriver import deca_regs as R

assert R.TXRXSWITCH_TX == 0x01011100 and R.TXRXSWITCH_AUTO == 0x1C000000
assert R.RF_ENABLE_ID == 0x70000 and R.RF_CTRL_MASK_ID == 0x70004 and R.TX_TEST_ID == 0x70028
assert R.PG_TEST_ID == 0x80018 and R.TEST_CTRL0_ID == 0xF0024 and R.LDO_CTRL_ID == 0x70048

ev = []
def patch():
    ev.clear()
    dwt.dwt_or32bitoffsetreg = lambda a, o, v: ev.append(('or32', a, o, v))
    dwt.dwt_or8bitoffsetreg = lambda a, o, v: ev.append(('or8', a, o, v))
    dwt.dwt_write32bitoffsetreg = lambda a, o, v: ev.append(('w32o', a, o, v))
    dwt.dwt_write32bitreg = lambda a, v: ev.append(('w32', a, v))
    dwt.dwt_write16bitoffsetreg = lambda a, o, v: ev.append(('w16o', a, o, v))
    dwt.dwt_write8bitoffsetreg = lambda a, o, v: ev.append(('w8o', a, o, v))
patch()

# CW mode, channel 5: TX LDOs, RF enable (with CH5), TX switch, manual blocks, forced clocks, CW regs
dwt.dwt_configcwmode(5)
ch5 = (R.RF_ENABLE_TX_SW_EN_BIT_MASK | R.RF_ENABLE_TX_CH5_BIT_MASK | R.RF_ENABLE_TX_EN_BIT_MASK |
       R.RF_ENABLE_TX_EN_BUF_BIT_MASK | R.RF_ENABLE_TX_BIAS_EN_BIT_MASK)
assert ('or32', R.RF_ENABLE_ID, 0, ch5) in ev and ('or32', R.RF_CTRL_MASK_ID, 0, ch5) in ev
assert ('w32o', R.RF_SWITCH_CTRL_ID, 0, R.TXRXSWITCH_TX) in ev
assert ('w32o', R.PWR_UP_TIMES_LO_ID, 2, R.PMSC_TXFINESEQ_DISABLE) in ev
assert ev[-2:] == [('w32o', R.TX_TEST_ID, 0, 0x8), ('w32o', R.PG_TEST_ID, 0, 0xF)], ev[-2:]
assert any(e[0] == 'w16o' and e[1] == R.CLK_CTRL_ID for e in ev)
assert ev.index(('or32', R.RF_ENABLE_ID, 0, ch5)) < [e[0] for e in ev].index('w16o')
print("  CW mode ch5 call sequence ok")

# channel 9 has no CH5 bit
patch(); dwt.dwt_configcwmode(9)
ch9 = ch5 & ~R.RF_ENABLE_TX_CH5_BIT_MASK
assert ('or32', R.RF_ENABLE_ID, 0, ch9) in ev and ('or32', R.RF_CTRL_MASK_ID, 0, ch9) in ev
print("  CW mode ch9 ok")

# continuous frame
patch(); dwt.dwt_configcontinuousframemode(249600, 5)
assert ev[-2:] == [('or8', R.TEST_CTRL0_ID, 0, R.TEST_CTRL0_TX_PSTM_BIT_MASK), ('w32', R.DX_TIME_ID, 249600)]
patch(); dwt.dwt_repeated_frames(1)
assert ev[-1] == ('w32', R.DX_TIME_ID, 4)
print("  continuous frame ok")

# soft reset
patch(); dwt.dwt_softreset()
assert ev[0] == ('w16o', R.AON_DIG_CFG_ID, 0, 0)
assert ev[-1] == ('w8o', R.SOFT_RST_ID, 0, R.DWT_RESET_ALL)
assert ('or8', R.CLK_CTRL_ID, 0, 3) in ev
assert dwt.local.sleep_mode == 0
print("  soft reset ok")

# --- the two examples --------------------------------------------------------
import utime
def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "examples", name, "main.py"))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

def run(name):
    log = []
    dwt.dwt_checkidlerc = lambda: True
    dwt.dwt_initialise = lambda m: R.DWT_SUCCESS
    dwt.dwt_configure = lambda c: 0
    dwt.dwt_configuretxrf = lambda c: None
    dwt.dwt_configcwmode = lambda ch: log.append(('cw', ch))
    dwt.dwt_configcontinuousframemode = lambda p, ch: log.append(('cf', p, ch))
    dwt.dwt_writetxdata = lambda n, d, o: log.append(('data', n, bytes(d), o))
    dwt.dwt_writetxfctrl = lambda n, o, r: log.append(('fctrl', n, o, r))
    dwt.dwt_starttx = lambda m: log.append(('starttx', m)) or 0
    dwt.dwt_softreset = lambda: log.append(('reset',))
    t = {'now': 0}
    utime.ticks_ms = lambda: t['now']
    utime.sleep_ms = lambda ms: t.__setitem__('now', t['now'] + (ms if ms >= 100 else 0) + 100000 * (ms >= 100))
    m = load(name)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        m.main()
    return log, buf.getvalue()

log, out = run("ex_04a_cont_wave")
assert log == [('cw', 5), ('reset',)], log
assert "done" in out
log, out = run("ex_04b_cont_frame")
assert log[0] == ('cf', 249600, 5) and log[1][:2] == ('data', 12) and log[1][2][:2] == b'\xc5\x00'
assert log[2] == ('fctrl', 12, 0, 0) and log[3] == ('starttx', R.DWT_START_TX_IMMEDIATE) and log[-1] == ('reset',)
print("  examples ok")
print("test_cont: ALL PASSED")
