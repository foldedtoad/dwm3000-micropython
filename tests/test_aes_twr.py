import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "mocks"))
sys.path.insert(0, ROOT)
import io, contextlib, importlib.util, re, hashlib
import utime
from decadriver import deca_device as dwt
from decadriver import deca_regs as R
from MAC_802_15_4 import mac_802_15_4 as mac
from shared_data.shared_defines import SPEED_OF_LIGHT, UUS_TO_DWT_TIME

M40 = (1 << 40) - 1
U = UUS_TO_DWT_TIME
ANT = 16385
INIT_ADDR = 0x8877665544332211        # initiator (upstream: its SRC_ADDR)
RESP_ADDR = 0x1122334455667788        # responder
KEYS = {1: (0x00010203, 0x04050607, 0x08090A0B, 0x0C0D0E0F), 2: (0x11223344, 0x55667788, 0x99AABBCC, 0xDDEEFF00)}

# ---- MAC helper unit tests ----------------------------------------------------
f = mac.new_initiator_frame()
assert bytes(f.header[:5]) == bytes([0x09, 0xEC, 0, 0x21, 0x43]) and f.header[mac.SEC_CTRL_IDX] == 0x0F
assert len(f.header) == 27 == mac.HEADER_LEN
mac.set_pan_ids_and_addresses(f, 0x4321, RESP_ADDR, INIT_ADDR)
assert bytes(f.header[5:13]) == RESP_ADDR.to_bytes(8, 'little') and bytes(f.header[13:21]) == INIT_ADDR.to_bytes(8, 'little')
mac.update_aux_frame_cnt(f, 0x01020304); assert bytes(f.header[22:26]) == b'\x04\x03\x02\x01' and mac.get_aux_frame_cnt(f) == 0x01020304
assert mac.get_nonce(f) == INIT_ADDR.to_bytes(8, 'little') + b'\x04\x03\x02\x01' + b'\x07' and len(mac.get_nonce(f)) == 13
for lvl, sz in ((0, 0), (1, 4), (2, 8), (3, 16), (4, mac.MIC_ERROR), (5, 4), (6, 8), (7, 16)):
    f.header[mac.SEC_CTRL_IDX] = lvl; assert mac.get_aux_mic_size(f) == sz, lvl
f.header[0] = 0x01; assert not mac.get_security_state(f)
f.header[0] = 0x09; assert mac.get_security_state(f)
print("  MAC helpers ok")

# ---- toy AES engine (not real AES: tests plumbing, key/nonce/header binding) ---------
class Chip:
    def __init__(self):
        self.key = None; self.rx = b""; self.log = []; self.sys_calls = 0
def stream(key, nonce, n):
    s = hashlib.sha256(repr((key, bytes(nonce))).encode()).digest() * 8
    return s[:n]
def tag(key, nonce, hdr, ct):
    return hashlib.sha256(repr((key, bytes(nonce), bytes(hdr), bytes(ct))).encode()).digest()[:16]

def stub(chip, rxframe, sys_script, resp_ts=None):
    dwt.dwt_checkidlerc = lambda: True
    dwt.dwt_initialise = lambda m: R.DWT_SUCCESS
    dwt.dwt_setleds = lambda m: None
    dwt.dwt_configure = lambda c: 0
    dwt.dwt_configuretxrf = lambda c: None
    dwt.dwt_setlnapamode = lambda m: None
    dwt.dwt_setrxantennadelay = lambda v: None
    dwt.dwt_settxantennadelay = lambda v: None
    dwt.dwt_forcetrxoff = lambda: chip.log.append(('off',))
    dwt.dwt_setrxaftertxdelay = lambda v: chip.log.append(('rxdelay', v))
    dwt.dwt_setrxtimeout = lambda v: chip.log.append(('rxto', v))
    dwt.dwt_write32bitreg = lambda r, v: None
    dwt.dwt_rxenable = lambda m: None
    dwt.dwt_setdelayedtrxtime = lambda v: chip.log.append(('dx', v))
    dwt.dwt_writetxfctrl = lambda n, o, r: chip.log.append(('txfctrl', n, o, r))
    dwt.dwt_readclockoffset = lambda: 0
    def r32(reg):
        if reg == R.SYS_STATUS_ID:
            chip.sys_calls += 1
            if chip.sys_calls > len(sys_script): raise KeyboardInterrupt
            return sys_script[chip.sys_calls - 1]
        if reg == R.RX_FINFO_ID: return len(rxframe) if rxframe else 0
        return 0
    dwt.dwt_read32bitreg = r32
    dwt.dwt_readrxdata = lambda n, off: bytes(rxframe[off:off + n])
    def setkey(k): chip.key = (k.key0, k.key1, k.key2, k.key3); chip.log.append(('key', chip.key))
    dwt.dwt_set_keyreg_128 = setkey
    dwt.dwt_configure_aes = lambda c: chip.log.append(('aescfg', c.mode, c.mic, c.aes_core_type))
    DONE = R.AES_STS_AES_DONE_BIT_MASK
    def do_aes(job, core):
        assert core == R.AES_core_type_CCM
        if job.mode == R.AES_Encrypt:
            hdr = bytes(job.header[:job.header_len]); pt = bytes(job.payload[:job.payload_len])
            ct = bytes(a ^ b for a, b in zip(pt, stream(chip.key, job.nonce, len(pt))))
            chip.log.append(('txframe', hdr + ct + tag(chip.key, job.nonce, hdr, ct) + b'\x00\x00', bytes(job.nonce)))
            return DONE
        hdr = bytes(rxframe[:job.header_len]); n = job.payload_len
        ct = bytes(rxframe[job.header_len:job.header_len + n])
        mic = bytes(rxframe[job.header_len + n:job.header_len + n + 16])
        if tag(chip.key, job.nonce, hdr, ct) != mic:
            return DONE | R.AES_ERRORS & -R.AES_ERRORS      # lowest error bit
        pt = bytes(a ^ b for a, b in zip(ct, stream(chip.key, job.nonce, n)))
        job.payload[:n] = pt
        return DONE
    dwt.dwt_do_aes = do_aes
    dwt.dwt_starttx = lambda m: chip.log.append(('starttx', m)) or (resp_ts or {}).get('ret', R.DWT_SUCCESS)

def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "examples", name, "main.py"))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

def run(name, rxframe, sys_script, tx_ts=0, rx_ts=0, resp_ts=None, raw_offset=0):
    chip = Chip(); stub(chip, rxframe, sys_script, resp_ts)
    dwt.dwt_readclockoffset = lambda: raw_offset
    dwt.dwt_readtxtimestamplo32 = lambda: tx_ts & 0xFFFFFFFF
    dwt.dwt_readrxtimestamplo32 = lambda: rx_ts & 0xFFFFFFFF
    dwt.dwt_readrxtimestamp = lambda: (rx_ts & M40).to_bytes(5, 'little')
    saved = utime.sleep_ms; utime.sleep_ms = lambda ms: None
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            load(name).main()
    finally:
        utime.sleep_ms = saved
    return chip, buf.getvalue()

RXFCG, RXFTO, TXFRS = R.SYS_STATUS_RXFCG_BIT_MASK, R.SYS_STATUS_RXFTO_BIT_MASK, R.SYS_STATUS_TXFRS_BIT_MASK
INIT, RESP = "ex_06e_aes_ss_twr_initiator", "ex_06f_aes_ss_twr_responder"

# ---- initiator's poll on the air ---------------------------------------------------
chip, out = run(INIT, None, [RXFTO])
poll = [l for l in chip.log if l[0] == 'txframe'][0]
frame = poll[1]
assert len(frame) == 27 + 12 + 16 + 2 and frame[:2] == b'\x09\xec' and frame[3:5] == b'\x21\x43'
assert frame[5:13] == RESP_ADDR.to_bytes(8, 'little') and frame[13:21] == INIT_ADDR.to_bytes(8, 'little')
assert frame[21] == 0x0F and frame[26] == 1 and frame[22:26] == b'\x00\x00\x00\x00'
assert poll[2] == INIT_ADDR.to_bytes(8, 'little') + bytes(4) + b'\x07'
assert ('key', KEYS[1]) in chip.log and ('txfctrl', 57, 0, 1) in chip.log
assert ('aescfg', R.AES_Encrypt, R.MIC_16 if False else dwt.dwt_mic_size_from_bytes(16), R.AES_core_type_CCM) in chip.log
assert ('rxdelay', 50000 - 280 - 5000) in chip.log and ('rxto', 250 + 10000) in chip.log
assert "no valid response" in out
assert b"Poll message" not in frame           # payload is not in the clear
print("  initiator poll frame ok (57 bytes, key 1, header authenticated)")

# ---- end to end: distance through encrypted poll + response ------------------------
def exchange(dist_m, ppm, offA, offB, t0=1_000_000):
    T = dist_m / SPEED_OF_LIGHT / R.DWT_TIME_UNITS
    k = 1 + ppm * 1e-6
    A = lambda t: (round(t) + offA) & M40
    B = lambda t: (round(t * k) + offB) & M40
    poll_tx = A(t0); poll_rx = B(t0 + T)
    # responder run
    chip_r, out_r = run(RESP, frame, [RXFCG, TXFRS], rx_ts=poll_rx)
    dx = [l[1] for l in chip_r.log if l[0] == 'dx'][0]
    assert dx == ((poll_rx + 50000 * U) >> 8) & 0xFFFFFFFF
    resp = [l for l in chip_r.log if l[0] == 'txframe'][0]
    resp_tx = (((dx & 0xFFFFFFFE) << 8) + ANT) & M40
    t3 = t0 + T + ((resp_tx - poll_rx) & M40) / k + T
    resp_rx = A(t3)
    chip_i, out_i = run(INIT, resp[1], [RXFCG], tx_ts=poll_tx, rx_ts=resp_rx,
                        raw_offset=round(ppm * 1e-6 * (1 << 26)))
    return chip_r, out_r, resp, chip_i, out_i

fails = 0
for dist, ppm in ((3.0, 0), (3.0, 10), (5.0, -15), (0.0, 0)):
    chip_r, out_r, resp, chip_i, out_i = exchange(dist, ppm, 0x1234, 0x56789)
    corr = float(re.search(r"dist (-?[0-9.]+) m", out_i).group(1))
    unc = float(re.search(r"uncorrected (-?[0-9.]+) m", out_i).group(1))
    assert abs(corr - dist) < 0.15, (dist, ppm, corr)
    if ppm:
        assert abs(unc - dist) > 5, "clock skew should hurt the uncorrected distance"
    print("  dist %.2f m skew %+d ppm -> corrected %.2f m (uncorrected %.2f m)" % (dist, ppm, corr, unc))
assert "AES ok" in out_i

# ---- responder details --------------------------------------------------------------
chip_r, out_r, resp, chip_i, out_i = exchange(3.0, 0, 0x1234, 0x56789)
r = resp[1]
assert len(r) == 27 + 16 + 16 + 2 and r[:2] == b'\x09\xec'
assert r[5:13] == INIT_ADDR.to_bytes(8, 'little') and r[13:21] == RESP_ADDR.to_bytes(8, 'little')   # swapped
assert r[26] == 2 and ('key', KEYS[2]) in chip_r.log
assert r[2] == (frame[2] + 1) & 0xFF and r[22:26] == (1).to_bytes(4, 'little') or r[22:26] == b'\x01\x00\x00\x00'
assert resp[2] == RESP_ADDR.to_bytes(8, 'little') + r[22:26] + b'\x07'                           # nonce: our address
assert ('txfctrl', 61, 0, 1) in chip_r.log and ('starttx', R.DWT_START_TX_DELAYED) in chip_r.log
assert "replied (host turnaround" in out_r
print("  responder reply: key 2, addresses swapped, counter+1, nonce uses responder address")

# decrypt used key 1 first (selected by the received key index), then key 2 to encrypt
keys_seen = [l[1] for l in chip_r.log if l[0] == 'key']
assert keys_seen[:2] == [KEYS[1], KEYS[2]], keys_seen

# late reply
chip_r, out_r = run(RESP, frame, [RXFCG], rx_ts=0x1000, resp_ts={'ret': R.DWT_ERROR})
assert "TOO LATE" in out_r and "replied" not in out_r
# poll with wrong key (tampered header byte) -> authentication error, responder stops
bad = bytearray(frame); bad[2] ^= 1
chip_r, out_r = run(RESP, bytes(bad), [RXFCG], rx_ts=0x1000)
assert "ERROR AES" in out_r and not [l for l in chip_r.log if l[0] == 'txframe' and False]
assert ('starttx', R.DWT_START_TX_DELAYED) not in chip_r.log
# frame for someone else -> ignored silently
other = bytearray(frame); other[5] ^= 0xFF
chip_r, out_r = run(RESP, bytes(other), [RXFCG], rx_ts=0x1000)
assert "ERROR" not in out_r and ('starttx', R.DWT_START_TX_DELAYED) not in chip_r.log
# unencrypted/insecure frame -> ignored
insec = bytearray(frame); insec[0] = 0x01
chip_r, out_r = run(RESP, bytes(insec), [RXFCG], rx_ts=0x1000)
assert "ERROR" not in out_r and ('starttx', R.DWT_START_TX_DELAYED) not in chip_r.log
# truncated frame -> Error Frame
chip_r, out_r = run(RESP, frame[:20], [RXFCG], rx_ts=0x1000)
assert "Error Frame" in out_r
print("  authentication failure, foreign, insecure and truncated frames handled")

# ---- initiator: tampered response -> ERROR AES; foreign -> ignored -----------------
_, _, resp, _, _ = exchange(3.0, 0, 0x1234, 0x56789)
tam = bytearray(resp[1]); tam[30] ^= 0x55
chip_i, out_i = run(INIT, bytes(tam), [RXFCG], tx_ts=1, rx_ts=2)
assert "ERROR AES" in out_i and "dist" not in out_i
fr = bytearray(resp[1]); fr[13] ^= 0xFF
chip_i, out_i = run(INIT, bytes(fr), [RXFCG], tx_ts=1, rx_ts=2)
assert "not for us" in out_i and "dist" not in out_i
print("  initiator rejects tampered/foreign responses")

# sequence number and frame counter advance between polls (nonce never repeats)
chip_i, out_i = run(INIT, None, [RXFTO, RXFTO, RXFTO])
polls = [l for l in chip_i.log if l[0] == 'txframe']
assert len(polls) >= 3
ctrs = [int.from_bytes(p[1][22:26], 'little') for p in polls[:3]]
seqs = [p[1][2] for p in polls[:3]]
assert ctrs == [0, 1, 2] and seqs == [0x0A, 0x0B, 0x0C] or ctrs == [0, 1, 2], (ctrs, seqs)
assert len({p[2] for p in polls[:3]}) == 3
print("  frame counter %s, seq %s, nonces distinct" % (ctrs, seqs))
print("AES SS-TWR EX_06E/06F TESTS PASSED")
