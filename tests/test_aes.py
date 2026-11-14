import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "mocks"))
sys.path.insert(0, ROOT)
import io, contextlib, importlib.util, hashlib
from decadriver import deca_device as dwt
from decadriver import deca_regs as R
from decadriver.deca_types import DwtAesKey, DwtAesConfig, DwtAesJob
from shared_data import shared_defines as SD

# ---- constants -----------------------------------------------------------------------
assert (R.AES_CFG_ID, R.AES_IV0_ID, R.DMA_CFG0_ID, R.DMA_CFG1_ID, R.AES_START_ID, R.AES_STS_ID, R.AES_KEY0_ID) == \
       (0x10030, 0x10034, 0x10044, 0x10048, 0x1004C, 0x10050, 0x10054)
assert R.AES_ERRORS == 0x0E and R.MIC_16 == 7 and R.SCRATCH_RAM_ID == 0x160000 and R.SCRATCH_BUFFER_MAX_LEN == 127
assert (SD.AES_RES_OK, SD.AES_RES_ERROR_LENGTH, SD.AES_RES_ERROR, SD.AES_RES_ERROR_FRAME) == (0, -1, -2, -3)
assert [dwt.dwt_mic_size_from_bytes(b) for b in (0, 4, 6, 8, 10, 12, 14, 16)] == list(range(8))

# ---- register-level programming (SPI headers) ------------------------------------------
cap = []
dwt.writetospi = lambda h, b: cap.append((bytes(h).hex(), bytes(b).hex()))
def cfg(mode, mic=R.MIC_16, core=R.AES_core_type_GCM):
    return DwtAesConfig(aes_key_otp_type=R.AES_key_RAM, aes_core_type=core, mic=mic, key_src=R.AES_KEY_Src_Register,
                        key_load=R.AES_KEY_Load, key_addr=0, key_size=R.AES_KEY_128bit, mode=mode)
# AES_CFG: file 1 offset 0x30 -> addr (1<<9)|(0x30<<2)=0x2C0 | 0x8000 | EAMRW 0x40 -> c2 c0. Value: key_load<<6 | mic 7<<8 = 0x0740 (+1 decrypt, +0x800 CCM)
cap.clear(); dwt.dwt_configure_aes(cfg(R.AES_Encrypt)); assert cap == [('c2c0', '4007')], cap
cap.clear(); dwt.dwt_configure_aes(cfg(R.AES_Decrypt)); assert cap == [('c2c0', '4107')], cap
cap.clear(); dwt.dwt_configure_aes(cfg(R.AES_Decrypt, mic=R.MIC_8, core=R.AES_core_type_CCM)); assert cap == [('c2c0', '410b')], cap   # 0x0B41 = CCM 0x800 | MIC_8 (3) << 8 | load 0x40 | decrypt 1
# key words, LSB first: KEY0 at file 1 offset 0x54 -> 0x200+0x150 = 0x350 -> c3 50
key = DwtAesKey(0x41424344, 0x45464748, 0x49505152, 0x53545556)
cap.clear(); dwt.dwt_set_keyreg_128(key)
assert cap == [('c350', '44434241'), ('c360', '48474645'), ('c370', '52515049'), ('c380', '56555453')], cap

# ---- nonce / IV programming ---------------------------------------------------------------
n12 = bytes(range(0x10, 0x1C))
cap.clear(); dwt._dwt_update_nonce_gcm(n12)
assert cap == [('c2d0', n12.hex())], cap                 # AES_IV0 file 1 offset 0x34: 0x2D0
n13 = bytes(range(13))                                    # nonce[i] = i
cap.clear(); dwt._dwt_update_nonce_ccm(n13, 0x0123)
# IV bytes per the upstream table: nonce[10..7], [6..3], [2..0], don't care, len LE, nonce[12], nonce[11]
assert cap == [('c2d0', bytes([10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 0, 0, 0x23, 0x01, 12, 11]).hex())], cap

# ---- dwt_do_aes: DMA configuration and error codes ----------------------------------------------
class Chip:
    """Fake DW3000 AES + buffers. The 'cipher' is a toy (hash keystream + hash tag): this checks the HOST
    plumbing (offsets, lengths, nonce, key, headers), not AES itself, which is done by the chip."""
    def __init__(self):
        self.reset()
    def reset(self):
        self.tx = bytearray(1024); self.rx = bytearray(1024); self.scratch = bytearray(127)
        self.key = [0] * 4; self.iv = bytes(12); self.aes_cfg = 0; self.dma0 = 0; self.dma1 = 0
        self.sts = 0; self.log = []; self.hang = False; self.clears = []
    # --- patched into the driver ---
    def writetodevice(self, reg, idx, n, buf):
        buf = bytes(buf)[:n]
        if reg == R.TX_BUFFER_ID: self.tx[idx:idx + n] = buf
        elif reg == R.SCRATCH_RAM_ID: self.scratch[idx:idx + n] = buf
        elif reg == R.AES_IV0_ID: self.iv = buf
        else: raise AssertionError(hex(reg))
    def readfromdevice(self, reg, idx, n):
        mem = {R.TX_BUFFER_ID: self.tx, R.RX_BUFFER_0_ID: self.rx, R.SCRATCH_RAM_ID: self.scratch}[reg]
        return bytes(mem[idx:idx + n])
    def write32(self, reg, v):
        if reg in (R.AES_KEY0_ID, R.AES_KEY1_ID, R.AES_KEY2_ID, R.AES_KEY3_ID): self.key[(reg - R.AES_KEY0_ID) // 4] = v
        elif reg == R.DMA_CFG0_ID: self.dma0 = v
        elif reg == R.DMA_CFG1_ID: self.dma1 = v
        else: self.log.append(('w32', reg, v))
    def write16(self, reg, off, v):
        if reg == R.AES_CFG_ID: self.aes_cfg = v
        else: self.log.append(('w16', reg, off, v))
    def write8(self, reg, off, v):
        if reg == R.AES_START_ID: self.start()
        elif reg == R.AES_STS_ID: self.clears.append(v); self.sts &= ~v
        else: self.log.append(('w8', reg, off, v))
    def read8(self, reg, off):
        assert reg == R.AES_STS_ID
        return 0 if self.hang else self.sts
    # --- the engine ---
    def keybytes(self):
        return b"".join(w.to_bytes(4, "little") for w in self.key)
    def stream(self, n):
        out = b""; c = 0
        while len(out) < n:
            out += hashlib.sha256(self.keybytes() + self.iv + bytes([c])).digest(); c += 1
        return out[:n]
    def tag(self, header, cipher, mic):
        return hashlib.sha256(b"T" + self.keybytes() + self.iv + header + cipher).digest()[:mic]
    def start(self):
        hdr = self.dma1 & R.DMA_CFG1_HDR_SIZE_BIT_MASK
        pl = (self.dma1 & R.DMA_CFG1_PYLD_SIZE_BIT_MASK) >> R.DMA_CFG1_PYLD_SIZE_BIT_OFFSET
        mic = 0 if ((self.aes_cfg >> 8) & 7) == 0 else ((self.aes_cfg >> 8) & 7) * 2 + 2
        src = (self.dma0 >> R.DMA_CFG0_SRC_PORT_BIT_OFFSET) & 7
        dst = (self.dma0 >> R.DMA_CFG0_DST_PORT_BIT_OFFSET) & 7
        buf = {R.AES_Src_Tx_buf: self.tx, R.AES_Src_Rx_buf_0: self.rx, R.AES_Src_Scratch: self.scratch}[src]
        dbuf = {R.AES_Dst_Tx_buf: self.tx, R.AES_Dst_Rx_buf_0: self.rx, R.AES_Dst_Scratch: self.scratch}.get(dst)
        header = bytes(buf[:hdr])
        if (self.aes_cfg & 1) == R.AES_Encrypt:
            plain = bytes(buf[hdr:hdr + pl])
            cipher = bytes(a ^ b for a, b in zip(plain, self.stream(pl)))
            dbuf[hdr:hdr + pl] = cipher
            dbuf[hdr + pl:hdr + pl + mic] = self.tag(header, cipher, mic)
            self.sts = R.AES_STS_AES_DONE_BIT_MASK
        else:
            cipher = bytes(buf[hdr:hdr + pl]); tag = bytes(buf[hdr + pl:hdr + pl + mic])
            dbuf[hdr:hdr + pl] = bytes(a ^ b for a, b in zip(cipher, self.stream(pl)))
            ok = tag == self.tag(header, cipher, mic)
            self.sts = R.AES_STS_AES_DONE_BIT_MASK | (0 if ok else R.AES_STS_AUTH_ERR_BIT_MASK)

chip = Chip()
def attach(chip):
    dwt.dwt_writetodevice = chip.writetodevice; dwt.dwt_readfromdevice = chip.readfromdevice
    dwt.dwt_write32bitreg = chip.write32; dwt.dwt_write16bitoffsetreg = chip.write16
    dwt.dwt_write8bitoffsetreg = chip.write8; dwt.dwt_read8bitoffsetreg = chip.read8
attach(chip)

dwt.dwt_set_keyreg_128(key); dwt.dwt_configure_aes(cfg(R.AES_Encrypt))
hdr = bytearray(range(21)); pay = bytearray(b"hello AES world")
job = DwtAesJob(nonce=bytearray(12), header=hdr, payload=pay, header_len=21, payload_len=len(pay),
                src_port=R.AES_Src_Tx_buf, dst_port=R.AES_Dst_Tx_buf, mode=R.AES_Encrypt, mic_size=16)
st = dwt.dwt_do_aes(job, R.AES_core_type_GCM)
assert st == R.AES_STS_AES_DONE_BIT_MASK, st
assert chip.dma0 == (3 | (3 << 13)) == 0x6003 and chip.dma1 == (21 | (len(pay) << 7)), (hex(chip.dma0), hex(chip.dma1))
assert chip.key == [0x41424344, 0x45464748, 0x49505152, 0x53545556]
assert bytes(chip.tx[:21]) == bytes(hdr)                                    # header in clear
assert bytes(chip.tx[21:21 + len(pay)]) != bytes(pay)                       # payload encrypted
assert chip.clears == [R.AES_STS_AES_DONE_BIT_MASK]                          # status bits cleared after the job
# decryption of what was produced: move TX buffer into the RX buffer, decrypt in place
chip.rx[:] = chip.tx
dwt.dwt_configure_aes(cfg(R.AES_Decrypt))
out_h = bytearray(21); out_p = bytearray(len(pay))
dj = DwtAesJob(nonce=bytearray(12), header=out_h, payload=out_p, header_len=21, payload_len=len(pay),
               src_port=R.AES_Src_Rx_buf_0, dst_port=R.AES_Dst_Rx_buf_0, mode=R.AES_Decrypt, mic_size=16)
st = dwt.dwt_do_aes(dj, R.AES_core_type_GCM)
assert st == 1 and chip.dma0 == (1 | (1 << 13)) == 0x2001, (st, hex(chip.dma0))
assert bytes(out_p) == bytes(pay) and bytes(out_h) == bytes(hdr)             # plain header and payload handed back
# auth failure: status carries AUTH_ERR, nothing is copied back
chip.rx[:] = chip.tx; chip.rx[25] ^= 1                                       # flip a ciphertext bit
out_p2 = bytearray(b"unchanged-bytes")
dj2 = DwtAesJob(nonce=bytearray(12), header=None, payload=out_p2, header_len=21, payload_len=len(pay),
                src_port=R.AES_Src_Rx_buf_0, dst_port=R.AES_Dst_Rx_buf_0, mode=R.AES_Decrypt, mic_size=16)
st = dwt.dwt_do_aes(dj2, R.AES_core_type_GCM)
assert st & R.AES_STS_AUTH_ERR_BIT_MASK and bytes(out_p2) == b"unchanged-bytes", st
# error codes
bad = lambda **kw: dwt.dwt_do_aes(DwtAesJob(**dict(dict(nonce=bytearray(12), header=hdr, payload=pay, header_len=21, payload_len=15,
              src_port=R.AES_Src_Tx_buf, dst_port=R.AES_Dst_Tx_buf, mode=R.AES_Encrypt, mic_size=16), **kw)), R.AES_core_type_GCM)
assert bad(mic_size=R.MIC_ERROR) == R.ERROR_WRONG_MIC_SIZE
assert bad(mode=5) == R.ERROR_WRONG_MODE
assert bad(src_port=R.AES_Src_Scratch, dst_port=R.AES_Dst_Scratch, header_len=100, payload=bytearray(20), payload_len=20) == R.ERROR_DATA_SIZE  # scratch only 127 - 16 - 2
assert bad(mode=R.AES_Decrypt, dst_port=R.AES_Dst_STS_key, payload_len=17) == R.ERROR_PAYLOAD_SIZE
# hung AES block: bounded wait reports a transfer error instead of looping forever
chip.hang = True
assert bad() & R.AES_STS_TRANS_ERR_BIT_MASK
chip.hang = False
print("  dwt_do_aes driver checks passed")

# ---- ex_01i and ex_02i end to end against the fake chip ----------------------------------------------
def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

def stub_common(chip):
    attach(chip)
    dwt.dwt_checkidlerc = lambda: True
    dwt.dwt_initialise = lambda m: R.DWT_SUCCESS
    dwt.dwt_configure = lambda c: 0
    dwt.dwt_configuretxrf = lambda c: None
    dwt.dwt_forcetrxoff = lambda: None

def run_tx(chip, cycles=3, key0=None):
    stub_common(chip)
    frames = []; fctrl = []
    dwt.dwt_writetxfctrl = lambda n, o, r: fctrl.append((n, o, r))
    def starttx(mode):
        n = fctrl[-1][0] - R.FCS_LEN
        frames.append(bytes(chip.tx[:n])); return R.DWT_SUCCESS
    dwt.dwt_starttx = starttx
    dwt.dwt_read32bitreg = lambda r: R.SYS_STATUS_TXFRS_BIT_MASK
    dwt.dwt_write32bitreg = chip.write32
    import utime
    real = utime.sleep_ms
    n = {'i': 0}
    def sl(ms):
        if ms == 500:
            n['i'] += 1
            if n['i'] >= cycles: raise KeyboardInterrupt
    utime.sleep_ms = sl
    m = load("ex01i", "examples/ex_01i_simple_tx_aes/main.py")
    if key0 is not None: m.aes_key.key0 = key0
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf): m.main()
    finally:
        utime.sleep_ms = real
    return frames, fctrl, buf.getvalue()

chip = Chip()
frames, fctrl, out = run_tx(chip)
assert len(frames) == 3 and fctrl[0] == (71, 0, 0) == fctrl[-1] and all(len(f) == 69 for f in frames), (fctrl, [len(f) for f in frames])
assert "AES TX OK: 0" in out and "AES TX OK: 2" in out and "Stopped by user" in out
h0, h1, h2 = frames[0][:21], frames[1][:21], frames[2][:21]
assert h0[:2] == bytes([0x50, 0x40]) and h0[3:9] == bytes([0xA, 0xB, 0xC, 0xD, 0xE, 0xF]) and h0[9:15] == bytes([1, 2, 3, 4, 5, 6])
assert [h[2] for h in (h0, h1, h2)] == [0, 1, 2]                                           # sequence number
assert [int.from_bytes(h[15:21], "little") for h in (h0, h1, h2)] == [0, 1, 2]             # PN, 6 bytes LSB first
assert len({bytes(f[21:53]) for f in frames}) == 3                                         # different nonce -> different ciphertext
assert b"Good" not in frames[0]                                                            # encrypted
assert chip.iv == bytes([2, 0, 0, 0, 0, 0, 1, 2, 3, 4, 5, 6])                              # last nonce: PN 2 || source address
print("  ex_01i frames built:", frames[0].hex()[:44], "...")

def run_rx(frames_in, key0=None):
    chip = Chip()
    stub_common(chip)
    st = {'i': 0}
    def cur(): return frames_in[st['i']]
    def rxenable(mode):
        if st['i'] >= len(frames_in): raise KeyboardInterrupt
        f = cur(); chip.rx[:] = bytes(1024); chip.rx[:len(f)] = f          # frame (+FCS) lands in RX buffer
    dwt.dwt_rxenable = rxenable
    dwt.dwt_read32bitreg = lambda r: R.SYS_STATUS_RXFCG_BIT_MASK
    dwt.dwt_read16bitoffsetreg = lambda r, o: len(cur())
    dwt.dwt_readrxdata = lambda n, o: bytes(chip.rx[o:o + n])
    def w32(reg, v):
        if reg == R.SYS_STATUS_ID: st['i'] += 1
        else: chip.write32(reg, v)
    dwt.dwt_write32bitreg = w32
    m = load("ex02i", "examples/ex_02i_simple_rx_aes/main.py")
    if key0 is not None: m.aes_key.key0 = key0
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf): m.main()
    return buf.getvalue(), chip

# frames as received on air carry the 2 FCS bytes
air = [f + b"\x00\x00" for f in frames]
out, rchip = run_rx(air)
assert out.count("AES RX OK") == 3, out
assert "AES RX OK 0: b'Good, This is the right message\\x00'" in out and "AES RX OK 2: b'Good" in out, out
assert rchip.iv == bytes([2, 0, 0, 0, 0, 0, 1, 2, 3, 4, 5, 6])                           # receiver rebuilt the same nonce from the header
assert rchip.dma0 == 0x2001 and rchip.dma1 == (21 | (32 << 7))
# wrong key on the receiver: authentication fails, reception continues
out, _ = run_rx(air, key0=0x41424345)
assert out.count("ERROR AES") == 3 and "AES RX OK" not in out and "Stopped by user" in out, out
# header modified in the air (e.g. destination address): tag no longer matches
tam = bytearray(air[0]); tam[4] ^= 0x55
out, _ = run_rx([bytes(tam), air[1]])
assert out.count("ERROR AES") == 1 and "AES RX OK 0" in out, out
# a frame too short to hold header + MIC + FCS, and one too long for the payload buffer
out, _ = run_rx([air[0][:30], bytes(21 + 16 + 2 + 200)])
assert out.count("Error Frame") == 2, out
print("AES DRIVER + EX_01I / EX_02I TESTS PASSED")
