import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "mocks"))   # fake machine/utime so the code runs on desktop CPython
sys.path.insert(0, ROOT)
import sys
from decadriver import deca_device as dwt
from decadriver import deca_regs as R

# --- register-level behaviour with a tiny fake register file ---
regs={}
dwt.dwt_read32bitoffsetreg=lambda r,o=0: regs.get((r,o),0)
dwt.dwt_write32bitoffsetreg=lambda r,o,v: regs.__setitem__((r,o),v&0xFFFFFFFF)
mods=[]
dwt.dwt_modify16bitoffsetreg=lambda r,o,a,b: mods.append((r,o,a,b))

# setrxaftertxdelay: only the low 20 bits change, upper bits (other ACK_RESP fields) preserved
regs[(R.ACK_RESP_ID,0)]=0xFF300000 | 0xABCDE
dwt.dwt_setrxaftertxdelay(60)
v=regs[(R.ACK_RESP_ID,0)]
print("ACK_RESP =",hex(v)); assert v==0xFF300000|60
# oversize value is masked to 20 bits, never spilling into upper fields
dwt.dwt_setrxaftertxdelay(0x1FFFFF); assert regs[(R.ACK_RESP_ID,0)]==0xFF300000|0xFFFFF

# setrxtimeout enable: writes RX_FWTO, ORs RXWTOE into SYS_CFG (16-bit OR = and 0xFFFF, or mask)
dwt.dwt_setrxtimeout(5000)
assert regs[(R.RX_FWTO_ID,0)]==5000
assert mods[-1]==(R.SYS_CFG_ID,0,0xFFFF,R.SYS_CFG_RXWTOE_BIT_MASK), mods[-1]
# disable: clears only RXWTOE
dwt.dwt_setrxtimeout(0)
assert mods[-1]==(R.SYS_CFG_ID,0,(~R.SYS_CFG_RXWTOE_BIT_MASK)&0xFFFF,0), mods[-1]
assert R.SYS_CFG_RXWTOE_BIT_MASK==0x200 and R.SYS_STATUS_ALL_RX_TO==0x220000

# --- SPI header bytes for the two new accesses ---
import importlib; importlib.reload(dwt)
cap=[]
dwt.writetospi=lambda h,b: cap.append(('W',h.hex(),b.hex()))
dwt.readfromspi=lambda h,n: (cap.append(('R',h.hex(),n)), bytes(n))[1]
dwt.dwt_setrxtimeout(5000)
print(cap)
# Hand-derived from the header formula (not from the code under test):
#  RX_FWTO_ID=0x34: addr=0x34<<2=0xD0; WR bit 0x80 | EAMRW bit 0x40 -> header C0 D0; body 5000 LE
assert cap[0]==('W','c0d0',(5000).to_bytes(4,'little').hex()), cap[0]
#  SYS_CFG OR16 (AND_OR_16 mode=0x8002): addr=0x10<<2=0x40 -> header C0, (0x40|0x02)=0x42; body and=FFFF or=0x0200 LE
assert cap[1]==('W','c042','ffff0002'), cap[1]
# starttx with RESPONSE_EXPECTED must issue the TX_W4R fast command (0x0C)
cap.clear(); dwt.dwt_starttx(R.DWT_START_TX_IMMEDIATE|R.DWT_RESPONSE_EXPECTED)
print(cap); assert cap[0][0]=='W' and int(cap[0][1],16)==(0x80|(R.CMD_TX_W4R<<1)|1), cap
print("03 DRIVER TESTS PASSED")
