"""
decadriver/deca_device.py

Target-independent DW3000 register-level driver. MicroPython port of (a
small, verified subset of) the upstream decadriver/deca_device.c.

*** SCOPE NOTE ***
The upstream deca_device.c is several thousand lines (dwt_initialise,
dwt_configure, TX/RX state machines, timestamp handling, STS/PDOA/AES
variants, OTP calibration loading, etc). Faithfully porting all of that
requires working line-by-line against the real source (and ideally
verifying against hardware), not guessing at register semantics from
memory. This file currently implements only:

  - dwt_xfer3000()   the core SPI header-encoding + transfer primitive,
                      for the "Fast Access Command with R/W support"
                      (FACRW) header case, i.e. register offset == 0.
                      This is the header format Qorvo's driver uses for
                      any register access where the field you're
                      touching starts at offset 0 within its file --
                      which includes DEV_ID and covers what ex_00a
                      needs. Extended (offset != 0) headers are NOT
                      YET ported (raises NotImplementedError) -- do not
                      guess at that encoding, port it properly against
                      deca_device.c when the next example needs it.
  - dwt_read32bitreg()
  - dwt_readdevid()

Everything else (dwt_initialise, dwt_configure, TX, RX, ...) is future
work -- see README.md roadmap.
"""

from platform.deca_spi import writetospi, readfromspi

DW3000_SPI_RD_BIT = 0
DW3000_SPI_WR_BIT = 1


def dwt_xfer3000(reg_id: int, length: int, mode: int, buffer: bytes = b""):
    """
    Read or write `length` bytes at combined register id `reg_id`
    (= (file << 16) | offset, see deca_regs.py).

    Only offset == 0 (the FACRW single-byte-header case) is implemented.
    Returns bytes read (mode == RD) or None (mode == WR).
    """
    reg_file = 0x1F & (reg_id >> 16)
    reg_offset = 0x7F & reg_id

    if reg_offset != 0:
        raise NotImplementedError(
            "dwt_xfer3000: extended (offset != 0) SPI header not yet "
            "ported -- see decadriver/deca_device.py scope note. "
            "Port this against the real dwt_xfer3000() in "
            "decadriver/deca_device.c before using registers with a "
            "nonzero offset."
        )

    # Fast Access Command with Read/Write support (FACRW), offset == 0:
    #   bit7 = R/W (1 = write, 0 = read)
    #   bit6 = 0   (marks this as FACRW, not a "pure" fast command)
    #   bits[5:1] = register file id
    #   bit0 = 0   (FACRW mode bit)
    rw_bit = 1 if mode == DW3000_SPI_WR_BIT else 0
    header = bytes([(rw_bit << 7) | ((reg_file & 0x1F) << 1)])

    if mode == DW3000_SPI_WR_BIT:
        writetospi(header, buffer)
        return None
    else:
        return readfromspi(header, length)


def dwt_read32bitreg(reg_id: int) -> int:
    data = dwt_xfer3000(reg_id, 4, DW3000_SPI_RD_BIT)
    # DW3000 registers are little-endian on the wire.
    return int.from_bytes(data, "little")


def dwt_readdevid() -> int:
    from decadriver.deca_regs import DEV_ID_ID
    return dwt_read32bitreg(DEV_ID_ID)
