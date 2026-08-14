"""
platform/deca_spi.py

Low-level SPI primitives for the DW3000, MicroPython equivalent of
platform/deca_spi.c's openspi()/writetospi()/readfromspi(). decadriver
(target-independent) calls these two functions; only this file and
port.py know about real hardware pins.

The DW3000 SPI protocol is: assert CS, clock out a 1-3 byte header that
encodes register-file/offset/R-W, then clock the data body in the same
CS-asserted transaction, then release CS. Both functions below take a
pre-built header buffer -- header construction (which varies by register
file/offset per the DW3000 SPI spec) lives in decadriver/deca_device.py,
matching the upstream project's split between "build the header" (target
independent) and "shove bytes over the wire" (target specific).
"""

from platform.port import port


def writetospi(header: bytes, body: bytes) -> None:
    """
    Write `header` immediately followed by `body` in a single SPI
    transaction (CS held low across both).
    """
    port.cs.value(0)
    try:
        port.spi.write(header)
        if body:
            port.spi.write(body)
    finally:
        port.cs.value(1)


def readfromspi(header: bytes, readlength: int) -> bytes:
    """
    Write `header`, then clock in `readlength` bytes, CS held low across
    both. Returns the bytes read (NOT including the header).
    """
    port.cs.value(0)
    try:
        port.spi.write(header)
        buf = bytearray(readlength)
        port.spi.readinto(buf)
    finally:
        port.cs.value(1)
    return bytes(buf)
