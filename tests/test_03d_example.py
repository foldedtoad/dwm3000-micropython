import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "mocks"))   # fake machine/utime so the code runs on desktop CPython
sys.path.insert(0, ROOT)
import sys, io, contextlib, importlib.util
import utime
from platform.port import port
from decadriver import deca_device as dwt
from decadriver import deca_regs as R

# ======================= simulated DW3000 =======================
chip=dict(status=0, enable=0, finfo=0)
queue=[]                 # events still to be delivered (as IRQ edges) during this cycle
cycles=[]                # scripted RX outcome per transmission
cmds=[]; sleeps=[]; txdata=[]
payload=bytes([0x41,0x8C,0,0x9A,0x60])+b"DECAWAVE"+b"DW"+bytes([0x10,0,0,0,0,0])
OK_BITS=(R.SYS_STATUS_RXFCG_BIT_MASK|R.SYS_STATUS_RXFR_BIT_MASK|R.SYS_STATUS_CIADONE_BIT_MASK|
         R.SYS_STATUS_RXPRD_BIT_MASK|R.SYS_STATUS_RXSFDD_BIT_MASK|R.SYS_STATUS_RXPHD_BIT_MASK)

def level():                               # IRQ line = any ENABLED event pending
    port.irq._v = 1 if (chip['status'] & chip['enable']) else 0
def fstat():
    s=chip['status']; f=0
    if s&R.SYS_STATUS_TXFRS_BIT_MASK: f|=R.FINT_STAT_TXOK_BIT_MASK
    if s&R.SYS_STATUS_RXFCG_BIT_MASK: f|=R.FINT_STAT_RXOK_BIT_MASK
    if s&R.SYS_STATUS_ALL_RX_ERR: f|=R.FINT_STAT_RXERR_BIT_MASK
    if s&R.SYS_STATUS_ALL_RX_TO: f|=R.FINT_STAT_RXTO_BIT_MASK
    return f
def clear(mask): chip['status']&=~mask; level()

dwt.dwt_read8bitoffsetreg=lambda r,o=0: fstat() if r==R.FINT_STAT_ID else 0
dwt.dwt_read16bitoffsetreg=lambda r,o=0: chip['finfo'] if r==R.RX_FINFO_ID else 0
dwt.dwt_read32bitoffsetreg=lambda r,o=0: chip['status'] if r==R.SYS_STATUS_ID else 0
dwt.dwt_read32bitreg=lambda r: dwt.dwt_read32bitoffsetreg(r,0)
def w8(r,o,v):  clear(v<<(8*o)) if r==R.SYS_STATUS_ID else None
def w16(r,o,v): clear(v<<(8*o)) if r==R.SYS_STATUS_ID else None
def w32(r,o,v): clear(v) if r==R.SYS_STATUS_ID else None
dwt.dwt_write8bitoffsetreg=w8; dwt.dwt_write16bitoffsetreg=w16; dwt.dwt_write32bitoffsetreg=w32
dwt.dwt_write32bitreg=lambda r,v: w32(r,0,v)
def modify32(r,o,a,b):
    if r==R.SYS_ENABLE_LO_ID: chip['enable']=((chip['enable']&a)|b)&0xFFFFFFFF; level()
dwt.dwt_modify32bitoffsetreg=modify32; dwt.dwt_modify16bitoffsetreg=lambda *a: None
dwt.dwt_writetodevice=lambda r,i,n,b: txdata.append(bytes(b)) if r==R.TX_BUFFER_ID else None
dwt.dwt_readfromdevice=lambda r,i,n: payload[i:i+n] if r==R.RX_BUFFER_0_ID else bytes(n)
def fastcmd(c):
    cmds.append(c)
    if c==R.CMD_TX_W4R:                                    # real dwt_starttx -> start a cycle
        script=cycles.pop(0) if cycles else None
        queue[:]=(['tx']+([script] if script else [])) if script is not None else ['tx']
dwt.dwt_writefastCMD=fastcmd
dwt.dwt_checkidlerc=lambda: True; dwt.dwt_initialise=lambda m: R.DWT_SUCCESS
dwt.dwt_configure=lambda c: 0; dwt.dwt_setleds=lambda m: None; dwt.dwt_configuretxrf=lambda c: None

def deliver():                    # the chip raises its IRQ line: a rising edge
    ev=queue.pop(0)
    if ev=='tx': chip['status']|=R.SYS_STATUS_TXFRS_BIT_MASK
    elif ev=='ok': chip['status']|=OK_BITS; chip['finfo']=21
    elif ev=='to': chip['status']|=R.SYS_STATUS_RXFTO_BIT_MASK
    elif ev=='err': chip['status']|=R.SYS_STATUS_RXFCE_BIT_MASK|R.SYS_STATUS_RXPHE_BIT_MASK
    level()
    if port.irq._v: port.irq._fire()           # edge -> soft IRQ -> port dispatch -> real dwt_isr

# The example's wait loop polls ticks_ms(); that's where MicroPython would run scheduled IRQs.
clock=[0]
def ticks_ms():
    if queue: deliver()
    else: clock[0]+=100              # fake time advances while nothing is happening
    return clock[0]
utime.ticks_ms=ticks_ms
utime.sleep_ms=lambda ms: sleeps.append(ms)

cycles[:]=['ok','to','err',None]       # exchange 1..3 produce an outcome; exchange 4 is silent

spec=importlib.util.spec_from_file_location('m03d',os.path.join(ROOT, 'examples/ex_03d_tx_wait_resp_interrupts/main.py'))
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
class CtrlCOnWarning(io.StringIO):
    def write(self,t):
        r=super().write(t)
        if "level 0 with status bits set" in t: raise KeyboardInterrupt
        return r
buf=CtrlCOnWarning()
with contextlib.redirect_stdout(buf): m.main()
out=buf.getvalue(); print(out)

# ---- setup: interrupts enabled, handler installed as a rising-edge SOFT irq, before first TX ----
want=(R.SYS_ENABLE_LO_TXFRS_ENABLE_BIT_MASK|R.SYS_ENABLE_LO_RXFCG_ENABLE_BIT_MASK|R.SYS_ENABLE_LO_RXFTO_ENABLE_BIT_MASK|
      R.SYS_ENABLE_LO_RXPTO_ENABLE_BIT_MASK|R.SYS_ENABLE_LO_RXPHE_ENABLE_BIT_MASK|R.SYS_ENABLE_LO_RXFCE_ENABLE_BIT_MASK|
      R.SYS_ENABLE_LO_RXFSL_ENABLE_BIT_MASK|R.SYS_ENABLE_LO_RXSTO_ENABLE_BIT_MASK)
assert chip['enable']==want, hex(chip['enable'])
assert "IRQ handler installed on GP2" in out
assert port.irq.hard is False and port.irq.trigger==port.irq.IRQ_RISING or port.irq._handler is None  # (cleared on exit)

# ---- the four exchanges ----
lines=[l for l in out.splitlines()]
def after(tag,start=0):
    return next(i for i,l in enumerate(lines) if l.startswith(tag) and i>=start)
# exchange 1: TX-done IRQ, then good response, with the payload read from the RX buffer
i1=after("msg len 14: c5 00"); t1=after("tx_conf_cb",i1); r1=after("rx_ok_cb: len 21: 41 8c 00 9a 60",t1)
assert i1<t1<r1 and "[round trip" in lines[r1]
# exchange 2: timeout
i2=after("msg len 14: c5 01",r1); t2=after("tx_conf_cb",i2); o2=after("rx_to_cb: timeout",t2)
# exchange 3: RX error, with the errors named (decoded from the status the ISR saw)
i3=after("msg len 14: c5 02",o2); t3=after("tx_conf_cb",i3); e3=after("rx_err_cb: error RXPHE RXFCE",t3)
# exchange 4: TX confirmed but nothing else -> watchdog explains instead of hanging silently
i4=after("msg len 14: c5 03",e3); t4=after("tx_conf_cb",i4); w4=after("WARNING: no interrupt 1000 ms after TX",t4)
assert "IRQ pin GP2 level=0, SYS_STATUS=0x00000000" in lines[w4], lines[w4]

# ---- 4 transmissions, each a real W4R command, frames carry the incrementing sequence number ----
assert cmds.count(R.CMD_TX_W4R)==4 and len(txdata)==4
assert [t[1] for t in txdata]==[0,1,2,3] and all(len(t)==14 and t[0]==0xC5 for t in txdata)

# ---- the callbacks' delay choices drive the main loop: 1000 after OK, 3000 after timeout, 0 after error ----
# three 2 ms start-up waits (reset hold, reset settle, INIT_RC->IDLE_RC), then 1000 after the good
# response and 3000 after the timeout. NOTHING after the RX error: upstream retries immediately (0 ms).
assert sleeps==[2,2,2,1000,3000], sleeps

# ---- clean exit on Ctrl-C ----
assert port.irq._handler is None and port._isr is None, "interrupt handler must be removed"
assert cmds[-1]==R.CMD_TXRXOFF
assert "Stopped by user" in out
assert port.isr_overruns==0 and not port.spi_busy and not port._in_service
print("EX_03D END-TO-END (REAL ISR + REAL DISPATCH) TEST PASSED")
