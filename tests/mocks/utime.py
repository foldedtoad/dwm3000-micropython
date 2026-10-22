_t=[0]
def sleep_ms(ms): pass
def sleep_us(us): pass
def ticks_us():
    _t[0]+=250   # deterministic fake clock: 250 us per call
    return _t[0]
def ticks_diff(a,b): return a-b
