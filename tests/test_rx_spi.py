import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "mocks"))   # fake machine/utime so the code runs on desktop CPython
sys.path.insert(0, ROOT)
import sys
from decadriver import deca_device as dwt
from decadriver import deca_regs as R

cap=[]
dwt.writetospi=lambda h,b: cap.append(('W',h,b))
dwt.readfromspi=lambda h,n: (cap.append(('R',h,n)), bytes([0x5A])*n)[1]

# CMD_RX fast command: single byte, FAC marker (bit0=1), write bit set
cap.clear(); assert dwt.dwt_rxenable(R.DWT_START_RX_IMMEDIATE)==R.DWT_SUCCESS
h=cap[0][1]; print("CMD_RX header:",h.hex()); assert len(h)==1 and h[0]&1 and h[0]&0x80

# RX buffer read: RX_BUFFER_0 = file 0x12, offset 0 -> single-byte FARW read header
cap.clear(); d=dwt.dwt_readrxdata(10,0)
h=cap[0][1]; print("RXBUF0 read header:",h.hex(),"len",cap[0][2])
assert len(h)==1 and h[0]==(0x12<<1) and cap[0][2]==10 and d==bytes([0x5A])*10

# nonzero direct offset -> 2-byte extended header
cap.clear(); dwt.dwt_readrxdata(4,16); assert len(cap[0][1])==2

# range check: offset+length beyond RX_BUFFER_MAX_LEN -> nothing read
cap.clear(); assert dwt.dwt_readrxdata(100,1000)==b"" and cap==[]

# double-buffer flag selects RX_BUFFER_1 (file 0x13)
dwt.local.dblbuffon=R.DBL_BUFF_ACCESS_BUFFER_1
cap.clear(); dwt.dwt_readrxdata(4,0); assert cap[0][1][0]==(0x13<<1); dwt.local.dblbuffon=0

# indirect access path for offset > 127
cap.clear(); dwt.dwt_readrxdata(4,200)
assert any(c[0]=='W' for c in cap) and cap[-1][0]=='R', cap

# delayed RX: HPDWARN clear -> success; set -> abort + immediate RX unless IDLE_ON_DLY_ERR
fast=[]; dwt.dwt_writefastCMD=lambda c: fast.append(c)
dwt.dwt_read8bitoffsetreg=lambda r,o: 0x00
assert dwt.dwt_rxenable(R.DWT_START_RX_DELAYED)==R.DWT_SUCCESS and fast==[R.CMD_DRX]
fast.clear(); dwt.dwt_read8bitoffsetreg=lambda r,o: (R.SYS_STATUS_HPDWARN_BIT_MASK>>24)&0xFF
assert dwt.dwt_rxenable(R.DWT_START_RX_DELAYED)==R.DWT_ERROR and fast==[R.CMD_DRX,R.CMD_TXRXOFF,R.CMD_RX], fast
fast.clear()
assert dwt.dwt_rxenable(R.DWT_START_RX_DELAYED|R.DWT_IDLE_ON_DLY_ERR)==R.DWT_ERROR and fast==[R.CMD_DRX,R.CMD_TXRXOFF], fast
fast.clear(); dwt.dwt_read8bitoffsetreg=lambda r,o:0
for mode,cmd in ((R.DWT_START_RX_DLY_REF,R.CMD_DRX_REF),(R.DWT_START_RX_DLY_RS,R.CMD_DRX_RS),(R.DWT_START_RX_DLY_TS,R.CMD_DRX_TS)):
    fast.clear(); dwt.dwt_rxenable(mode); assert fast==[cmd],(mode,fast)
assert dwt.dwt_rxenable(0x40)==R.DWT_ERROR   # invalid mode
print("RX SPI-LEVEL + DELAYED-RX TESTS PASSED")
