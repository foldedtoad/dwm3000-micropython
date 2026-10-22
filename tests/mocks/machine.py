class Pin:
    OUT=0; IN=1; PULL_DOWN=2; IRQ_RISING=8
    def __init__(self,id,mode=None,pull=None,value=0):
        self.id=id; self._v=value; self._handler=None; self.trigger=None; self.hard=None
    def value(self,v=None):
        if v is None: return self._v
        self._v=v
    def irq(self,handler=None,trigger=None,hard=False):
        self._handler=handler; self.trigger=trigger; self.hard=hard
    def _fire(self):                      # test helper: deliver a rising edge
        if self._handler: self._handler(self)
class SPI:
    MSB=0
    def __init__(self,id,**kw): self.write_log=[]; self.on_write=None
    def init(self,**kw): pass
    def write(self,d):
        self.write_log.append(bytes(d))
        if self.on_write: self.on_write()
    def readinto(self,b): pass
