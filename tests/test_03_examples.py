import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "mocks"))   # fake machine/utime so the code runs on desktop CPython
sys.path.insert(0, ROOT)
import sys, io, contextlib, importlib.util
from decadriver import deca_device as dwt
from decadriver import deca_regs as R

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

def stub_common(log, status_events, finfo_len, rx_payloads):
    st={'i':0,'tx_pending':False}
    def read32(reg,off=0):
        if reg==R.SYS_STATUS_ID:
            if st['tx_pending']:
                st['tx_pending']=False; return R.SYS_STATUS_TXFRS_BIT_MASK
            if st['i']>=len(status_events): raise KeyboardInterrupt
            v=status_events[st['i']]; st['i']+=1; return v
        if reg==R.RX_FINFO_ID: return finfo_len[st['i']-1]
        return 0
    dwt.dwt_read32bitreg=lambda r: read32(r,0)
    dwt.dwt_write32bitreg=lambda r,v: log.append(('clear',hex(v)))
    dwt.dwt_writefastCMD=lambda c: log.append(('fast',c))
    dwt.dwt_checkidlerc=lambda: True
    dwt.dwt_initialise=lambda m: R.DWT_SUCCESS
    dwt.dwt_setleds=lambda m: None
    dwt.dwt_configure=lambda c: 0
    dwt.dwt_configuretxrf=lambda c: None
    dwt.dwt_rxenable=lambda m: log.append(('rxenable',m))
    dwt.dwt_readrxdata=lambda n,off: (log.append(('readrx',n,off)), rx_payloads[st['i']-1][:n])[1]
    dwt.dwt_writetxdata=lambda n,d,off: log.append(('txdata',n,bytes(d),off))
    dwt.dwt_writetxfctrl=lambda n,off,rng: log.append(('txfctrl',n,off,rng))
    def starttx(mode): log.append(('starttx',mode)); st['tx_pending']=True; return R.DWT_SUCCESS
    dwt.dwt_starttx=starttx
    dwt.dwt_setrxaftertxdelay=lambda v: log.append(('rxdelay',v))
    dwt.dwt_setrxtimeout=lambda v: log.append(('rxto',v))
    dwt.dwt_forcetrxoff=lambda: log.append(('forceoff',))

class LogWriter(io.StringIO):
    def __init__(self,log): super().__init__(); self.log=log
    def write(self,t):
        if t.strip(): self.log.append(('out',t))
        return super().write(t)

def run(m,log):
    buf=LogWriter(log)
    with contextlib.redirect_stdout(buf): m.main()
    return buf.getvalue()

# ============================== 03a ==============================
log=[]
resp=bytes([0x41,0x8C,0,0x9A,0x60])+b"DECAWAVE"[:8]+b"DW"+bytes([0x10,0,0,0,0,0])
resp=resp[:21]; assert len(resp)==21
OK=R.SYS_STATUS_RXFCG_BIT_MASK
events=[0,OK, R.SYS_STATUS_RXFTO_BIT_MASK, R.SYS_STATUS_RXPHE_BIT_MASK]
# FINFO/payload indexed by (status reads so far - 1)
finfo=[0,21,0,0]; pay=[b"",resp,b"",b""]
stub_common(log,events,finfo,pay)
m=load('m03a',os.path.join(ROOT, 'examples/ex_03a_tx_wait_resp/main.py'))
out=run(m,log); print(out)
assert ('rxdelay',60) in log and ('rxto',50000) in log
tx=[x for x in log if x[0]=='txdata']
assert [t[1] for t in tx]==[14,14,14,14]  # 3 completed cycles + a 4th sent when Ctrl-C hits
assert [t[2][1] for t in tx]==[0,1,2,3], "sequence number must increment each frame"
assert all(t[2][0]==0xC5 and t[2][10]==0x43 and t[2][11]==0x02 and len(t[2])==14 for t in tx), "blink frame shape"
assert [x for x in log if x[0]=='txfctrl'][0]==('txfctrl',14,0,0)
assert all(x[1]==(R.DWT_START_TX_IMMEDIATE|R.DWT_RESPONSE_EXPECTED) for x in log if x[0]=='starttx')
assert "resp len 21: 41 8c 00 9a 60" in out and "[round trip " in out
assert out.count("no response (frame-wait timeout after")==1 and "preamble-detect" not in out and "receive error: RXPHE" in out
assert "setup: ACK_RESP=" in out and "RX_FWTO=" in out
clears=[x[1] for x in log if x[0]=='clear']
assert clears==[hex(OK), hex(R.SYS_STATUS_ALL_RX_TO|R.SYS_STATUS_ALL_RX_ERR), hex(R.SYS_STATUS_ALL_RX_TO|R.SYS_STATUS_ALL_RX_ERR)], clears
assert [x for x in log if x[0]!='out'][-1]==('forceoff',) and "Stopped by user" in out
print("03a PASSED\n")

# ============================== 03b ==============================
log=[]
def blink(src,sn=0,flag=0x02): return bytes([0xC5,sn])+src+bytes([0x43,flag,0,0])
b1=blink(b"AAAAAAAA"); bad=blink(b"BBBBBBBB",flag=0x00); b2=blink(b"CCCCCCCC",sn=5)
events=[OK, OK, R.SYS_STATUS_RXFCE_BIT_MASK, OK]
finfo=[14,14,0,14]; pay=[b1,bad,b"",b2]
stub_common(log,events,finfo,pay)
m=load('m03b',os.path.join(ROOT, 'examples/ex_03b_rx_send_resp/main.py'))
out=run(m,log); print(out)
tx=[x for x in log if x[0]=='txdata']
assert len(tx)==2, "must reply to the 2 valid blinks only, not the look-alike"
r1,r2=tx[0][2],tx[1][2]
assert tx[0][1]==21 and r1[5:13]==b"AAAAAAAA" and r2[5:13]==b"CCCCCCCC", "reply addressed to each blink's sender"
assert (r1[2],r2[2])==(0,1), "response sequence number increments"
assert r1[:5]==bytes([0x41,0x8C,0,0x9A,0x60]) and r1[13:15]==b"DW"
assert all(x[1]==R.DWT_START_TX_IMMEDIATE for x in log if x[0]=='starttx')   # no W4R on the response
assert "receive error: RXFCE" in out
assert out.count("resp len 21")==2 and out.count("host turnaround:")==2
assert "msg len 14: c5 00 42 42" in out, "look-alike frame should still be reported"
# the reply must be on the air BEFORE any console output for that frame
first_tx=min(i for i,x in enumerate(log) if x[0]=='starttx')
first_print=min(i for i,x in enumerate(log) if x[0]=='out' and 'msg len' in x[1])
assert first_tx < first_print, "responder must start TX before printing"
txfrs_clear=min(i for i,x in enumerate(log) if x[0]=='clear' and x[1]==hex(R.SYS_STATUS_TXFRS_BIT_MASK))
assert txfrs_clear < first_print, "...and finish TX (clear TXFRS) before printing"
clears=[x[1] for x in log if x[0]=='clear']
assert clears.count(hex(R.SYS_STATUS_TXFRS_BIT_MASK))==2 and clears.count(hex(R.SYS_STATUS_ALL_RX_ERR))==1
assert [x for x in log if x[0]!='out'][-1]==('forceoff',) and "Stopped by user" in out
print("03b PASSED\n\n03a/03b END-TO-END TESTS PASSED")
