import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "mocks"))   # fake machine/utime so the code runs on desktop CPython
sys.path.insert(0, ROOT)
import sys
from platform.port import port, Port
import platform.port as P
from platform import deca_spi as S

irq=port.irq
def fresh():
    port.clear_dwic_isr(); port.spi_busy=False; port.isr_pending=False
    port.isr_overruns=0; port._in_service=False; irq._v=0

# ---- install: rising edge, SOFT irq (so the handler may do SPI / allocate) ----
fresh(); calls=[]
port.set_dwic_isr(lambda: calls.append(1))
assert irq._handler is not None and irq.trigger==irq.IRQ_RISING and irq.hard is False
print("installed: trigger=RISING hard=%s" % irq.hard)

# 1) one edge, line drops after the handler -> exactly one call
fresh(); n=[]
def h1(): n.append(1); irq._v=0
port.set_dwic_isr(h1); irq._v=1; irq._fire()
assert len(n)==1

# 2) line still high after the first pass (a 2nd event arrived with no new edge)
#    -> keeps servicing until it drops
fresh(); n=[]
def h2():
    n.append(1)
    if len(n)==3: irq._v=0
port.set_dwic_isr(h2); irq._v=1; irq._fire()
assert len(n)==3, n

# 3) IRQ lands in the MIDDLE of a main-loop SPI transaction: deferred, then run
#    right after that transaction ends (CS high, bus free), exactly once
fresh(); seen=[]
def h3():
    seen.append(dict(cs=port.cs.value(), busy=port.spi_busy)); irq._v=0
port.set_dwic_isr(h3)
fired=[]
def mid_txn():
    if not fired:
        fired.append(1); irq._v=1; irq._fire()      # edge arrives between spi.write() calls
        assert seen==[] and port.isr_pending, "must NOT run inside the transaction"
port.spi.on_write=mid_txn
S.writetospi(b'\x80',b'\x01\x02')
assert seen==[dict(cs=1,busy=False)], seen
assert not port.isr_pending
port.spi.on_write=None
# same for the read path
fresh(); seen.clear(); fired.clear(); port.set_dwic_isr(h3); port.spi.on_write=mid_txn
out=S.readfromspi(b'\x00',4)
assert len(out)==4 and seen==[dict(cs=1,busy=False)], (out,seen)
port.spi.on_write=None

# 4) edge arrives while the handler itself is running (and doing SPI) -> no nested
#    handler; one extra pass picks it up
fresh(); depth=[]; passes=[]
def h4():
    depth.append(1); assert len(depth)==1, "handler re-entered!"
    passes.append(1)
    S.writetospi(b'\x80',b'\x00')                       # handler talks SPI
    if len(passes)==1:
        irq._fire()                                      # a new edge during the handler
    else:
        irq._v=0
    depth.pop()
port.set_dwic_isr(h4); irq._v=1; irq._fire()
assert len(passes)==2, passes

# 5) line stuck high forever -> bounded, counted, and the next call still works
fresh(); n=[]
port.set_dwic_isr(lambda: n.append(1)); irq._v=1; irq._fire()
assert len(n)==P._ISR_MAX_PASSES and port.isr_overruns==1 and not port._in_service, (len(n),port.isr_overruns)
irq._v=0; n.clear(); irq._fire(); assert len(n)==1 and port.isr_overruns==1

# 6) a handler that raises must not wedge the dispatcher
fresh()
def bad(): raise ValueError("boom")
port.set_dwic_isr(bad); irq._v=1
try: irq._fire(); raise SystemExit("exception should propagate")
except ValueError: pass
assert port._in_service is False
n=[]; port.set_dwic_isr(lambda: (n.append(1), setattr(irq,'_v',0))); irq._v=1; irq._fire(); assert n==[1]

# 7) clear_dwic_isr stops delivery
fresh(); n=[]
port.set_dwic_isr(lambda: n.append(1)); port.clear_dwic_isr()
assert irq._handler is None; irq._v=1; irq._fire(); assert n==[]

# 8) spi_busy is always released, even if the SPI write raises
fresh()
def boom(): raise OSError("bus fault")
port.spi.on_write=boom
try: S.writetospi(b'\x80',b'\x00'); raise SystemExit("should raise")
except OSError: pass
port.spi.on_write=None
assert port.spi_busy is False and port.cs.value()==1

# 9) boards with no IRQ pin refuse instead of arming some unrelated GPIO
src=open(os.path.join(ROOT, 'platform/port.py')).read().replace('BOARD = "flexypin"','BOARD = "ardupico"')
ns={}; exec(compile(src,'port_ardupico','exec'),ns)
try: ns['Port']().set_dwic_isr(lambda:None); raise SystemExit("should raise")
except RuntimeError as e: print("ardupico ->",e)

print("PORT IRQ DISPATCH TESTS PASSED")
