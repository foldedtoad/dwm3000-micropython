"""
decadriver/deca_device.py

Target-independent DW3000 register-level driver. MicroPython port of the
subset of upstream decadriver/deca_device.c needed to run ex_00a
(dev-ID read) and ex_01a (simple TX).

Every function here is a line-by-line transliteration of the real
upstream source (verified against the actual foldedtoad/dwm3000 repo,
not reconstructed from memory or guessed). Where the upstream code uses
C-specific patterns (uint8_t truncation, struct pointers, #define
macros), the Python equivalent is noted in a comment.

*** SCOPE ***
Implemented: dwt_xfer3000 (all three SPI header modes: FAC/FACRW/EAMRW),
the 8/16/32-bit offset read/write/modify register accessors, OTP read,
dwt_initialise, dwt_configure (non-SCP, PRF64 DGC-from-OTP or
hard-coded-LUT path), dwt_configuretxrf, dwt_writetxdata,
dwt_writetxfctrl, dwt_starttx, dwt_setleds, dwt_checkidlerc,
dwt_check_dev_id/dwt_readdevid.

NOT yet ported: RX path (dwt_rxenable, dwt_readrxdata, RX diagnostics),
STS/PDOA/AES variants, sleep modes, timestamps, ranging. These are
larger, separate chunks of deca_device.c -- port them the same way
(against the real source) when the next example needs them.
"""

import utime as time

from platform.deca_spi import writetospi, readfromspi
from decadriver import deca_regs as R


# ===========================================================================
# Local device state -- Python equivalent of the upstream's
# `static dwt_local_data_t DW3000local[...]` / `pdw3000local`.
# Single-device only (matches every example ported so far).
# ===========================================================================
class _LocalData:
    def __init__(self):
        self.dblbuffon = R.DBL_BUFF_OFF
        self.sleep_mode = R.DWT_RUNSAR
        self.spicrc = 0
        self.stsconfig = 0
        self.vBatP = 0
        self.tempP = 0
        self.bias_tune = 0
        self.dgc_otp_set = R.DWT_DGC_LOAD_FROM_SW
        self.longFrames = 0
        self.ststhreshold = 0
        self.partID = 0
        self.lotID = 0
        self.otprev = 0
        self.init_xtrim = 0


local = _LocalData()


# ===========================================================================
# Core SPI transaction primitive -- ported from dwt_xfer3000()
# ===========================================================================
def dwt_xfer3000(regFileID: int, indx: int, length: int, buffer: bytes, mode: int):
    """
    Read or write `length` bytes at (regFileID + indx). `mode` is one of
    DW3000_SPI_RD_BIT / WR_BIT / AND_OR_8 / AND_OR_16 / AND_OR_32.
    Returns the bytes read for RD_BIT, else None.
    """
    reg_file = 0x1F & ((regFileID + indx) >> 16)
    reg_offset = 0x7F & (regFileID + indx)

    addr = (reg_file << 9) | (reg_offset << 2)
    combined = (mode | addr) & 0xFFFF
    header0 = (combined >> 8) & 0xFF
    header1 = (addr | (mode & 0x03)) & 0xFF
    cnt = 2

    if length == 0:
        # Fast Access Command (FAC) -- write-only, single byte header.
        assert mode == R.DW3000_SPI_WR_BIT
        header0 = ((R.DW3000_SPI_WR_BIT >> 8) | ((regFileID << 1) & 0xFF) | R.DW3000_SPI_FAC) & 0xFF
        cnt = 1
    elif reg_offset == 0 and mode in (R.DW3000_SPI_WR_BIT, R.DW3000_SPI_RD_BIT):
        # Fast Access Command with Read/Write support (FACRW)
        header0 |= R.DW3000_SPI_FARW
        cnt = 1
    else:
        # Extended Address Mode with Read/Write support (EAMRW)
        header0 |= R.DW3000_SPI_EAMRW
        cnt = 2

    header = bytes([header0, header1])[:cnt]

    if mode in (R.DW3000_SPI_WR_BIT, R.DW3000_SPI_AND_OR_8, R.DW3000_SPI_AND_OR_16, R.DW3000_SPI_AND_OR_32):
        writetospi(header, bytes(buffer) if buffer else b"")
        return None
    elif mode == R.DW3000_SPI_RD_BIT:
        return readfromspi(header, length)
    else:
        raise ValueError("dwt_xfer3000: bad mode 0x%x" % mode)


def dwt_writetodevice(regFileID: int, index: int, length: int, buffer: bytes):
    dwt_xfer3000(regFileID, index, length, buffer, R.DW3000_SPI_WR_BIT)


def dwt_writefastCMD(cmd: int):
    dwt_xfer3000(cmd, 0, 0, b"", R.DW3000_SPI_WR_BIT)


def dwt_readfromdevice(regFileID: int, index: int, length: int) -> bytes:
    return dwt_xfer3000(regFileID, index, length, b"", R.DW3000_SPI_RD_BIT)


# ===========================================================================
# 8/16/32-bit offset register accessors
# ===========================================================================
def dwt_read32bitoffsetreg(regFileID: int, regOffset: int) -> int:
    buf = dwt_readfromdevice(regFileID, regOffset, 4)
    return int.from_bytes(buf, "little")


def dwt_read16bitoffsetreg(regFileID: int, regOffset: int) -> int:
    buf = dwt_readfromdevice(regFileID, regOffset, 2)
    return int.from_bytes(buf, "little")


def dwt_read8bitoffsetreg(regFileID: int, regOffset: int) -> int:
    buf = dwt_readfromdevice(regFileID, regOffset, 1)
    return buf[0]


def dwt_write32bitoffsetreg(regFileID: int, regOffset: int, regval: int):
    dwt_writetodevice(regFileID, regOffset, 4, (regval & 0xFFFFFFFF).to_bytes(4, "little"))


def dwt_write16bitoffsetreg(regFileID: int, regOffset: int, regval: int):
    dwt_writetodevice(regFileID, regOffset, 2, (regval & 0xFFFF).to_bytes(2, "little"))


def dwt_write8bitoffsetreg(regFileID: int, regOffset: int, regval: int):
    dwt_writetodevice(regFileID, regOffset, 1, bytes([regval & 0xFF]))


def dwt_modify32bitoffsetreg(regFileID: int, regOffset: int, _and: int, _or: int):
    buf = (_and & 0xFFFFFFFF).to_bytes(4, "little") + (_or & 0xFFFFFFFF).to_bytes(4, "little")
    dwt_xfer3000(regFileID, regOffset, len(buf), buf, R.DW3000_SPI_AND_OR_32)


def dwt_modify16bitoffsetreg(regFileID: int, regOffset: int, _and: int, _or: int):
    buf = (_and & 0xFFFF).to_bytes(2, "little") + (_or & 0xFFFF).to_bytes(2, "little")
    dwt_xfer3000(regFileID, regOffset, len(buf), buf, R.DW3000_SPI_AND_OR_16)


def dwt_modify8bitoffsetreg(regFileID: int, regOffset: int, _and: int, _or: int):
    buf = bytes([_and & 0xFF, _or & 0xFF])
    dwt_xfer3000(regFileID, regOffset, len(buf), buf, R.DW3000_SPI_AND_OR_8)


# convenience macros (deca_device_api.h #defines)
def dwt_or8bitoffsetreg(addr, offset, or_val):
    dwt_modify8bitoffsetreg(addr, offset, 0xFF, or_val)


def dwt_and8bitoffsetreg(addr, offset, and_val):
    dwt_modify8bitoffsetreg(addr, offset, and_val, 0)


def dwt_or16bitoffsetreg(addr, offset, or_val):
    dwt_modify16bitoffsetreg(addr, offset, 0xFFFF, or_val)


def dwt_and16bitoffsetreg(addr, offset, and_val):
    dwt_modify16bitoffsetreg(addr, offset, and_val, 0)


def dwt_and_or16bitoffsetreg(addr, offset, and_val, or_val):
    dwt_modify16bitoffsetreg(addr, offset, and_val, or_val)


def dwt_or32bitoffsetreg(addr, offset, or_val):
    dwt_modify32bitoffsetreg(addr, offset, 0xFFFFFFFF, or_val)


def dwt_and32bitoffsetreg(addr, offset, and_val):
    dwt_modify32bitoffsetreg(addr, offset, and_val, 0)


# offset-0 convenience wrappers (used throughout the examples)
def dwt_read32bitreg(regFileID: int) -> int:
    return dwt_read32bitoffsetreg(regFileID, 0)


def dwt_write32bitreg(regFileID: int, regval: int):
    dwt_write32bitoffsetreg(regFileID, 0, regval)


# ===========================================================================
# Device ID
# ===========================================================================
def dwt_readdevid() -> int:
    return dwt_read32bitreg(R.DEV_ID_ID)


def dwt_check_dev_id() -> int:
    dev_id = dwt_readdevid()
    if dev_id not in (R.DWT_C0_DEV_ID, R.DWT_C0_PDOA_DEV_ID):
        return R.DWT_ERROR
    return R.DWT_SUCCESS


# ===========================================================================
# Part ID / Lot ID -- factory-unique, OTP (fuse) backed
# ===========================================================================
def dwt_getpartid() -> int:
    """
    Factory-unique 32-bit Part ID, read from OTP (fuses). This is the
    real "which physical chip is this" identifier on the DW3000 -- unlike
    the EUI_64 register, which is plain writable RAM-style storage that
    reads back all-zero until firmware writes it, Part ID/Lot ID are
    burned in at manufacture and differ between every module.
    """
    return _dwt_otpread(R.PARTID_ADDRESS)


def dwt_getlotid() -> int:
    """Factory-unique Lot ID, read from OTP. See dwt_getpartid()."""
    return _dwt_otpread(R.LOTID_ADDRESS)


# ===========================================================================
# OTP
# ===========================================================================
def _dwt_otpread(address: int) -> int:
    dwt_write16bitoffsetreg(R.OTP_CFG_ID, 0, 0x0001)   # manual access mode
    dwt_write16bitoffsetreg(R.OTP_ADDR_ID, 0, address)  # set address
    dwt_write16bitoffsetreg(R.OTP_CFG_ID, 0, 0x0002)   # assert read strobe
    return dwt_read32bitoffsetreg(R.OTP_RDATA_ID, 0)


def dwt_otpread(address: int, length: int) -> list:
    return [_dwt_otpread(address + i) for i in range(length)]


# ===========================================================================
# LDO/bias tune, DGC OTP kick
# ===========================================================================
def _dwt_prog_ldo_and_bias_tune():
    dwt_or16bitoffsetreg(R.OTP_CFG_ID, 0, R.LDO_BIAS_KICK)
    dwt_and_or16bitoffsetreg(R.BIAS_CTRL_ID, 0, (~R.BIAS_CTRL_BIAS_MASK) & 0xFFFF, local.bias_tune)


def _dwt_kick_dgc_on_wakeup(channel: int):
    if channel == 5:
        dwt_modify32bitoffsetreg(
            R.OTP_CFG_ID, 0, (~R.OTP_CFG_DGC_SEL_BIT_MASK) & 0xFFFFFFFF,
            (R.DWT_DGC_SEL_CH5 << R.OTP_CFG_DGC_SEL_BIT_OFFSET) | R.OTP_CFG_DGC_KICK_BIT_MASK)
    elif channel == 9:
        dwt_modify32bitoffsetreg(
            R.OTP_CFG_ID, 0, (~R.OTP_CFG_DGC_SEL_BIT_MASK) & 0xFFFFFFFF,
            (R.DWT_DGC_SEL_CH9 << R.OTP_CFG_DGC_SEL_BIT_OFFSET) | R.OTP_CFG_DGC_KICK_BIT_MASK)


def dwt_configmrxlut(channel: int):
    if channel == 5:
        luts = (R.CH5_DGC_LUT_0, R.CH5_DGC_LUT_1, R.CH5_DGC_LUT_2, R.CH5_DGC_LUT_3,
                R.CH5_DGC_LUT_4, R.CH5_DGC_LUT_5, R.CH5_DGC_LUT_6)
    else:
        luts = (R.CH9_DGC_LUT_0, R.CH9_DGC_LUT_1, R.CH9_DGC_LUT_2, R.CH9_DGC_LUT_3,
                R.CH9_DGC_LUT_4, R.CH9_DGC_LUT_5, R.CH9_DGC_LUT_6)

    lut_regs = (R.DGC_LUT_0_CFG_ID, R.DGC_LUT_1_CFG_ID, R.DGC_LUT_2_CFG_ID, R.DGC_LUT_3_CFG_ID,
                R.DGC_LUT_4_CFG_ID, R.DGC_LUT_5_CFG_ID, R.DGC_LUT_6_CFG_ID)
    for reg, val in zip(lut_regs, luts):
        dwt_write32bitoffsetreg(reg, 0, val)

    dwt_write32bitoffsetreg(R.DGC_CFG0_ID, 0, R.DWT_DGC_CFG0)
    dwt_write32bitoffsetreg(R.DGC_CFG1_ID, 0, R.DWT_DGC_CFG1)


# ===========================================================================
# Clock / state control
# ===========================================================================
def dwt_force_clocks(clocks: int):
    FORCE_CLK_SYS_TX = 1
    FORCE_CLK_AUTO = 5
    FORCE_CLK_PLL = 2
    FORCE_SYSCLK_PLL = 2

    if clocks == FORCE_CLK_SYS_TX:
        regvalue0 = R.CLK_CTRL_TX_BUF_CLK_ON_BIT_MASK | R.CLK_CTRL_RX_BUF_CLK_ON_BIT_MASK
        regvalue0 |= FORCE_SYSCLK_PLL << R.CLK_CTRL_SYS_CLK_SEL_BIT_OFFSET
        regvalue0 |= FORCE_CLK_PLL << R.CLK_CTRL_TX_CLK_SEL_BIT_OFFSET
        dwt_write16bitoffsetreg(R.CLK_CTRL_ID, 0, regvalue0)

    if clocks == FORCE_CLK_AUTO:
        dwt_write16bitoffsetreg(R.CLK_CTRL_ID, 0, R.DWT_AUTO_CLKS)


_FORCE_CLK_AUTO = 5


def dwt_setdwstate(state: int):
    FORCE_SYSCLK_FOSC = 3
    FORCE_SYSCLK_FOSCDIV4 = 1

    if state == R.DWT_DW_IDLE:
        dwt_force_clocks(_FORCE_CLK_AUTO)
        dwt_or8bitoffsetreg(R.SEQ_CTRL_ID, 0x01, (R.SEQ_CTRL_AINIT2IDLE_BIT_MASK >> 8) & 0xFF)
    elif state == R.DWT_DW_IDLE_RC:
        dwt_or8bitoffsetreg(R.CLK_CTRL_ID, 0, FORCE_SYSCLK_FOSC)
        dwt_modify32bitoffsetreg(R.SEQ_CTRL_ID, 0x0, (~R.SEQ_CTRL_AINIT2IDLE_BIT_MASK) & 0xFFFFFFFF,
                                  R.SEQ_CTRL_FORCE2INIT_BIT_MASK)
        dwt_and8bitoffsetreg(R.SEQ_CTRL_ID, 0x2, (~(R.SEQ_CTRL_FORCE2INIT_BIT_MASK >> 16)) & 0xFF)
        dwt_force_clocks(_FORCE_CLK_AUTO)
    else:
        dwt_or8bitoffsetreg(R.CLK_CTRL_ID, 0, FORCE_SYSCLK_FOSCDIV4)
        dwt_modify32bitoffsetreg(R.SEQ_CTRL_ID, 0x0, (~R.SEQ_CTRL_AINIT2IDLE_BIT_MASK) & 0xFFFFFFFF,
                                  R.SEQ_CTRL_FORCE2INIT_BIT_MASK)
        dwt_and8bitoffsetreg(R.SEQ_CTRL_ID, 0x2, (~(R.SEQ_CTRL_FORCE2INIT_BIT_MASK >> 16)) & 0xFF)


def dwt_checkidlerc() -> bool:
    reg = dwt_read16bitoffsetreg(R.SYS_STATUS_ID, 2) << 16
    return (reg & R.SYS_STATUS_RCINIT_BIT_MASK) == R.SYS_STATUS_RCINIT_BIT_MASK


def dwt_checkirq() -> int:
    return dwt_read8bitoffsetreg(R.SYS_STATUS_ID, 0) & R.SYS_STATUS_IRQS_BIT_MASK


def dwt_enablegpioclocks():
    dwt_or32bitoffsetreg(R.CLK_CTRL_ID, 0, R.CLK_CTRL_GPIO_CLK_EN_BIT_MASK)


# ===========================================================================
# dwt_initialise
# ===========================================================================
def dwt_initialise(mode: int) -> int:
    local.dblbuffon = R.DBL_BUFF_OFF
    local.sleep_mode = R.DWT_RUNSAR
    local.spicrc = 0
    local.stsconfig = 0
    local.vBatP = 0
    local.tempP = 0

    if dwt_check_dev_id() != R.DWT_SUCCESS:
        return R.DWT_ERROR

    ldo_tune_lo = _dwt_otpread(R.LDOTUNELO_ADDRESS)
    ldo_tune_hi = _dwt_otpread(R.LDOTUNEHI_ADDRESS)
    local.bias_tune = (_dwt_otpread(R.BIAS_TUNE_ADDRESS) >> 16) & R.BIAS_CTRL_BIAS_MASK

    if ldo_tune_lo != 0 and ldo_tune_hi != 0 and local.bias_tune != 0:
        _dwt_prog_ldo_and_bias_tune()

    if _dwt_otpread(R.DGC_TUNE_ADDRESS) == R.DWT_DGC_CFG0:
        local.dgc_otp_set = R.DWT_DGC_LOAD_FROM_OTP
    else:
        local.dgc_otp_set = R.DWT_DGC_LOAD_FROM_SW

    if mode & R.DWT_READ_OTP_PID:
        local.partID = _dwt_otpread(R.PARTID_ADDRESS)
    if mode & R.DWT_READ_OTP_LID:
        local.lotID = _dwt_otpread(R.LOTID_ADDRESS)
    if mode & R.DWT_READ_OTP_BAT:
        local.vBatP = _dwt_otpread(R.VBAT_ADDRESS) & 0xFF
    if mode & R.DWT_READ_OTP_TMP:
        local.tempP = _dwt_otpread(R.VTEMP_ADDRESS) & 0xFF

    if local.tempP == 0:
        local.tempP = 0x85
    if local.vBatP == 0:
        local.vBatP = 0x74

    local.otprev = _dwt_otpread(R.OTPREV_ADDRESS) & 0xFF

    local.init_xtrim = _dwt_otpread(R.XTRIM_ADDRESS) & 0x7F
    if local.init_xtrim == 0:
        local.init_xtrim = 0x2E
    dwt_write8bitoffsetreg(R.XTAL_ID, 0, local.init_xtrim)

    return R.DWT_SUCCESS


# ===========================================================================
# PGF calibration
# ===========================================================================
def dwt_run_pgfcal() -> int:
    result = R.DWT_SUCCESS

    data = (0x02 << R.RX_CAL_CFG_COMP_DLY_BIT_OFFSET) | (R.RX_CAL_CFG_CAL_MODE_BIT_MASK & 0x1)
    dwt_write32bitoffsetreg(R.RX_CAL_CFG_ID, 0x0, data)
    dwt_or8bitoffsetreg(R.RX_CAL_CFG_ID, 0x0, R.RX_CAL_CFG_CAL_EN_BIT_MASK)

    flag = True
    for _ in range(R.MAX_RETRIES_FOR_PGF):
        time.sleep_us(R.DELAY_20uUSec)
        if dwt_read8bitoffsetreg(R.RX_CAL_STS_ID, 0x0) == 1:
            flag = False
            break
    if flag:
        result = R.DWT_ERROR

    dwt_write8bitoffsetreg(R.RX_CAL_CFG_ID, 0x0, 0)
    dwt_write8bitoffsetreg(R.RX_CAL_STS_ID, 0x0, 1)
    dwt_or8bitoffsetreg(R.RX_CAL_CFG_ID, 0x2, 0x1)

    if dwt_read32bitoffsetreg(R.RX_CAL_RESI_ID, 0x0) == R.ERR_RX_CAL_FAIL:
        result = R.DWT_ERROR
    if dwt_read32bitoffsetreg(R.RX_CAL_RESQ_ID, 0x0) == R.ERR_RX_CAL_FAIL:
        result = R.DWT_ERROR

    return result


def dwt_pgf_cal(ldoen: int) -> int:
    val = 0
    if ldoen == 1:
        val = dwt_read16bitoffsetreg(R.LDO_CTRL_ID, 0)
        dwt_or16bitoffsetreg(R.LDO_CTRL_ID, 0,
                              R.LDO_CTRL_LDO_VDDIF2_EN_BIT_MASK |
                              R.LDO_CTRL_LDO_VDDMS3_EN_BIT_MASK |
                              R.LDO_CTRL_LDO_VDDMS1_EN_BIT_MASK)

    temp = dwt_run_pgfcal()

    if ldoen == 1:
        dwt_and16bitoffsetreg(R.LDO_CTRL_ID, 0, val)

    return temp


# ===========================================================================
# dwt_configure
# ===========================================================================
def _get_sts_mnth(cipher: int, threshold: int, shift_val: int) -> int:
    value = cipher * threshold
    if shift_val == 3:
        value = (value * R.SQRT_FACTOR) >> R.SQRT_SHIFT_VAL
    mod_val = (value % R.MOD_VALUE) + R.HALF_MOD
    value >>= R.SHIFT_VALUE
    if mod_val >= R.MOD_VALUE:
        value += 1
    return value & 0xFFFF


def dwt_setplenfine(preamble_length: int):
    dwt_write8bitoffsetreg(R.TX_FCTRL_HI_ID, 1, preamble_length)


_PREAMBLE_LEN_SYMBOLS = {
    R.DWT_PLEN_32: 32, R.DWT_PLEN_64: 64, R.DWT_PLEN_72: 72, R.DWT_PLEN_128: 128,
}


def dwt_configure(config) -> int:
    """
    config: object/namespace with attributes matching dwt_config_t:
    chan, txPreambLength, rxPAC, txCode, rxCode, sfdType, dataRate,
    phrMode, phrRate, sfdTO, stsMode, stsLength, pdoaMode.
    """
    chan = config.chan
    scp = 1 if (config.rxCode > 24 or config.txCode > 24) else 0
    mode = R.SYS_CFG_PHR_MODE_BIT_MASK if config.phrMode == R.DWT_PHRMODE_EXT else 0

    preamble_len = _PREAMBLE_LEN_SYMBOLS.get(config.txPreambLength, 256)

    local.sleep_mode &= ~(R.DWT_ALT_OPS | R.DWT_SEL_OPS3)
    local.longFrames = config.phrMode
    sts_len = R.GET_STS_REG_SET_VALUE(config.stsLength)
    local.ststhreshold = int((sts_len * 8) * R.STSQUAL_THRESH_64)
    local.stsconfig = config.stsMode

    # SYS_CFG
    dwt_modify32bitoffsetreg(
        R.SYS_CFG_ID, 0,
        (~(R.SYS_CFG_PHR_MODE_BIT_MASK | R.SYS_CFG_PHR_6M8_BIT_MASK |
           R.SYS_CFG_CP_SPC_BIT_MASK | R.SYS_CFG_PDOA_MODE_BIT_MASK |
           R.SYS_CFG_CP_SDC_BIT_MASK)) & 0xFFFFFFFF,
        (config.pdoaMode << R.SYS_CFG_PDOA_MODE_BIT_OFFSET)
        | ((config.stsMode & R.DWT_STS_CONFIG_MASK) << R.SYS_CFG_CP_SPC_BIT_OFFSET)
        | (R.SYS_CFG_PHR_6M8_BIT_MASK & (config.phrRate << R.SYS_CFG_PHR_6M8_BIT_OFFSET))
        | mode)

    if scp:
        local.sleep_mode |= R.DWT_ALT_OPS | R.DWT_SEL_OPS1
        dwt_modify32bitoffsetreg(R.OTP_CFG_ID, 0, (~R.OTP_CFG_OPS_ID_BIT_MASK) & 0xFFFFFFFF,
                                  R.DWT_OPSET_SCP | R.OTP_CFG_OPS_KICK_BIT_MASK)
        raise NotImplementedError(
            "dwt_configure: SCP-mode RF register writes not yet ported "
            "(IP_CONFIG_*_SCP / STS_CONFIG_*_SCP constants) -- not needed "
            "by ex_01a_simple_tx (txCode/rxCode=9, scp=0)."
        )
    else:
        if config.stsMode != R.DWT_STS_MODE_OFF:
            if config.pdoaMode in (R.DWT_PDOA_M1, R.DWT_PDOA_M0):
                sts_mnth = _get_sts_mnth(sts_len, R.CIA_MANUALLOWERBOUND_TH_64, 3)
            else:
                sts_mnth = _get_sts_mnth(sts_len, R.CIA_MANUALLOWERBOUND_TH_64, 4)
            preamble_len += sts_len * 8
            dwt_modify16bitoffsetreg(R.STS_CONFIG_LO_ID, 2,
                                      (~(R.STS_CONFIG_LO_STS_MAN_TH_BIT_MASK >> 16)) & 0xFFFF,
                                      sts_mnth & 0x7F)

        if preamble_len >= 256:
            local.sleep_mode |= R.DWT_ALT_OPS | R.DWT_SEL_OPS0
            dwt_modify32bitoffsetreg(R.OTP_CFG_ID, 0, (~R.OTP_CFG_OPS_ID_BIT_MASK) & 0xFFFFFFFF,
                                      R.DWT_OPSET_LONG | R.OTP_CFG_OPS_KICK_BIT_MASK)
        else:
            dwt_modify32bitoffsetreg(R.OTP_CFG_ID, 0, (~R.OTP_CFG_OPS_ID_BIT_MASK) & 0xFFFFFFFF,
                                      R.DWT_OPSET_SHORT | R.OTP_CFG_OPS_KICK_BIT_MASK)

    dwt_modify8bitoffsetreg(R.DTUNE0_ID, 0, (~R.DTUNE0_PRE_PAC_SYM_BIT_MASK) & 0xFF, config.rxPAC)

    dwt_write8bitoffsetreg(R.STS_CFG0_ID, 0, sts_len - 1)

    if config.txPreambLength == R.DWT_PLEN_72:
        dwt_setplenfine(8)
    else:
        dwt_setplenfine(0)

    if (config.stsMode & R.DWT_STS_MODE_ND) == R.DWT_STS_MODE_ND:
        dwt_write32bitoffsetreg(R.DTUNE3_ID, 0, R.PD_THRESH_NO_DATA)
    else:
        dwt_write32bitoffsetreg(R.DTUNE3_ID, 0, R.PD_THRESH_DEFAULT)

    # CHAN_CTRL
    temp = dwt_read32bitoffsetreg(R.CHAN_CTRL_ID, 0)
    temp &= ~(R.CHAN_CTRL_RX_PCODE_BIT_MASK | R.CHAN_CTRL_TX_PCODE_BIT_MASK |
              R.CHAN_CTRL_SFD_TYPE_BIT_MASK | R.CHAN_CTRL_RF_CHAN_BIT_MASK)
    if chan == 9:
        temp |= R.CHAN_CTRL_RF_CHAN_BIT_MASK
    temp |= R.CHAN_CTRL_RX_PCODE_BIT_MASK & (config.rxCode << R.CHAN_CTRL_RX_PCODE_BIT_OFFSET)
    temp |= R.CHAN_CTRL_TX_PCODE_BIT_MASK & (config.txCode << R.CHAN_CTRL_TX_PCODE_BIT_OFFSET)
    temp |= R.CHAN_CTRL_SFD_TYPE_BIT_MASK & (config.sfdType << R.CHAN_CTRL_SFD_TYPE_BIT_OFFSET)
    dwt_write32bitoffsetreg(R.CHAN_CTRL_ID, 0, temp)

    # TX_FCTRL: preamble size, PRF, data rate
    dwt_modify32bitoffsetreg(
        R.TX_FCTRL_ID, 0,
        (~(R.TX_FCTRL_TXBR_BIT_MASK | R.TX_FCTRL_TXPSR_BIT_MASK)) & 0xFFFFFFFF,
        (config.dataRate << R.TX_FCTRL_TXBR_BIT_OFFSET) | (config.txPreambLength << R.TX_FCTRL_TXPSR_BIT_OFFSET))

    if config.sfdTO == 0:
        config.sfdTO = R.DWT_SFDTOC_DEF
    dwt_write16bitoffsetreg(R.DTUNE0_ID, 2, config.sfdTO)

    # RF analog setup
    if chan == 9:
        dwt_write32bitoffsetreg(R.TX_CTRL_HI_ID, 0, R.RF_TXCTRL_CH9)
        dwt_write16bitoffsetreg(R.PLL_CFG_ID, 0, R.RF_PLL_CFG_CH9)
        dwt_write32bitoffsetreg(R.RX_CTRL_HI_ID, 0, R.RF_RXCTRL_CH9)
    else:
        dwt_write32bitoffsetreg(R.TX_CTRL_HI_ID, 0, R.RF_TXCTRL_CH5)
        dwt_write16bitoffsetreg(R.PLL_CFG_ID, 0, R.RF_PLL_CFG_CH5)

    dwt_write8bitoffsetreg(R.LDO_RLOAD_ID, 1, R.LDO_RLOAD_VAL_B1)
    dwt_write8bitoffsetreg(R.TX_CTRL_LO_ID, 2, R.RF_TXCTRL_LO_B2)
    dwt_write8bitoffsetreg(R.PLL_CAL_ID, 0, R.RF_PLL_CFG_LD)

    dwt_write8bitoffsetreg(R.SYS_STATUS_ID, 0, R.SYS_STATUS_CP_LOCK_BIT_MASK)

    # Auto-cal PLL, IDLE_PLL state
    dwt_setdwstate(R.DWT_DW_IDLE)

    flag = True
    for _ in range(R.MAX_RETRIES_FOR_PLL):
        time.sleep_us(R.DELAY_20uUSec)
        if dwt_read8bitoffsetreg(R.SYS_STATUS_ID, 0) & R.SYS_STATUS_CP_LOCK_BIT_MASK:
            flag = False
            break
    if flag:
        return R.DWT_ERROR

    if 9 <= config.rxCode <= 24:
        if local.dgc_otp_set == R.DWT_DGC_LOAD_FROM_OTP:
            _dwt_kick_dgc_on_wakeup(chan)
        else:
            dwt_configmrxlut(chan)
        dwt_modify16bitoffsetreg(R.DGC_CFG_ID, 0x0, (~R.DGC_CFG_THR_64_BIT_MASK) & 0xFFFF,
                                  R.DWT_DGC_CFG << R.DGC_CFG_THR_64_BIT_OFFSET)
    else:
        dwt_and8bitoffsetreg(R.DGC_CFG_ID, 0x0, (~R.DGC_CFG_RX_TUNE_EN_BIT_MASK) & 0xFF)

    # PGF calibration
    return dwt_pgf_cal(1)


# ===========================================================================
# TX RF config
# ===========================================================================
def dwt_configuretxrf(txconfig):
    """txconfig: object with .PGdly, .power, .PGcount"""
    if txconfig.PGcount == 0:
        dwt_write8bitoffsetreg(R.TX_CTRL_HI_ID, 0, txconfig.PGdly)
    else:
        raise NotImplementedError(
            "dwt_configuretxrf: PGcount-based dwt_calcbandwidthadj() not "
            "ported (not used by ex_01a_simple_tx's txconfig_options, "
            "which has PGcount=0)."
        )
    dwt_write32bitreg(R.TX_POWER_ID, txconfig.power)


# ===========================================================================
# LEDs
# ===========================================================================
def dwt_setleds(mode: int):
    if mode & R.DWT_LEDS_ENABLE:
        dwt_modify32bitoffsetreg(
            R.GPIO_MODE_ID, 0,
            (~(R.GPIO_MODE_MSGP3_MODE_BIT_MASK | R.GPIO_MODE_MSGP2_MODE_BIT_MASK)) & 0xFFFFFFFF,
            R.GPIO_PIN2_RXLED | R.GPIO_PIN3_TXLED)

        dwt_or32bitoffsetreg(R.CLK_CTRL_ID, 0,
                              R.CLK_CTRL_GPIO_DCLK_EN_BIT_MASK | R.CLK_CTRL_LP_CLK_EN_BIT_MASK)

        reg = R.LED_CTRL_BLINK_EN_BIT_MASK | R.DWT_LEDS_BLINK_TIME_DEF
        if mode & R.DWT_LEDS_INIT_BLINK:
            reg |= R.LED_CTRL_FORCE_TRIGGER_BIT_MASK
        dwt_write32bitreg(R.LED_CTRL_ID, reg)

        if mode & R.DWT_LEDS_INIT_BLINK:
            reg &= ~R.LED_CTRL_FORCE_TRIGGER_BIT_MASK
            dwt_write32bitreg(R.LED_CTRL_ID, reg)
    else:
        dwt_and32bitoffsetreg(R.GPIO_MODE_ID, 0,
                               (~(R.GPIO_MODE_MSGP2_MODE_BIT_MASK | R.GPIO_MODE_MSGP3_MODE_BIT_MASK)) & 0xFFFFFFFF)
        dwt_and16bitoffsetreg(R.LED_CTRL_ID, 0, (~R.LED_CTRL_BLINK_EN_BIT_MASK) & 0xFFFF)


# ===========================================================================
# TX data / frame control / start
# ===========================================================================
def dwt_writetxdata(txDataLength: int, txDataBytes: bytes, txBufferOffset: int) -> int:
    if (txBufferOffset + txDataLength) >= R.TX_BUFFER_MAX_LEN:
        return R.DWT_ERROR

    if txBufferOffset <= R.REG_DIRECT_OFFSET_MAX_LEN:
        dwt_writetodevice(R.TX_BUFFER_ID, txBufferOffset, txDataLength, txDataBytes)
    else:
        dwt_write32bitreg(R.INDIRECT_ADDR_A_ID, (R.TX_BUFFER_ID >> 16))
        dwt_write32bitreg(R.ADDR_OFFSET_A_ID, txBufferOffset)
        dwt_writetodevice(R.INDIRECT_POINTER_A_ID, 0, txDataLength, txDataBytes)

    return R.DWT_SUCCESS


def dwt_writetxfctrl(txFrameLength: int, txBufferOffset: int, ranging: int):
    if txBufferOffset <= 127:
        reg32 = txFrameLength | (txBufferOffset << R.TX_FCTRL_TXB_OFFSET_BIT_OFFSET) | (ranging << R.TX_FCTRL_TR_BIT_OFFSET)
        dwt_modify32bitoffsetreg(
            R.TX_FCTRL_ID, 0,
            (~(R.TX_FCTRL_TXB_OFFSET_BIT_MASK | R.TX_FCTRL_TR_BIT_MASK | R.TX_FCTRL_TXFLEN_BIT_MASK)) & 0xFFFFFFFF,
            reg32)
    else:
        reg32 = (txFrameLength | ((txBufferOffset + R.DWT_TX_BUFF_OFFSET_ADJUST) << R.TX_FCTRL_TXB_OFFSET_BIT_OFFSET)
                 | (ranging << R.TX_FCTRL_TR_BIT_OFFSET))
        dwt_modify32bitoffsetreg(
            R.TX_FCTRL_ID, 0,
            (~(R.TX_FCTRL_TXB_OFFSET_BIT_MASK | R.TX_FCTRL_TR_BIT_MASK | R.TX_FCTRL_TXFLEN_BIT_MASK)) & 0xFFFFFFFF,
            reg32)
        dwt_read8bitoffsetreg(R.SAR_CTRL_ID, 0)  # load correct TX buffer offset (per upstream note)


def dwt_starttx(mode: int) -> int:
    retval = R.DWT_SUCCESS

    delayed = bool(mode & (R.DWT_START_TX_DELAYED | R.DWT_START_TX_DLY_REF |
                            R.DWT_START_TX_DLY_RS | R.DWT_START_TX_DLY_TS))

    if delayed:
        resp = bool(mode & R.DWT_RESPONSE_EXPECTED)
        if mode & R.DWT_START_TX_DELAYED:
            dwt_writefastCMD(R.CMD_DTX_W4R if resp else R.CMD_DTX)
        elif mode & R.DWT_START_TX_DLY_RS:
            dwt_writefastCMD(R.CMD_DTX_RS_W4R if resp else R.CMD_DTX_RS)
        elif mode & R.DWT_START_TX_DLY_TS:
            dwt_writefastCMD(R.CMD_DTX_TS_W4R if resp else R.CMD_DTX_TS)
        else:
            dwt_writefastCMD(R.CMD_DTX_REF_W4R if resp else R.CMD_DTX_REF)

        checkTxOK = dwt_read8bitoffsetreg(R.SYS_STATUS_ID, 3)
        if (checkTxOK & ((R.SYS_STATUS_HPDWARN_BIT_MASK >> 24) & 0xFF)) == 0:
            sys_state = dwt_read32bitreg(R.SYS_STATE_LO_ID)
            if sys_state == R.DW_SYS_STATE_TXERR:
                dwt_writefastCMD(R.CMD_TXRXOFF)
                retval = R.DWT_ERROR
            else:
                retval = R.DWT_SUCCESS
        else:
            dwt_writefastCMD(R.CMD_TXRXOFF)
            retval = R.DWT_ERROR

    elif mode & R.DWT_START_TX_CCA:
        resp = bool(mode & R.DWT_RESPONSE_EXPECTED)
        dwt_writefastCMD(R.CMD_CCA_TX_W4R if resp else R.CMD_CCA_TX)

    else:
        resp = bool(mode & R.DWT_RESPONSE_EXPECTED)
        dwt_writefastCMD(R.CMD_TX_W4R if resp else R.CMD_TX)

    return retval


def dwt_forcetrxoff():
    dwt_writefastCMD(R.CMD_TXRXOFF)


# ===========================================================================
# Antenna delay (trivial one-liners, kept for later examples)
# ===========================================================================
def dwt_setrxantennadelay(rxDelay: int):
    dwt_write16bitoffsetreg(R.CIA_CONF_ID, 0, rxDelay)


def dwt_settxantennadelay(txDelay: int):
    dwt_write16bitoffsetreg(R.TX_ANTD_ID, 0, txDelay)
