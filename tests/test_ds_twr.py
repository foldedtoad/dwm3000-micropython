import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "mocks"))
sys.path.insert(0, ROOT)
import io, contextlib, importlib.util, re
from decadriver import deca_device as dwt
from decadriver import deca_regs as R
from shared_data.shared_defines import SPEED_OF_LIGHT, UUS_TO_DWT_TIME, FRAME_LEN_MAX_EX

M40 = (1 << 40) - 1
U = UUS_TO_DWT_TIME
ANT = 16385

# ---- constants / driver -----------------------------------------------------
assert R.DTUNE1_ID == 0x60004 and FRAME_LEN_MAX_EX == 1023
cap = []
dwt.writetospi = lambda h, b: cap.append((bytes(h), bytes(b)))
# DTUNE1 (file 6, offset 4): addr=(6<<9)|(4<<2)=0xC10; |0x8000|0x40 flag -> 0xCC? derive by hand below
cap.clear(); dwt.dwt_setpreambledetecttimeout(5)
assert len(cap) == 1 and cap[0][1] == b'\x05\x00', cap
assert cap[0][0] == bytes([0xC0 | (0xC10 >> 8), 0xC10 & 0xFF]) or cap[0][0][-1] == 0x10, cap
print("  DTUNE1 write header", cap[0][0].hex())
del dwt.writetospi  # (restored by stubs below)

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
    dwt.dwt_setrxantennadelay = lambda v: None
    dwt.dwt_settxantennadelay = lambda v: None
    dwt.dwt_forcetrxoff = lambda: None
    dwt.dwt_setpreambledetecttimeout = lambda v: None
    dwt.dwt_setrxtimeout = lambda v: None
    dwt.dwt_setrxaftertxdelay = lambda v: None

RESP = bytes([0x41, 0x88, 0, 0xCA, 0xDE, ord('V'), ord('E'), ord('W'), ord('A'), 0x10, 0x02, 0, 0])
POLL = bytes([0x41, 0x88, 0, 0xCA, 0xDE, ord('W'), ord('A'), ord('V'), ord('E'), 0x21, 0, 0])

# ============================ initiator ======================================
def run_initiator(poll_tx_ts, resp_rx_ts, delayed_result=R.DWT_SUCCESS, script=None):
    """script: list of status values the poll loop sees (default: one good response)."""
    stub_chip()
    log = []
    script = script or ['good']
    # status register read sequence: per exchange, 'good' -> RXFCG then TXFRS (final sent)
    seq = []
    for s in script:
        if s == 'good': seq += [R.SYS_STATUS_RXFCG_BIT_MASK] + ([R.SYS_STATUS_TXFRS_BIT_MASK] if delayed_result == R.DWT_SUCCESS else [])
        elif s == 'timeout': seq += [R.SYS_STATUS_RXFTO_BIT_MASK]
        elif s == 'other': seq += [R.SYS_STATUS_RXFCG_BIT_MASK]
    it = iter(seq)
    frames = iter([bytes(RESP[:2]) + bytes([9]) + RESP[3:] + b'\x00\x00'
                   if s == 'good' else bytes([0x41, 0x88, 0, 0xCA, 0xDE, 1, 2, 3, 4, 0x77]) + bytes(8)
                   for s in script if s in ('good', 'other')])
    def r32(reg):
        if reg == R.SYS_STATUS_ID:
            try: return next(it)
            except StopIteration: raise KeyboardInterrupt
        if reg == R.RX_FINFO_ID: return 15
        return 0
    dwt.dwt_read32bitreg = r32
    dwt.dwt_write32bitreg = lambda r, v: log.append(('w32', r, hex(v)))
    dwt.dwt_setrxaftertxdelay = lambda v: log.append(('rxdelay', v))
    dwt.dwt_setrxtimeout = lambda v: log.append(('rxto', v))
    dwt.dwt_setpreambledetecttimeout = lambda v: log.append(('pto', v))
    dwt.dwt_writetxdata = lambda n, d, off: log.append(('txdata', n, bytes(d), off))
    dwt.dwt_writetxfctrl = lambda n, off, rng: log.append(('txfctrl', n, off, rng))
    def starttx(mode):
        log.append(('starttx', mode))
        return delayed_result if mode == R.DWT_START_TX_DELAYED else R.DWT_SUCCESS
    dwt.dwt_starttx = starttx
    dwt.dwt_readrxdata = lambda n, off: next(frames)
    dwt.dwt_readtxtimestamp = lambda: poll_tx_ts.to_bytes(5, 'little')
    dwt.dwt_readrxtimestamp = lambda: resp_rx_ts.to_bytes(5, 'little')
    dwt.dwt_setdelayedtrxtime = lambda v: log.append(('dx', v))
    m = load("ex05a", os.path.join(ROOT, "examples", "ex_05a_ds_twr_initiator", "main.py"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        m.main()
    return log, buf.getvalue()

# ============================ responder ======================================
def run_responder(poll_rx_ts, resp_tx_ts, final_rx_ts, final_msg, starttx_results=(R.DWT_SUCCESS,),
                  final_status='good', final_bytes=None):
    stub_chip()
    log = []
    st = {'phase': 0}
    # phase 0: waiting for poll -> RXFCG ; phase 1: waiting for final -> script ; then stop
    def r32(reg):
        if reg == R.SYS_STATUS_ID:
            if st['phase'] == 0:
                st['phase'] = 1; return R.SYS_STATUS_RXFCG_BIT_MASK
            if st['phase'] == 1:
                st['phase'] = 2
                return R.SYS_STATUS_RXFCG_BIT_MASK if final_status == 'good' else R.SYS_STATUS_RXFTO_BIT_MASK
            raise KeyboardInterrupt
        if reg == R.RX_FINFO_ID: return 12 if st['phase'] == 1 else 24
        return 0
    dwt.dwt_read32bitreg = r32
    dwt.dwt_write32bitreg = lambda r, v: log.append(('w32', r, hex(v)))
    dwt.dwt_rxenable = lambda m: log.append(('rxenable', m))
    dwt.dwt_setrxaftertxdelay = lambda v: log.append(('rxdelay', v))
    dwt.dwt_setrxtimeout = lambda v: log.append(('rxto', v))
    dwt.dwt_setpreambledetecttimeout = lambda v: log.append(('pto', v))
    poll = bytearray(POLL); poll[2] = 33          # sequence number is ignored
    reads = iter([bytes(poll), final_bytes if final_bytes is not None else final_msg])
    dwt.dwt_readrxdata = lambda n, off: next(reads)
    # RX timestamp: poll's on first call, final's afterwards; TX ts is the response's
    rxts = iter([poll_rx_ts, final_rx_ts])
    dwt.dwt_readrxtimestamp = lambda: next(rxts).to_bytes(5, 'little')
    dwt.dwt_readtxtimestamp = lambda: resp_tx_ts.to_bytes(5, 'little')
    dwt.dwt_setdelayedtrxtime = lambda v: log.append(('dx', v))
    dwt.dwt_writetxdata = lambda n, d, off: log.append(('txdata', n, bytes(d), off))
    dwt.dwt_writetxfctrl = lambda n, off, rng: log.append(('txfctrl', n, off, rng))
    res = iter(starttx_results)
    def starttx(mode):
        log.append(('starttx', mode)); return next(res)
    dwt.dwt_starttx = starttx
    m = load("ex05b", os.path.join(ROOT, "examples", "ex_05b_ds_twr_responder", "main.py"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        m.main()
    return log, buf.getvalue()

# ============ end-to-end model: two clocks, offset + skew, 40-bit wrap ========
def exchange(dist_m, ppm, offA, offB, t0=1_000_000):
    """Run the initiator and responder against a physical model; return printed distance."""
    T = dist_m / SPEED_OF_LIGHT / R.DWT_TIME_UNITS            # one-way flight, ticks (true time)
    k = 1 + ppm * 1e-6                                          # B clock rate relative to A
    A = lambda t: (round(t) + offA) & M40
    B = lambda t: (round(t * k) + offB) & M40
    # poll
    poll_tx = A(t0); t1 = t0 + T; poll_rx = B(t1)
    # responder reply: DX_TIME from poll_rx + 15000 uus; TX stamp includes the TX antenna delay
    dx_b = ((poll_rx + 15000 * U) >> 8) & 0xFFFFFFFF
    resp_tx = (((dx_b & 0xFFFFFFFE) << 8) + ANT) & M40
    t2 = t1 + ((resp_tx - poll_rx) & M40) / k
    t3 = t2 + T; resp_rx = A(t3)
    # initiator run: capture its final message and DX time
    log_i, out_i = run_initiator(poll_tx, resp_rx)
    dx_a = [l[1] for l in log_i if l[0] == 'dx'][0]
    assert dx_a == ((resp_rx + 15000 * U) >> 8) & 0xFFFFFFFF, (hex(dx_a),)
    final = [l for l in log_i if l[0] == 'txdata'][1]
    assert final[1] == 22 and final[3] == 0
    final_tx = (((dx_a & 0xFFFFFFFE) << 8) + ANT) & M40
    t4 = t3 + ((final_tx - resp_rx) & M40)
    t5 = t4 + T; final_rx = B(t5)
    # the final frame as received over the air (+ FCS)
    on_air = bytes(final[2]) + b'\x00\x00'
    log_r, out_r = run_responder(poll_rx, resp_tx, final_rx, on_air)
    return log_i, out_i, log_r, out_r, (poll_tx, resp_rx, final_tx, poll_rx, resp_tx, final_rx)

fails = 0
for dist, ppm, offA, offB in ((3.0, 0, 0x1234, 0x56789),
                              (3.0, 10, 0x1234, 0x56789),
                              (3.0, -20, 0x1234, 0x56789),                     # +/-20 ppm crystal error
                              (10.0, 15, M40 - 20_000_000_000, M40 - 5_000_000_000),   # stamps wrap 2^40 mid-exchange
                              (1.0, 5, 0xFFFFF000 - 3_000_000, 0xFFFFFFFF00 - 1_000_000),   # wrap 2^32 low word
                              (0.0, 0, 0, 0)):
    log_i, out_i, log_r, out_r, ts = exchange(dist, ppm, offA, offB)
    got = float(re.search(r"dist (-?[0-9.]+) m", out_r).group(1))
    assert abs(got - dist) < 0.06, (dist, ppm, got, out_r)
    print("  dist %5.2f m skew %+3d ppm -> responder prints %.2f m" % (dist, ppm, got))

# ---- initiator details ------------------------------------------------------
log_i, out_i, _, _, _ = exchange(3.0, 0, 0x1234, 0x56789)
assert ('rxdelay', 15000 - 200 - 1000) in log_i and ('rxto', 300 + 2000) in log_i and ('pto', 0) in log_i
txs = [l for l in log_i if l[0] == 'txdata']
assert txs[0][1] == 10 and txs[0][2][9] == 0x21 and txs[0][2][2] == 0           # poll, seq 0
assert txs[1][2][9] == 0x23 and txs[1][2][2] == 1                                # final, seq 1 (after poll)
assert [l for l in log_i if l[0] == 'txfctrl'][:2] == [('txfctrl', 12, 0, 1), ('txfctrl', 24, 0, 1)]
assert ('starttx', R.DWT_START_TX_IMMEDIATE | R.DWT_RESPONSE_EXPECTED) in log_i
assert ('starttx', R.DWT_START_TX_DELAYED) in log_i
assert "final sent" in out_i
# timestamps embedded in the final: poll_tx, resp_rx (low 32) and final_tx (low 32)
log_i, out_i = run_initiator(0x12_3456_7890, 0x34_AABB_CCDD)
fin = [l for l in log_i if l[0] == 'txdata'][1][2]
dx = [l[1] for l in log_i if l[0] == 'dx'][0]
assert int.from_bytes(fin[10:14], 'little') == 0x34567890
assert int.from_bytes(fin[14:18], 'little') == 0xAABBCCDD
assert int.from_bytes(fin[18:22], 'little') == (((dx & 0xFFFFFFFE) << 8) + ANT) & 0xFFFFFFFF
# late delayed TX -> TOO LATE, exchange abandoned, sequence number not advanced past the poll
log_i, out_i = run_initiator(0x1000, 0x2000, delayed_result=R.DWT_ERROR)
assert "TOO LATE" in out_i and "RESP_RX_TO_FINAL_TX_DLY_UUS" in out_i and "final sent" not in out_i
# timeout and unexpected frame paths
log_i, out_i = run_initiator(0x1000, 0x2000, script=['timeout', 'other'])
assert "no valid response" in out_i and "unexpected frame" in out_i, out_i
assert ('w32', R.SYS_STATUS_ID, hex(R.SYS_STATUS_ALL_RX_TO | R.SYS_STATUS_ALL_RX_ERR | R.SYS_STATUS_TXFRS_BIT_MASK)) in log_i

# ---- responder details ------------------------------------------------------
log_i, out_i, log_r, out_r, ts = exchange(3.0, 0, 0x1234, 0x56789)
poll_tx, resp_rx, final_tx, poll_rx, resp_tx, final_rx = ts
assert ('rxdelay', 15000 - 200 - 1000) in log_r and ('rxto', 220 + 2000) in log_r and ('pto', 0) in log_r
rsp = [l for l in log_r if l[0] == 'txdata'][0]
assert rsp[1] == 15 and rsp[2][9] == 0x10 and rsp[2][10] == 0x02 and rsp[2][2] == 0, rsp
assert [l for l in log_r if l[0] == 'txfctrl'][0] == ('txfctrl', 15, 0, 1)
assert ('starttx', R.DWT_START_TX_DELAYED | R.DWT_RESPONSE_EXPECTED) in log_r
assert [l[1] for l in log_r if l[0] == 'dx'] == [((poll_rx + 15000 * U) >> 8) & 0xFFFFFFFF]
assert "Ra " in out_r and "host turnaround" in out_r
# late response TX -> TOO LATE
log_r, out_r = run_responder(0x1000, 0x2000, 0x3000, bytes(24), starttx_results=(R.DWT_ERROR,))
assert "TOO LATE" in out_r and "POLL_RX_TO_RESP_TX_DLY_UUS" in out_r and "dist" not in out_r
# final never arrives
log_r, out_r = run_responder(0x1000, 0x2000, 0x3000, bytes(24), final_status='timeout')
assert "no final received" in out_r and "dist" not in out_r
# wrong frame in place of the final
log_r, out_r = run_responder(0x1000, 0x2000, 0x3000, bytes(24))
assert "unexpected frame in place of final" in out_r and "dist" not in out_r

# ---- exact integer math beats float32 --------------------------------------
import struct
f32 = lambda x: struct.unpack('f', struct.pack('f', x))[0]
Ra = Rb = Da = Db = 15000 * U
Ra += 2 * 60; Rb += 2 * 60
exact = (Ra * Rb - Da * Db) // (Ra + Rb + Da + Db)
naive = (f32(Ra) * f32(Rb) - f32(Da) * f32(Db)) / (f32(Ra) + f32(Rb) + f32(Da) + f32(Db))
print("  exact tof %d ticks; naive float32 would give %.0f" % (exact, naive))
assert exact == 60
print("DS-TWR DRIVER + EX_05A/05B TESTS PASSED")
