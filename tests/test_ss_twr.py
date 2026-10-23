import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "mocks"))
sys.path.insert(0, ROOT)
import io, contextlib, importlib.util, struct, re
from decadriver import deca_device as dwt
from decadriver import deca_regs as R
from shared_data import shared_functions as sf
from shared_data.shared_defines import SPEED_OF_LIGHT, UUS_TO_DWT_TIME

# ---- constants -------------------------------------------------------------
assert R.DX_TIME_ID == 0x2C and R.RX_TIME_0_ID == 0x64 and R.TX_TIME_LO_ID == 0x74
assert UUS_TO_DWT_TIME == 63898 and SPEED_OF_LIGHT == 299702547
assert abs(R.DWT_TIME_UNITS - 15.65e-12) < 0.01e-12

# ---- driver SPI level ------------------------------------------------------
cap = []
dwt.writetospi = lambda h, b: cap.append(('W', bytes(h), bytes(b)))
dwt.readfromspi = lambda h, n: (cap.append(('R', bytes(h), n)), bytes(range(1, n + 1)))[1]

# CIA_CONF (0xE0000: file 0xE, offset 0) 16-bit write: FARW header 0x80|(0xE<<1)=0x9C
cap.clear(); dwt.dwt_setrxantennadelay(16385)
assert cap == [('W', b'\x9c', (16385).to_bytes(2, 'little'))], cap
# TX_ANTD (0x10004: file 1, offset 4): addr=(1<<9)|(4<<2)=0x210; |0x8000 -> 0x8210; +0x40 -> 82? => 0xC2 0x10
cap.clear(); dwt.dwt_settxantennadelay(16385)
assert cap == [('W', b'\xc2\x10', (16385).to_bytes(2, 'little'))], cap
# DX_TIME (0x2C: file 0, offset 0x2C): addr=0x2C<<2=0xB0; 0x80B0|0x40 -> c0 b0
cap.clear(); dwt.dwt_setdelayedtrxtime(0x12345678)
assert cap == [('W', b'\xc0\xb0', bytes.fromhex('78563412'))], cap
# timestamps: 5-byte reads, little-endian ints
cap.clear(); assert dwt.dwt_readrxtimestamp() == bytes([1, 2, 3, 4, 5]) and cap[0][2] == 5
assert sf.get_rx_timestamp_u64() == 0x0504030201 and sf.get_tx_timestamp_u64() == 0x0504030201
assert dwt.dwt_readtxtimestamplo32() == 0x04030201 and dwt.dwt_readrxtimestamplo32() == 0x04030201
dwt.local.dblbuffon = R.DBL_BUFF_ACCESS_BUFFER_1
try: dwt.dwt_readrxtimestamp(); raise SystemExit("expected NotImplementedError")
except NotImplementedError: pass
dwt.local.dblbuffon = R.DBL_BUFF_OFF

# ---- shared_functions pack/unpack -------------------------------------------
buf = bytearray(20)
sf.resp_msg_set_ts(buf, 10, 0x1122334455)           # only low 32 bits, LSB first
assert buf[10:14] == bytes([0x55, 0x44, 0x33, 0x22]) and buf[14] == 0
assert sf.resp_msg_get_ts(buf, 10) == 0x22334455
sf.final_msg_set_ts(buf, 14, 0xFFFFFFFF + 5)
assert sf.final_msg_get_ts(buf, 14) == 4

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

def stub_chip():
    dwt.dwt_checkidlerc = lambda: True
    dwt.dwt_initialise = lambda m: R.DWT_SUCCESS
    dwt.dwt_setleds = lambda m: None
    dwt.dwt_configure = lambda c: 0
    dwt.dwt_configuretxrf = lambda c: None
    dwt.dwt_setlnapamode = lambda m: None

TX_ANT = 16385
POLL = bytes([0x41, 0x88, 0, 0xCA, 0xDE, ord('W'), ord('A'), ord('V'), ord('E'), 0xE0, 0, 0])
RESP = bytes([0x41, 0x88, 0, 0xCA, 0xDE, ord('V'), ord('E'), ord('W'), ord('A'), 0xE1])

# ============================ initiator ======================================
# Model (upstream's convention): rtd_init = 2*tof + rtd_resp*(1 - ratio), ratio = raw/2^26
def run_initiator(distance_m, raw_offset, poll_tx_ts, poll_rx_ts):
    stub_chip()
    log = []
    tof_ticks = distance_m / SPEED_OF_LIGHT / R.DWT_TIME_UNITS
    resp_delay = 20000 * UUS_TO_DWT_TIME
    resp_tx_ts = (poll_rx_ts + resp_delay) & 0xFFFFFFFF
    rtd_resp = (resp_tx_ts - poll_rx_ts) & 0xFFFFFFFF
    rtd_init = round(2 * tof_ticks + rtd_resp * (1 - raw_offset / (1 << 26)))
    resp_rx_ts = (poll_tx_ts + rtd_init) & 0xFFFFFFFF
    resp = bytearray(RESP + bytes(10)); resp[2] = 7
    resp[10:14] = (poll_rx_ts & 0xFFFFFFFF).to_bytes(4, 'little')
    resp[14:18] = resp_tx_ts.to_bytes(4, 'little')
    events = iter([R.SYS_STATUS_RXFCG_BIT_MASK, R.SYS_STATUS_RXFTO_BIT_MASK, R.SYS_STATUS_RXFCG_BIT_MASK])
    frames = iter([bytes(resp), bytes([0x41, 0x88, 0, 0xCA, 0xDE, 1, 2, 3, 4, 0x77]) + bytes(10)])
    def r32(reg):
        if reg == R.SYS_STATUS_ID:
            try: return next(events)
            except StopIteration: raise KeyboardInterrupt
        if reg == R.RX_FINFO_ID: return 20
        return 0
    dwt.dwt_read32bitreg = r32
    dwt.dwt_write32bitreg = lambda r, v: log.append(('w32', r, hex(v)))
    dwt.dwt_setrxantennadelay = lambda v: log.append(('rxant', v))
    dwt.dwt_settxantennadelay = lambda v: log.append(('txant', v))
    dwt.dwt_setrxaftertxdelay = lambda v: log.append(('rxdelay', v))
    dwt.dwt_setrxtimeout = lambda v: log.append(('rxto', v))
    dwt.dwt_writetxdata = lambda n, d, off: log.append(('txdata', n, bytes(d), off))
    dwt.dwt_writetxfctrl = lambda n, off, rng: log.append(('txfctrl', n, off, rng))
    dwt.dwt_starttx = lambda mode: (log.append(('starttx', mode)), R.DWT_SUCCESS)[1]
    dwt.dwt_readrxdata = lambda n, off: next(frames)
    dwt.dwt_readtxtimestamplo32 = lambda: poll_tx_ts
    dwt.dwt_readrxtimestamplo32 = lambda: resp_rx_ts
    dwt.dwt_readclockoffset = lambda: raw_offset
    dwt.dwt_forcetrxoff = lambda: log.append(('forceoff',))
    m = load("ex06a", os.path.join(ROOT, "examples", "ex_06a_ss_twr_initiator", "main.py"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        m.main()
    return log, buf.getvalue(), m, (rtd_init, rtd_resp)

for dist, raw, ptx, prx in ((3.0, 150, 0x10000000, 0x77000000),        # normal
                            (3.0, -150, 0xFFFF0000, 0x00001000),        # TX stamp wraps near 2^32
                            (12.5, 4000, 0xFFFFF000, 0xFFFFFF00),       # big offset, both wrap
                            (0.0, 0, 0x1234, 0x5678)):
    log, out, m, (rtd_i, rtd_r) = run_initiator(dist, raw, ptx, prx)
    got = float(re.search(r"dist (-?[0-9.]+) m", out).group(1))
    assert abs(got - dist) < 0.02, (dist, raw, got, out)
    # the 32-bit float32 formula (as upstream would compute it with these larger
    # round-trip times) is NOT good enough: that is why integers are used.
    f32 = lambda x: struct.unpack('f', struct.pack('f', x))[0]
    naive = f32((f32(rtd_i) - f32(rtd_r) * f32(1 - raw / (1 << 26))) / 2.0) * R.DWT_TIME_UNITS * SPEED_OF_LIGHT
    print("  dist %.2f m raw %+d -> printed %.2f  (naive float32 would give %.2f)" % (dist, raw, got, naive))
ops = [l[0] for l in log]
# setup: antenna delays, RX-after-TX delay = reply delay - 410, timeout 400
assert ('rxant', 16385) in log and ('txant', 16385) in log
assert ('rxdelay', 20000 - 410 - 1000) in log and ('rxto', 400 + 2000) in log   # +/-1000 uus margin
# poll: frame has the right content, ranging bit set, immediate TX with response expected
tx = [l for l in log if l[0] == 'txdata'][0]
assert tx[1] == 12 and tx[2][:2] == b'\x41\x88' and tx[2][9] == 0xE0 and tx[3] == 0 and tx[2][2] == 0
assert [l for l in log if l[0] == 'txfctrl'][0] == ('txfctrl', 12, 0, 1)
assert ('starttx', R.DWT_START_TX_IMMEDIATE | R.DWT_RESPONSE_EXPECTED) in log
assert [l[2] for l in [x for x in log if x[0] == 'txdata']][1][2] == 1       # sequence number incremented
# timeout and unexpected-frame paths
assert "no valid response" in out and "unexpected frame" in out and ops[-1] == 'forceoff', out
assert ('w32', R.SYS_STATUS_ID, hex(R.SYS_STATUS_ALL_RX_TO | R.SYS_STATUS_ALL_RX_ERR)) in log
assert "uncorrected" in out and "round trip" in out and "reply delay 20000 uus" in out
assert "gave up after" in out and "window closes at" in out

# ============================ responder ======================================
def run_responder(poll_rx_ts, starttx_results):
    stub_chip()
    log = []
    events = iter([R.SYS_STATUS_RXFCG_BIT_MASK] * len(starttx_results) +
                  [R.SYS_STATUS_RXPHE_BIT_MASK])
    st = {'tx_done': False}
    def r32(reg):
        if reg == R.SYS_STATUS_ID:
            if st['tx_done']:
                st['tx_done'] = False; return R.SYS_STATUS_TXFRS_BIT_MASK
            try: return next(events)
            except StopIteration: raise KeyboardInterrupt
        if reg == R.RX_FINFO_ID: return 12
        return 0
    dwt.dwt_read32bitreg = r32
    dwt.dwt_write32bitreg = lambda r, v: log.append(('w32', r, hex(v)))
    dwt.dwt_setrxantennadelay = lambda v: None
    dwt.dwt_settxantennadelay = lambda v: None
    dwt.dwt_rxenable = lambda m: log.append(('rxenable', m))
    poll = bytearray(POLL); poll[2] = 33          # sequence number is ignored
    dwt.dwt_readrxdata = lambda n, off: bytes(poll)
    dwt.dwt_readrxtimestamp = lambda: poll_rx_ts.to_bytes(5, 'little')
    dwt.dwt_setdelayedtrxtime = lambda v: log.append(('dx', v))
    dwt.dwt_writetxdata = lambda n, d, off: log.append(('txdata', n, bytes(d), off))
    dwt.dwt_writetxfctrl = lambda n, off, rng: log.append(('txfctrl', n, off, rng))
    res = iter(starttx_results)
    def starttx(mode):
        log.append(('starttx', mode)); r = next(res)
        st['tx_done'] = (r == R.DWT_SUCCESS); return r
    dwt.dwt_starttx = starttx
    dwt.dwt_forcetrxoff = lambda: log.append(('forceoff',))
    m = load("ex06b", os.path.join(ROOT, "examples", "ex_06b_ss_twr_responder", "main.py"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        m.main()
    return log, buf.getvalue()

# 40-bit poll RX stamp with the high byte set, and the sum crossing 2^40 so DX_TIME wraps
for prx in (0x12_FFFF_F000, 0xFF_FFFF_F000):
    log, out = run_responder(prx, [R.DWT_SUCCESS, R.DWT_ERROR])
    dx = [l[1] for l in log if l[0] == 'dx']
    expect_dx = ((prx + 20000 * UUS_TO_DWT_TIME) >> 8) & 0xFFFFFFFF
    assert dx[0] == expect_dx and 0 <= dx[0] <= 0xFFFFFFFF, (hex(dx[0]), hex(expect_dx))
    tx = [l for l in log if l[0] == 'txdata']
    msg = tx[0][2]
    assert tx[0][1] == 20 and msg[9] == 0xE1 and msg[2] == 0                      # first reply: seq 0
    assert int.from_bytes(msg[10:14], 'little') == prx & 0xFFFFFFFF                # poll RX ts (low 32)
    resp_tx_ts = ((expect_dx & 0xFFFFFFFE) << 8) + TX_ANT
    assert int.from_bytes(msg[14:18], 'little') == resp_tx_ts & 0xFFFFFFFF, "resp TX ts"
    assert [l for l in log if l[0] == 'txfctrl'][0] == ('txfctrl', 20, 0, 1)
    assert ('starttx', R.DWT_START_TX_DELAYED) in log
assert "replied" in out and "TOO LATE" in out and "raise POLL_RX_TO_RESP_TX_DLY_UUS" in out, out
assert ('w32', R.SYS_STATUS_ID, hex(R.SYS_STATUS_TXFRS_BIT_MASK)) in log
assert ('w32', R.SYS_STATUS_ID, hex(R.SYS_STATUS_ALL_RX_ERR)) in log and log[-1] == ('forceoff',)
# the failed exchange must NOT advance the sequence number: second reply (after the failure) would be seq 1
log, out = run_responder(0x1000, [R.DWT_ERROR, R.DWT_SUCCESS])
seqs = [l[2][2] for l in log if l[0] == 'txdata']
assert seqs == [0, 0], seqs
print("SS-TWR DRIVER + SHARED + EX_06A/06B TESTS PASSED")
