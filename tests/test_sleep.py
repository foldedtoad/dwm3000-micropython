import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "mocks"))
sys.path.insert(0, ROOT)
import io, contextlib, importlib.util
from decadriver import deca_device as dwt
from decadriver import deca_regs as R
from platform.port import port

assert R.AON_CTRL_ID == 0xA0004 and R.AON_DIG_CFG_ID == 0xA0000 and R.ANA_CFG_ID == 0xA0014
assert R.AON_CTRL_ARRAY_SAVE_BIT_MASK == 2 and R.BUF1_RX_FINFO == 0x1800E8
assert (R.DWT_CONFIG, R.DWT_PRES_SLEEP, R.DWT_WAKE_WUP, R.DWT_WAKE_CSN, R.DWT_SLP_EN) == (1, 0x20, 0x10, 8, 1)

cap = []
dwt.writetospi = lambda h, b: cap.append(('W', bytes(h).hex(), bytes(b).hex()))
dwt.readfromspi = lambda h, n: (cap.append(('R', bytes(h).hex(), n)), b'\xff' * n)[1]

# configuresleep: AON_DIG_CFG (file 0xA, offset 0) 16-bit write, 1-byte FACRW header 0x80|(0xA<<1)=0x94;
# body = sleep_mode (RUNSAR 0x0002) | DWT_CONFIG 1 = 0x0003, little endian.
dwt.local.sleep_mode = R.DWT_RUNSAR
cap.clear(); dwt.dwt_configuresleep(R.DWT_CONFIG, R.DWT_PRES_SLEEP | R.DWT_WAKE_CSN | R.DWT_WAKE_WUP | R.DWT_SLP_EN)
# ANA_CFG offset 0x14: addr (0xA<<9)|(0x14<<2)=0x1450, |0x8000, EAMRW flag 0x40 in byte0 -> d4 50
assert cap == [('W', '94', '0300'), ('W', 'd450', '39')], cap
assert dwt.local.sleep_mode == 3

# entersleep (default): AON_CTRL offset 4: addr 0x1410 -> d4 10; writes 0 then ARRAY_SAVE (2)
cap.clear(); dwt.dwt_entersleep(R.DWT_DW_IDLE)
assert cap == [('W', 'd410', '00'), ('W', 'd410', '02')], cap
# entersleep IDLE_RC: first clears AINIT2IDLE: SEQ_CTRL byte 1 (offset 9): addr (0x11<<9)|(9<<2)=0x2224 -> e2 24
cap.clear(); dwt.dwt_entersleep(R.DWT_DW_IDLE_RC)
# the chip does the masking itself (SPI AND/OR mode: header low bits 01, body = AND mask 0xFE, OR mask 0x00)
assert cap == [('W', 'e225', 'fe00'), ('W', 'd410', '00'), ('W', 'd410', '02')], cap

# wake: WAKEUP pin pulse when present, CS pulse otherwise
events = []
class Pin:
    def __init__(s, n): s.n = n
    def value(s, v=None):
        if v is not None: events.append((s.n, v))
saved = (port.wakeup, port.cs)
port.wakeup, port.cs = Pin('wakeup'), Pin('cs')
dwt.dwt_wakeup_ic(); assert events == [('wakeup', 1), ('wakeup', 0)], events
events.clear(); port.wakeup = None
dwt.dwt_wakeup_ic(); assert events == [('cs', 0), ('cs', 1)], events
port.wakeup, port.cs = saved

# restoreconfig: LDO_RLOAD, INDIRECT B pointers, then OPS kick, then DGC (PRF64 code 9, channel 5)
log = []
dwt.dwt_write8bitoffsetreg = lambda r, o, v: log.append(('w8', r, o, v))
dwt.dwt_write32bitreg = lambda r, v: log.append(('w32', r, v))
dwt.dwt_modify32bitoffsetreg = lambda r, o, a, v: log.append(('mod32', r, o, hex(v)))
dwt.dwt_configmrxlut = lambda ch: log.append(('lut', ch))
dwt.dwt_read16bitoffsetreg = lambda r, o: 9 << R.CHAN_CTRL_TX_PCODE_BIT_OFFSET
dwt._dwt_prog_ldo_and_bias_tune = lambda: log.append(('bias',))
def chk(local_setup, chan_ctrl, expect_tail, bias=0):
    log.clear()
    dwt.local.bias_tune = bias
    dwt.local.sleep_mode = local_setup[0]; dwt.local.dgc_otp_set = local_setup[1]
    dwt.dwt_read16bitoffsetreg = lambda r, o: chan_ctrl
    dwt.dwt_restoreconfig()
    head = ([('bias',)] if bias else []) + [('w8', R.LDO_RLOAD_ID, 1, R.LDO_RLOAD_VAL_B1),
            ('w32', R.INDIRECT_ADDR_B_ID, 0x18), ('w32', R.ADDR_OFFSET_B_ID, 0x00E8)]
    assert log[:len(head)] == head, log
    assert log[len(head):] == expect_tail, (log, expect_tail)
kick5 = ('mod32', R.OTP_CFG_ID, 0, hex((R.DWT_DGC_SEL_CH5 << R.OTP_CFG_DGC_SEL_BIT_OFFSET) | R.OTP_CFG_DGC_KICK_BIT_MASK))
kick9 = ('mod32', R.OTP_CFG_ID, 0, hex((R.DWT_DGC_SEL_CH9 << R.OTP_CFG_DGC_SEL_BIT_OFFSET) | R.OTP_CFG_DGC_KICK_BIT_MASK))
code9 = 9 << R.CHAN_CTRL_TX_PCODE_BIT_OFFSET
chk((R.DWT_RUNSAR, R.DWT_DGC_LOAD_FROM_SW), code9, [('lut', 5)])                      # software LUT, ch5
chk((R.DWT_RUNSAR, R.DWT_DGC_LOAD_FROM_OTP), code9, [kick5])                          # OTP kick, ch5
chk((R.DWT_RUNSAR, R.DWT_DGC_LOAD_FROM_OTP), code9 | 1, [kick9], bias=0x12)           # ch9 (CHAN_CTRL bit 0) + bias tune
chk((R.DWT_RUNSAR, R.DWT_DGC_LOAD_FROM_OTP), 3 << R.CHAN_CTRL_TX_PCODE_BIT_OFFSET, [])  # PRF16 code: no DGC
ops_long = ('mod32', R.OTP_CFG_ID, 0, hex(R.DWT_OPSET_LONG | R.OTP_CFG_OPS_KICK_BIT_MASK))
ops_scp = ('mod32', R.OTP_CFG_ID, 0, hex(R.DWT_OPSET_SCP | R.OTP_CFG_OPS_KICK_BIT_MASK))
chk((R.DWT_RUNSAR | R.DWT_ALT_OPS | R.DWT_SEL_OPS0, R.DWT_DGC_LOAD_FROM_OTP), code9, [ops_long, kick5])
chk((R.DWT_RUNSAR | R.DWT_ALT_OPS | R.DWT_SEL_OPS1, R.DWT_DGC_LOAD_FROM_OTP), code9, [ops_scp, kick5])

# ---- whole example against stubs -------------------------------------------
def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

def run(use_idle_rc, cycles=2, interrupt_asleep=False):
    importlib.reload(dwt) if False else None
    log = []
    dwt.dwt_checkidlerc = lambda: True
    dwt.dwt_initialise = lambda m: R.DWT_SUCCESS
    dwt.dwt_configure = lambda c: 0
    dwt.dwt_configuretxrf = lambda c: None
    dwt.dwt_setlnapamode = lambda m: log.append(('lnapa', m))
    dwt.dwt_configuresleep = lambda m, w: log.append(('cfgsleep', m, w))
    dwt.dwt_entersleep = lambda i: log.append(('sleep', i))
    dwt.dwt_wakeup_ic = lambda: log.append(('wake',))
    dwt.dwt_restoreconfig = lambda: log.append(('restore',))
    dwt.dwt_writetxdata = lambda n, d, o: log.append(('txdata', n, bytes(d), o))
    dwt.dwt_writetxfctrl = lambda n, o, r: log.append(('txfctrl', n, o, r))
    dwt.dwt_starttx = lambda m: log.append(('starttx', m)) or R.DWT_SUCCESS
    dwt.dwt_read32bitreg = lambda r: R.SYS_STATUS_TXFRS_BIT_MASK
    dwt.dwt_write32bitreg = lambda r, v: log.append(('w32', r, v))
    dwt.dwt_forcetrxoff = lambda: log.append(('forceoff',))
    n = {'sleeps': 0}
    import utime
    real = utime.sleep_ms
    def sl(ms):
        log.append(('sleep_ms', ms))
        if ms >= 100:
            n['sleeps'] += 1
            if n['sleeps'] > cycles: raise KeyboardInterrupt
    utime.sleep_ms = sl
    m = load("ex01b", os.path.join(ROOT, "examples", "ex_01b_tx_sleep", "main.py"))
    m.USE_IDLE_RC = use_idle_rc
    m.TX_DELAY_MS = 100 if use_idle_rc else 1000
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            m.main()
    finally:
        utime.sleep_ms = real
    return log, buf.getvalue()

for idle_rc in (False, True):
    log, out = run(idle_rc)
    ops = [l[0] for l in log]
    assert ('cfgsleep', R.DWT_CONFIG, 0x20 | 0x8 | 0x10 | 0x1) in log
    want = R.DWT_DW_IDLE_RC if idle_rc else R.DWT_DW_IDLE
    assert ('sleep', want) in log and ('sleep_ms', 100 if idle_rc else 1000) in log
    # order within a cycle: TX, clear TXFRS, sleep, delay, wake, [2 ms], restore, next frame
    i = ops.index('sleep')
    assert ops[i - 2:i] == ['starttx', 'w32'], ops
    j = ops.index('wake')
    assert ops[i] == 'sleep' and ops[j] == 'wake' and i < j < ops.index('restore')
    txs = [l for l in log if l[0] == 'txdata']
    assert txs[0][1] == 10 and txs[0][2][:2] == b'\xc5\x00' and txs[1][2][1] == 1   # seq advances after each wake
    assert [l for l in log if l[0] == 'txfctrl'][0] == ('txfctrl', 12, 0, 0)
    assert ('w32', R.SYS_STATUS_ID, R.SYS_STATUS_TXFRS_BIT_MASK) in log
    assert "woke: IDLE_RC after" in out and "sent seq 0" in out and "sent seq 1" in out
    # Ctrl-C arrives during the delay, i.e. while asleep: must wake before forcetrxoff
    assert ops[-3:] == ['wake', 'restore', 'forceoff'] or ops[-4:] == ['sleep_ms', 'wake', 'restore', 'forceoff'] \
        or (ops[-1] == 'forceoff' and 'wake' in ops[-5:]), ops[-6:]
    assert "Stopped by user" in out
print("SLEEP DRIVER + EX_01B TESTS PASSED")

# ============================ ex_01c (auto sleep after TX) ====================
assert R.SEQ_CTRL_ATX2SLP_BIT_MASK == 0x800 and R.SYS_ENABLE_LO_SPIRDY_ENABLE_BIT_MASK == 0x800000
cap = []
dwt.writetospi = lambda h, b: cap.append((bytes(h).hex(), bytes(b).hex()))
# SEQ_CTRL 0x110008: addr (0x11<<9)|(8<<2)=0x2220 | 0x8000, 0x40 flag -> e2; byte1 0x20 | 2 (16-bit AND/OR mode) = 0x22.
# Body = AND mask, OR mask (little endian 16-bit each).
dwt.dwt_entersleepaftertx(1)
dwt.dwt_entersleepaftertx(0)
assert cap == [('e222', 'ffff0008'), ('e222', 'fff70000')], cap

def run_auto(cycles=2, interrupt_during_sleep=True):
    log = []
    dwt.dwt_checkidlerc = lambda: True
    dwt.dwt_initialise = lambda m: R.DWT_SUCCESS
    dwt.dwt_configure = lambda c: 0
    dwt.dwt_configuretxrf = lambda c: None
    dwt.dwt_configuresleep = lambda m, w: log.append(('cfgsleep', m, w))
    dwt.dwt_entersleepaftertx = lambda e: log.append(('autosleep', e))
    dwt.dwt_entersleep = lambda i: log.append(('entersleep', i))
    dwt.dwt_setinterrupt = lambda lo, hi, o: log.append(('setint', lo, hi, o))
    dwt.dwt_wakeup_ic = lambda: log.append(('wake',))
    dwt.dwt_restoreconfig = lambda: log.append(('restore',))
    dwt.dwt_writetxdata = lambda n, d, o: log.append(('txdata', n, bytes(d), o))
    dwt.dwt_writetxfctrl = lambda n, o, r: log.append(('txfctrl', n, o, r))
    dwt.dwt_starttx = lambda m: log.append(('starttx', m)) or R.DWT_SUCCESS
    dwt.dwt_read32bitreg = lambda r: log.append(('r32', r)) or 0x00800001
    dwt.dwt_write32bitreg = lambda r, v: log.append(('w32', r, v))
    dwt.dwt_forcetrxoff = lambda: log.append(('forceoff',))
    n = {'sleeps': 0}
    import utime
    real = utime.sleep_ms
    def sl(ms):
        log.append(('sleep_ms', ms))
        if ms >= 100:
            n['sleeps'] += 1
            if n['sleeps'] > cycles: raise KeyboardInterrupt
    utime.sleep_ms = sl
    m = load("ex01c", os.path.join(ROOT, "examples", "ex_01c_tx_sleep_auto", "main.py"))
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            m.main()
    finally:
        utime.sleep_ms = real
    return log, buf.getvalue()

log, out = run_auto()
ops = [l[0] for l in log]
assert ('cfgsleep', R.DWT_CONFIG, 0x20 | 0x8 | 0x10 | 0x1) in log
# setup order: configure sleep, enable auto sleep, read status, drop the SPIRDY interrupt, clear the events read
a, b = ops.index('cfgsleep'), ops.index('autosleep')
assert a < b < ops.index('setint') < ops.index('w32') and ('autosleep', 1) in log
assert ('setint', R.SYS_ENABLE_LO_SPIRDY_ENABLE_BIT_MASK, 0, R.DWT_DISABLE_INT) in log
assert ('w32', R.SYS_STATUS_ID, 0x00800001) in log            # clears exactly what was read
# frame: full 12 bytes (data + 2 checksum placeholders), as upstream
tx = [l for l in log if l[0] == 'txdata']
assert tx[0][1] == 12 and tx[0][2][:2] == b'\xc5\x00' and tx[1][2][1] == 1
assert [l for l in log if l[0] == 'txfctrl'][0] == ('txfctrl', 12, 0, 0)
# the host must NOT touch the chip between starttx and wake: no register access, no explicit sleep
i = ops.index('starttx'); j = ops.index('wake')
between = ops[i + 1:j]
assert between == ['sleep_ms'], between
assert 'entersleep' not in ops
assert ops[j:j + 3] == ['wake', 'sleep_ms', 'restore'], ops[j:j + 3]      # 2 ms start-up wait, then restore
assert "woke: IDLE_RC after" in out and "TX started seq 0" in out and "TX started seq 1" in out
# Ctrl-C arrives during the delay (chip asleep): wake first, then force off
assert ops[-1] == 'forceoff' and 'wake' in ops[-6:], ops[-6:]
assert "Stopped by user" in out
print("AUTO-SLEEP (EX_01C) TESTS PASSED")
