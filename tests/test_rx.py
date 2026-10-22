import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "mocks"))   # fake machine/utime so the code runs on desktop CPython
sys.path.insert(0, ROOT)
import sys, io, contextlib
from decadriver import deca_device as dwt
from decadriver import deca_regs as R

log=[]
# ---- simulated device: sequence of status words returned by SYS_STATUS reads ----
events=[0, 0, R.SYS_STATUS_RXFCG_BIT_MASK,              # idle, idle, good frame
        R.SYS_STATUS_RXPHE_BIT_MASK|R.SYS_STATUS_RXFCE_BIT_MASK,  # error frame (2 errors)
        R.SYS_STATUS_RXFCG_BIT_MASK]                    # good frame
state={'i':0,'rxenables':0}
payload=b"\xC5\x07DECAWAVE"

def fake_read32(reg, off=0):
    if reg==R.SYS_STATUS_ID:
        if state['i']>=len(events): raise KeyboardInterrupt
        v=events[state['i']]; state['i']+=1; return v
    if reg==R.RX_FINFO_ID: return len(payload)+R.FCS_LEN
    if reg==R.DEV_ID_ID: return R.DWT_C0_DEV_ID
    return 0
dwt.dwt_read32bitoffsetreg=fake_read32
dwt.dwt_read32bitreg=lambda r: fake_read32(r,0)
dwt.dwt_write32bitreg=lambda r,v: log.append(('clear',hex(r),hex(v)))
dwt.dwt_writefastCMD=lambda c: log.append(('fast',c))
dwt.dwt_readfromdevice=lambda reg,idx,n: (log.append(('rd',hex(reg),idx,n)), payload[:n])[1]
dwt.dwt_checkidlerc=lambda: True
dwt.dwt_initialise=lambda m: R.DWT_SUCCESS
dwt.dwt_setleds=lambda m: None
dwt.dwt_configure=lambda c: 0

import importlib.util
spec=importlib.util.spec_from_file_location('rxmain',os.path.join(ROOT, 'examples/ex_02a_simple_rx/main.py'))
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

buf=io.StringIO()
with contextlib.redirect_stdout(buf): m.main()
out=buf.getvalue(); print(out)

assert "Ready to Receive" in out
assert "len 10: b'\\xc5\\x07DECAWAVE'" in out, "good frame not printed"
assert out.count("len 10:")==2, "expected 2 good frames"
assert "receive error: RXPHE" in out and "receive error: RXFCE" in out
assert "RXFSL" not in out, "false error reported"
assert "Stopped by user" in out
# RX enabled before each of the 3 reception cycles; forcetrxoff (CMD_TXRXOFF=0) on exit
fast=[x[1] for x in log if x[0]=='fast']
assert fast.count(R.CMD_RX)==4, fast  # 3 completed cycles + a 4th armed when Ctrl-C hits
assert fast[-1]==R.CMD_TXRXOFF, fast
# good-frame clears use RXFCG, error clear uses ALL_RX_ERR
clears=[x[2] for x in log if x[0]=='clear']
assert clears==[hex(R.SYS_STATUS_RXFCG_BIT_MASK),hex(R.SYS_STATUS_ALL_RX_ERR),hex(R.SYS_STATUS_RXFCG_BIT_MASK)], clears
# payload read: FCS excluded (10 bytes), from RX_BUFFER_0 offset 0
rd=[x for x in log if x[0]=='rd']
assert rd[0]==('rd',hex(R.RX_BUFFER_0_ID),0,10), rd
print("RX EXAMPLE END-TO-END TEST PASSED")
