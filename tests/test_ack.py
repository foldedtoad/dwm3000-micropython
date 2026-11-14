import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "mocks"))
sys.path.insert(0, ROOT)
import io, contextlib, importlib.util
import utime
from decadriver import deca_device as dwt
from decadriver import deca_regs as R

# ---- constants ---------------------------------------------------------------
assert R.SYS_CFG_AUTO_ACK_BIT_MASK == 0x800 and R.SYS_CFG_FAST_AAT_EN_BIT_MASK == 0x40000
assert R.RDB_STATUS_ID == 0x10024 and R.PANADR_ID == 0xC and R.ADR_FILT_CFG_ID == 0x14
assert R.RDB_STATUS_CLEAR_BUFF0_EVENTS == 0xF and R.RDB_STATUS_CLEAR_BUFF1_EVENTS == 0xF0

# ---- driver call sequences ----------------------------------------------------
ev = []
def patch():
    ev.clear()
    dwt.dwt_write16bitoffsetreg = lambda a, o, v: ev.append(('w16o', a, o, v))
    dwt.dwt_write8bitoffsetreg = lambda a, o, v: ev.append(('w8o', a, o, v))
    dwt.dwt_write32bitreg = lambda a, v: ev.append(('w32', a, v))
    dwt.dwt_or8bitoffsetreg = lambda a, o, v: ev.append(('or8', a, o, v))
    dwt.dwt_or32bitoffsetreg = lambda a, o, v: ev.append(('or32', a, o, v))
    dwt.dwt_and8bitoffsetreg = lambda a, o, v: ev.append(('and8', a, o, v))
    dwt.dwt_and16bitoffsetreg = lambda a, o, v: ev.append(('and16', a, o, v))
    dwt.dwt_modify32bitoffsetreg = lambda a, o, an, orr: ev.append(('mod32', a, o, an, orr))
    dwt.dwt_writefastCMD = lambda c: ev.append(('fast', c))
patch()
dwt.dwt_setpanid(0xDECA); dwt.dwt_setaddress16(0x5258)
assert ev == [('w16o', R.PANADR_ID, 2, 0xDECA), ('w16o', R.PANADR_ID, 0, 0x5258)], ev
patch(); dwt.dwt_configureframefilter(R.DWT_FF_ENABLE_802_15_4, R.DWT_FF_DATA_EN)
assert ev == [('or8', R.SYS_CFG_ID, 0, R.SYS_CFG_FFEN_BIT_MASK), ('w16o', R.ADR_FILT_CFG_ID, 0, 2)], ev
patch(); dwt.dwt_configureframefilter(0, 0)
assert ev == [('and8', R.SYS_CFG_ID, 0, 0xFE), ('w16o', R.ADR_FILT_CFG_ID, 0, 0)], ev
patch(); dwt.dwt_enableautoack(50, 1)
assert ev == [('w8o', R.ACK_RESP_ID, 3, 50), ('or32', R.SYS_CFG_ID, 0, 0x800 | 0x40000)], ev
patch(); dwt.dwt_enableautoack(0, 0)
assert ev[-1] == ('and16', R.SYS_CFG_ID, 0, 0xF7FF), ev
patch(); dwt.dwt_setdblrxbuffmode(R.DBL_BUF_STATE_EN, R.DBL_BUF_MODE_MAN)
assert dwt.local.dblbuffon == R.DBL_BUFF_ACCESS_BUFFER_0
assert ev[0] == ('w32', R.INDIRECT_ADDR_B_ID, R.BUF1_RX_FINFO >> 16)
assert ev[1] == ('w32', R.ADDR_OFFSET_B_ID, R.BUF1_RX_FINFO & 0xFFFF)
assert ev[2] == ('mod32', R.SYS_CFG_ID, 0, (~0x8 & ~0x400) & 0xFFFFFFFF, 0), ev[2]
patch(); dwt.dwt_setdblrxbuffmode(R.DBL_BUF_STATE_EN, R.DBL_BUF_MODE_AUTO)
assert ev[2] == ('mod32', R.SYS_CFG_ID, 0, (~0x8) & 0xFFFFFFFF, 0x400), ev[2]
patch(); dwt.dwt_setdblrxbuffmode(R.DBL_BUF_STATE_DIS, R.DBL_BUF_MODE_MAN)
assert dwt.local.dblbuffon == R.DBL_BUFF_OFF and ev[-1] == ('mod32', R.SYS_CFG_ID, 0, (~0x400) & 0xFFFFFFFF, 0x8)
# buffer hand-over toggles which buffer the host reads
dwt.local.dblbuffon = R.DBL_BUFF_ACCESS_BUFFER_0
patch(); dwt.dwt_signal_rx_buff_free()
assert ev == [('fast', R.CMD_DB_TOGGLE)] and dwt.local.dblbuffon == R.DBL_BUFF_ACCESS_BUFFER_1
dwt.dwt_signal_rx_buff_free(); assert dwt.local.dblbuffon == R.DBL_BUFF_ACCESS_BUFFER_0
print("  driver sequences ok")

# ---- examples -----------------------------------------------------------------
def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "examples", name, "main.py"))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

def stub(log):
    dwt.dwt_checkidlerc = lambda: True
    dwt.dwt_initialise = lambda m: log.append(('init', m)) or R.DWT_SUCCESS
    dwt.dwt_configure = lambda c: 0
    dwt.dwt_configuretxrf = lambda c: None
    dwt.dwt_setleds = lambda m: None
    dwt.dwt_setlnapamode = lambda m: None
    dwt.dwt_forcetrxoff = lambda: log.append(('off',))
    dwt.dwt_setrxaftertxdelay = lambda v: log.append(('rxdelay', v))
    dwt.dwt_setrxtimeout = lambda v: log.append(('rxto', v))
    dwt.dwt_writetxdata = lambda n, d, o: log.append(('txdata', n, bytes(d), o))
    dwt.dwt_writetxfctrl = lambda n, o, r: log.append(('txfctrl', n, o, r))
    dwt.dwt_starttx = lambda m: log.append(('starttx', m)) or 0
    dwt.dwt_write32bitreg = lambda r, v: log.append(('w32', r, v))
    dwt.dwt_setpanid = lambda v: log.append(('pan', v))
    dwt.dwt_setaddress16 = lambda v: log.append(('addr', v))
    dwt.dwt_configureframefilter = lambda a, b: log.append(('ff', a, b))
    dwt.dwt_enableautoack = lambda d, e: log.append(('autoack', d, e))
    dwt.dwt_rxenable = lambda m: log.append(('rx', m)) or 0

def runex(name):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        load(name).main()
    return buf.getvalue()

# --- ex_07a: first attempt times out, second is ACKed, third ACKed; then Ctrl-C -------
log = []; stub(log)
ack = lambda sn: bytes([0x02, 0x00, sn, 0, 0])
script = iter([R.SYS_STATUS_RXFTO_BIT_MASK, R.SYS_STATUS_RXFCG_BIT_MASK, R.SYS_STATUS_RXFCG_BIT_MASK])
acks = iter([ack(0), ack(1)])
cur = {}
def r32(reg):
    if reg == R.SYS_STATUS_ID:
        try: cur['s'] = next(script)
        except StopIteration: raise KeyboardInterrupt
        return cur['s']
    if reg == R.RX_FINFO_ID: return 5
    return 0
dwt.dwt_read32bitreg = r32
dwt.dwt_readrxdata = lambda n, o: next(acks)
sleeps = []
real_sleep = utime.sleep_ms
utime.sleep_ms = lambda ms: sleeps.append(ms)
out = runex("ex_07a_ack_data_tx")
utime.sleep_ms = real_sleep
assert ('init', R.DWT_DW_IDLE) in log and ('rxdelay', 50) in log and ('rxto', 2200) in log
tx = [l for l in log if l[0] == 'txdata']
assert [t[2][2] for t in tx][:3] == [0, 0, 1], [t[2][2] for t in tx]   # retry keeps the seq#, next frame advances
assert tx[0][1] == 19 and tx[0][2][0] == 0x61
assert [l for l in log if l[0] == 'txfctrl'][0] == ('txfctrl', 21, 0, 0)
assert all(l == ('starttx', R.DWT_START_TX_IMMEDIATE | R.DWT_RESPONSE_EXPECTED) for l in log if l[0] == 'starttx')
assert sleeps.count(200) >= 1 and sleeps.count(1000) == 2, sleeps       # retry delay, then 2 inter-frame delays
assert "no ACK" in out and "frame 0 ACKed" in out and "frame 1 ACKed" in out, out
# a wrong-sequence ACK does not count
log = []; stub(log)
script = iter([R.SYS_STATUS_RXFCG_BIT_MASK]); acks = iter([ack(99)])
dwt.dwt_read32bitreg = r32; dwt.dwt_readrxdata = lambda n, o: next(acks)
utime.sleep_ms = lambda ms: sleeps.append(ms)
out = runex("ex_07a_ack_data_tx"); utime.sleep_ms = real_sleep
assert "no ACK" in out and "ACKed" not in out
print("  ex_07a ok")

# --- ex_07b: frame requesting an ACK, then a frame that does not -----------------------
log = []; stub(log)
frames = iter([bytes([0x61, 0x88, 7]) + bytes(14), bytes([0x41, 0x88, 8]) + bytes(14)])
state = {'phase': 0, 'frames': 0}
def r32b(reg):
    if reg == R.SYS_STATUS_ID:
        state['phase'] += 1
        # per frame: RXFCG (wait loop), then TXFRS if an ACK was requested
        order = [R.SYS_STATUS_RXFCG_BIT_MASK, R.SYS_STATUS_TXFRS_BIT_MASK, R.SYS_STATUS_RXFCG_BIT_MASK]
        if state['phase'] <= len(order): return order[state['phase'] - 1]
        raise KeyboardInterrupt
    if reg == R.RX_FINFO_ID: return 17
    return 0
dwt.dwt_read32bitreg = r32b
dwt.dwt_readrxdata = lambda n, o: next(frames)
out = runex("ex_07b_ack_data_rx")
assert ('pan', 0xDECA) in log and ('addr', 0x5258) in log
assert ('ff', R.DWT_FF_ENABLE_802_15_4, R.DWT_FF_DATA_EN) in log and ('autoack', 0, 1) in log
assert "frame 1: len 17 seq 7 -> ACK sent" in out and "frame 2: len 17 seq 8 (no ACK requested)" in out, out
assert ('w32', R.SYS_STATUS_ID, R.SYS_STATUS_TXFRS_BIT_MASK) in log          # TXFRS cleared after the ACK
print("  ex_07b ok")

# --- ex_07c: buffers alternate A, B, A; ACK waits; buffer released each time -------
log = []; stub(log)
dwt.dwt_setdblrxbuffmode = lambda s, m: log.append(('dbl', s, m))
dwt.dwt_configciadiag = lambda m: log.append(('cia', m))
dwt.dwt_signal_rx_buff_free = lambda: log.append(('free',))
rdb = iter([R.RDB_STATUS_RXFCG0_BIT_MASK, R.RDB_STATUS_RXFCG1_BIT_MASK, R.RDB_STATUS_RXFCG0_BIT_MASK])
def r8(reg, off):
    assert reg == R.RDB_STATUS_ID
    try: return next(rdb)
    except StopIteration: raise KeyboardInterrupt
dwt.dwt_read8bitoffsetreg = r8
dwt.dwt_write8bitoffsetreg = lambda a, o, v: log.append(('w8o', a, o, v))
fin = {R.BUF0_RX_FINFO: 17, R.INDIRECT_POINTER_B_ID: 17}
dwt.dwt_read16bitoffsetreg = lambda a, o: fin[a]
dwt.dwt_read32bitreg = lambda reg: R.SYS_STATUS_TXFRS_BIT_MASK if reg == R.SYS_STATUS_ID else 0
frames = iter([bytes([0x61, 0x88, 1]) + bytes(14), bytes([0x61, 0x88, 2]) + bytes(14), bytes([0x61, 0x88, 3]) + bytes(14)])
dwt.dwt_readrxdata = lambda n, o: next(frames)
out = runex("ex_07c_ack_data_rx_dbl_buff")
assert ('dbl', R.DBL_BUF_STATE_EN, R.DBL_BUF_MODE_MAN) in log and ('autoack', 50, 1) in log and ('cia', R.DW_CIA_DIAG_LOG_MIN) in log
clr = [l[3] for l in log if l[0] == 'w8o']
assert clr == [R.RDB_STATUS_CLEAR_BUFF0_EVENTS, R.RDB_STATUS_CLEAR_BUFF1_EVENTS, R.RDB_STATUS_CLEAR_BUFF0_EVENTS], clr
assert [l[0] for l in log].count('free') == 3
assert "frame 1 (buffer A)" in out and "frame 2 (buffer B)" in out and "frame 3 (buffer A)" in out, out
assert out.count("-> ACK sent") == 3
print("  ex_07c ok")
print("test_ack: ALL PASSED")
