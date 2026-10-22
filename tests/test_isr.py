import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "mocks"))   # fake machine/utime so the code runs on desktop CPython
sys.path.insert(0, ROOT)
import sys
from decadriver import deca_device as dwt
from decadriver import deca_regs as R

# ---- fake register layer: records every access in one ordered log ----
log=[]; reg={}
def reset(fstat=0,status=0,finfo=0,status_hi=0):
    log.clear(); reg.clear()
    reg.update(fstat=fstat,status=status,finfo=finfo,status_hi=status_hi)
    for a in ('cbTxDone','cbRxOk','cbRxTo','cbRxErr','cbSPIErr','cbSPIRdy'): setattr(dwt.local,a,None)
    dwt.local.stsconfig=0; dwt.local.spicrc=0; dwt.local.dblbuffon=0; dwt.local.longFrames=0
def r8(r,o=0):
    if r==R.FINT_STAT_ID: return reg['fstat']
    return 0
def r16(r,o=0):
    if r==R.SYS_STATUS_HI_ID: return reg['status_hi']
    if r==R.RX_FINFO_ID: return reg['finfo']
    return 0
def r32(r,o=0): return reg['status'] if r==R.SYS_STATUS_ID else 0
dwt.dwt_read8bitoffsetreg=r8; dwt.dwt_read16bitoffsetreg=r16; dwt.dwt_read32bitoffsetreg=r32
dwt.dwt_read32bitreg=lambda r: r32(r,0)
dwt.dwt_write8bitoffsetreg=lambda r,o,v: log.append(('w8',r,o,v))
dwt.dwt_write16bitoffsetreg=lambda r,o,v: log.append(('w16',r,o,v))
dwt.dwt_write32bitoffsetreg=lambda r,o,v: log.append(('w32',r,o,v))
dwt.dwt_write32bitreg=lambda r,v: log.append(('w32',r,0,v))
def cb(name):
    def f(d): log.append((name,d.status,d.datalength,d.rx_flags,d.status_hi))
    return f
def arm(**which):
    dwt.dwt_setcallbacks(*[cb(n) if which.get(n,True) else None for n in
        ('tx','ok','to','err')], cb('spierr') if which.get('spierr') else None,
        cb('spirdy') if which.get('spirdy') else None)
def idx(tag): return next(i for i,x in enumerate(log) if x[0]==tag)
SS=R.SYS_STATUS_ID

# ---------- fast-status read goes to the right place (hand-derived header) ----------
cap=[]
dwt.writetospi=lambda h,b: cap.append(('W',h.hex(),b.hex())); dwt.readfromspi=lambda h,n:(cap.append(('R',h.hex(),n)),bytes(n))[1]
import importlib, importlib.util; src=open(os.path.join(ROOT, 'decadriver/deca_device.py')).read()
# (use a fresh module copy so the stubs above don't mask the real accessors)
spec=importlib.util.spec_from_file_location('dd2',os.path.join(ROOT, 'decadriver/deca_device.py'))
dd2=importlib.util.module_from_spec(spec); spec.loader.exec_module(dd2)
dd2.writetospi=lambda h,b: cap.append(('W',h.hex(),b.hex())); dd2.readfromspi=lambda h,n:(cap.append(('R',h.hex(),n)),bytes(n))[1]
dd2.dwt_read8bitoffsetreg(R.FINT_STAT_ID,0)
# FINT_STAT_ID=0x1F0000: file 0x1F, offset 0 -> one-byte FARW read header = 0x1F<<1 = 0x3E
assert cap[-1]==('R','3e',1), cap[-1]

# ---------- TX done: clear first (low byte of ALL_TX = 0xF8), then callback ----------
reset(fstat=R.FINT_STAT_TXOK_BIT_MASK,status=R.SYS_STATUS_TXFRS_BIT_MASK); arm()
dwt.dwt_isr()
assert log[0]==('w8',SS,0,0xF8) and log[1][0]=='tx' and len(log)==2, log
assert log[1][1]==R.SYS_STATUS_TXFRS_BIT_MASK        # cb sees status as ISR entered

# ---------- RX good frame: datalength + flags, clear BEFORE callback ----------
st=R.SYS_STATUS_RXFCG_BIT_MASK|R.SYS_STATUS_RXFR_BIT_MASK|R.SYS_STATUS_CIADONE_BIT_MASK
reset(fstat=R.FINT_STAT_RXOK_BIT_MASK,status=st,finfo=14|R.RX_FINFO_RNG_BIT_MASK); arm()
dwt.dwt_isr()
assert log[0]==('w32',SS,0,R.SYS_STATUS_ALL_RX_GOOD) and log[1][0]=='ok', log
assert log[1][2]==14 and log[1][3]==(R.DWT_CB_DATA_RX_FLAG_CIA|R.DWT_CB_DATA_RX_FLAG_RNG), log[1]
assert R.SYS_STATUS_ALL_RX_GOOD==0x6f00

# standard frames mask to 7 bits (0x380|0x0E -> 14), extended to 10 bits (-> 0x38E)
reset(fstat=R.FINT_STAT_RXOK_BIT_MASK,status=R.SYS_STATUS_RXFCG_BIT_MASK,finfo=0x380|0x0E); arm()
dwt.dwt_isr(); assert log[1][2]==14, log[1]
reset(fstat=R.FINT_STAT_RXOK_BIT_MASK,status=R.SYS_STATUS_RXFCG_BIT_MASK,finfo=0x380|0x0E); arm(); dwt.local.longFrames=1
dwt.dwt_isr(); assert log[1][2]==0x38E, log[1]

# CIA error / CP error: flagged AND added to the clear mask so RXERR isn't also raised
st=R.SYS_STATUS_RXFCG_BIT_MASK|R.SYS_STATUS_CIAERR_BIT_MASK|R.SYS_STATUS_CPERR_BIT_MASK
reset(fstat=R.FINT_STAT_RXOK_BIT_MASK,status=st,finfo=14); arm(); dwt.dwt_isr()
assert log[0][3]==(R.SYS_STATUS_CIAERR_BIT_MASK|R.SYS_STATUS_CPERR_BIT_MASK|R.SYS_STATUS_ALL_RX_GOOD), log[0]
assert log[1][3]==(R.DWT_CB_DATA_RX_FLAG_CER|R.DWT_CB_DATA_RX_FLAG_CPER), log[1]

# no-data STS mode: frame reported by RXFR alone, even with fstat clear
reset(fstat=0,status=R.SYS_STATUS_RXFR_BIT_MASK); arm(); dwt.local.stsconfig=R.DWT_STS_MODE_ND
dwt.dwt_isr()
assert log[0]==('w32',SS,0,R.SYS_STATUS_RXFCE_BIT_MASK|R.SYS_STATUS_ALL_RX_GOOD), log[0]
assert log[1][2]==0 and log[1][3]==R.DWT_CB_DATA_RX_FLAG_ND, log[1]

# ---------- RX error: clear (32-bit) BEFORE callback ----------
reset(fstat=R.FINT_STAT_RXERR_BIT_MASK,status=R.SYS_STATUS_RXFCE_BIT_MASK); arm(); dwt.dwt_isr()
assert log[0]==('w32',SS,0,R.SYS_STATUS_ALL_RX_ERR) and log[1][0]=='err', log

# ---------- RX timeout: clear byte 2 with 0x22 (FTO|PTO >>16) BEFORE callback ----------
reset(fstat=R.FINT_STAT_RXTO_BIT_MASK,status=R.SYS_STATUS_RXFTO_BIT_MASK); arm(); dwt.dwt_isr()
assert log[0]==('w8',SS,2,0x22) and log[1][0]=='to', log

# ---------- SPI ready: callback FIRST, then clear (so cb can still read status) ----------
reset(fstat=R.FINT_STAT_SYS_EVENT_BIT_MASK); arm(spirdy=True); dwt.dwt_isr()
assert log[0][0]=='spirdy' and log[1]==('w16',SS,2,0x0180), log

# ---------- panic: SPI error -> clear + cbSPIErr; CMD error -> clear only ----------
bits=R.SYS_STATUS_HI_SPIERR_BIT_MASK|R.SYS_STATUS_HI_SPI_UNF_BIT_MASK|R.SYS_STATUS_HI_SPI_OVF_BIT_MASK
reset(fstat=R.FINT_STAT_SYS_PANIC_BIT_MASK,status_hi=R.SYS_STATUS_HI_SPIERR_BIT_MASK); arm(spierr=True); dwt.dwt_isr()
assert ('w32',SS,0,R.SYS_STATUS_SPICRCE_BIT_MASK) in log and ('w16',R.SYS_STATUS_HI_ID,0,bits) in log
assert log[-1][0]=='spierr' and log[-1][4]==R.SYS_STATUS_HI_SPIERR_BIT_MASK
reset(fstat=R.FINT_STAT_SYS_PANIC_BIT_MASK,status_hi=R.SYS_STATUS_HI_CMD_ERR_BIT_MASK); arm(spierr=True); dwt.dwt_isr()
assert log==[('w16',R.SYS_STATUS_HI_ID,0,R.SYS_STATUS_HI_CMD_ERR_BIT_MASK)], log   # cleared, no SPI-error callback
# SPI CRC error only counts when CRC checking is on
reset(fstat=R.FINT_STAT_SYS_PANIC_BIT_MASK,status=R.SYS_STATUS_SPICRCE_BIT_MASK); arm(spierr=True); dwt.dwt_isr()
assert not any(x[0]=='spierr' for x in log)
reset(fstat=R.FINT_STAT_SYS_PANIC_BIT_MASK,status=R.SYS_STATUS_SPICRCE_BIT_MASK); arm(spierr=True); dwt.local.spicrc=1; dwt.dwt_isr()
assert any(x[0]=='spierr' for x in log)

# ---------- several events at once: handled in upstream's order ----------
reset(fstat=(R.FINT_STAT_TXOK_BIT_MASK|R.FINT_STAT_RXOK_BIT_MASK|R.FINT_STAT_RXERR_BIT_MASK|R.FINT_STAT_RXTO_BIT_MASK),
      status=R.SYS_STATUS_TXFRS_BIT_MASK|R.SYS_STATUS_RXFCG_BIT_MASK,finfo=14); arm(); dwt.dwt_isr()
cbs=[x[0] for x in log if x[0] in ('tx','ok','err','to')]
assert cbs==['tx','ok','err','to'], cbs

# ---------- unregistered callbacks are skipped, not called ----------
reset(fstat=(R.FINT_STAT_TXOK_BIT_MASK|R.FINT_STAT_RXTO_BIT_MASK)); arm(tx=False,to=False); dwt.dwt_isr()
assert [x[0] for x in log]==['w8','w8'], log          # events still cleared
reset(fstat=0); arm(); dwt.dwt_isr(); assert log==[]   # nothing pending: nothing touched

# ---------- double-buffer mode is refused loudly, not silently mishandled ----------
reset(fstat=R.FINT_STAT_RXOK_BIT_MASK,status=R.SYS_STATUS_RXFCG_BIT_MASK); arm(); dwt.local.dblbuffon=R.DBL_BUFF_ACCESS_BUFFER_1
try: dwt.dwt_isr(); raise SystemExit("should have raised")
except NotImplementedError: pass
dwt.local.dblbuffon=0

# ---------- dwt_setinterrupt: three modes, hand-derived SPI bytes ----------
mask=R.SYS_ENABLE_LO_TXFRS_ENABLE_BIT_MASK|R.SYS_ENABLE_LO_RXFCG_ENABLE_BIT_MASK
cap.clear()
dd2.dwt_setinterrupt(mask,0,R.DWT_ENABLE_INT)
#  SYS_ENABLE_LO_ID=0x3C: addr=0x3C<<2=0xF0; AND_OR_32 mode 0x8003 -> hdr C0, F3; body and=FFFFFFFF or=mask(LE)
assert cap[0]==('W','c0f3','ffffffff'+mask.to_bytes(4,'little').hex()), cap[0]
#  SYS_ENABLE_HI_ID=0x40: addr=0x100 -> hdr0=0x80|0x01|0x40=0xC1, hdr1=0x00|3=0x03; mask 0 -> or=0
assert cap[1]==('W','c103','ffffffff'+'00000000'), cap[1]
cap.clear(); dd2.dwt_setinterrupt(mask,0,R.DWT_DISABLE_INT)
assert cap[0]==('W','c0f3',((~mask)&0xFFFFFFFF).to_bytes(4,'little').hex()+'00000000'), cap[0]
cap.clear(); dd2.dwt_setinterrupt(mask,0x55,R.DWT_ENABLE_INT_ONLY)
assert cap[0]==('W','c0f0',mask.to_bytes(4,'little').hex()) and cap[1]==('W','c100',(0x55).to_bytes(4,'little').hex()), cap
assert (R.DWT_DISABLE_INT,R.DWT_ENABLE_INT,R.DWT_ENABLE_INT_ONLY)==(0,1,2)

print("DWT_ISR / SETINTERRUPT TESTS PASSED")
