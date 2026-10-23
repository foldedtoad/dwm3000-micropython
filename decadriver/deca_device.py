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
from decadriver.deca_types import DwtCbData, DwtRxDiag, DwtDeviceEntCnts


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
        # Interrupt callbacks (dwt_setcallbacks) and the single reused
        # callback-data object handed to them by dwt_isr().
        self.cbTxDone = None
        self.cbRxOk = None
        self.cbRxTo = None
        self.cia_diagnostic = 0     # CIA diagnostic logging level (dwt_configciadiag)
        self.cbRxErr = None
        self.cbSPIErr = None
        self.cbSPIRdy = None
        self.cbData = DwtCbData()


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


# ===========================================================================
# RX path
# ===========================================================================
def dwt_readrxdata(length: int, rxBufferOffset: int = 0) -> bytes:
    """
    Read `length` bytes from the RX buffer starting at `rxBufferOffset`.
    Returns the bytes (the C version fills a caller-supplied buffer).
    Returns b"" if the requested range exceeds the RX buffer, mirroring the
    upstream range check (which silently does nothing in that case).
    """
    if local.dblbuffon == R.DBL_BUFF_ACCESS_BUFFER_1:
        rx_buff_addr = R.RX_BUFFER_1_ID
    else:  # RX_BUFFER_0 -- also used in non-double-buffer mode
        rx_buff_addr = R.RX_BUFFER_0_ID

    if (rxBufferOffset + length) > R.RX_BUFFER_MAX_LEN:
        return b""

    if rxBufferOffset <= R.REG_DIRECT_OFFSET_MAX_LEN:
        return dwt_readfromdevice(rx_buff_addr, rxBufferOffset, length)

    # Indirect access for offsets beyond the direct-access window
    dwt_write32bitreg(R.INDIRECT_ADDR_A_ID, (rx_buff_addr >> 16))
    dwt_write32bitreg(R.ADDR_OFFSET_A_ID, rxBufferOffset)
    return dwt_readfromdevice(R.INDIRECT_POINTER_A_ID, 0, length)


def dwt_rxenable(mode: int) -> int:
    """Turn the receiver on, immediately or delayed. Returns DWT_SUCCESS/DWT_ERROR."""
    if mode == R.DWT_START_RX_IMMEDIATE:
        dwt_writefastCMD(R.CMD_RX)
        return R.DWT_SUCCESS

    # delayed RX
    sel = mode & ~R.DWT_IDLE_ON_DLY_ERR
    if sel == R.DWT_START_RX_DELAYED:
        dwt_writefastCMD(R.CMD_DRX)
    elif sel == R.DWT_START_RX_DLY_REF:
        dwt_writefastCMD(R.CMD_DRX_REF)
    elif sel == R.DWT_START_RX_DLY_RS:
        dwt_writefastCMD(R.CMD_DRX_RS)
    elif sel == R.DWT_START_RX_DLY_TS:
        dwt_writefastCMD(R.CMD_DRX_TS)
    else:
        return R.DWT_ERROR

    temp1 = dwt_read8bitoffsetreg(R.SYS_STATUS_ID, 3)  # 4th byte of the 5-byte status reg
    if (temp1 & ((R.SYS_STATUS_HPDWARN_BIT_MASK >> 24) & 0xFF)) != 0:
        # delay already passed: turn off, and unless told not to, RX immediately
        dwt_writefastCMD(R.CMD_TXRXOFF)
        if (mode & R.DWT_IDLE_ON_DLY_ERR) == 0:
            dwt_writefastCMD(R.CMD_RX)
        return R.DWT_ERROR

    return R.DWT_SUCCESS


# ===========================================================================
# RX-after-TX delay and RX timeout (used by TX-then-wait-for-response)
# ===========================================================================
def dwt_setrxaftertxdelay(rxDelayTime: int):
    """
    Delay, in UWB microseconds (20 bits), from the end of a transmission to
    turning the receiver on. Applies to TX started with DWT_RESPONSE_EXPECTED.
    """
    val = dwt_read32bitreg(R.ACK_RESP_ID)
    val &= ~R.ACK_RESP_W4R_TIM_BIT_MASK
    val |= (rxDelayTime & R.ACK_RESP_W4R_TIM_BIT_MASK)
    dwt_write32bitoffsetreg(R.ACK_RESP_ID, 0, val)


def dwt_setrxtimeout(time_uus: int):
    """
    Enable the RX frame-wait timeout (SYS_STATUS RXFTO event). `time_uus` is
    how long the receiver stays on after RX enable, in units of 1.0256 us
    (512/499.2 MHz). 0 disables the timeout.
    """
    if time_uus > 0:
        dwt_write32bitoffsetreg(R.RX_FWTO_ID, 0, time_uus)
        dwt_or16bitoffsetreg(R.SYS_CFG_ID, 0, R.SYS_CFG_RXWTOE_BIT_MASK)
    else:
        dwt_and16bitoffsetreg(R.SYS_CFG_ID, 0, (~R.SYS_CFG_RXWTOE_BIT_MASK) & 0xFFFF)


# ===========================================================================
# Interrupts: callbacks, enable mask, and the interrupt service routine
# ===========================================================================
def dwt_setcallbacks(cbTxDone, cbRxOk, cbRxTo, cbRxErr, cbSPIErr=None, cbSPIRdy=None):
    """
    Register the functions dwt_isr() calls for each event class. Each takes one
    argument (a DwtCbData). Pass None for any you don't need; the ISR then
    skips that call, saving interrupt processing time.
    """
    local.cbTxDone = cbTxDone
    local.cbRxOk = cbRxOk
    local.cbRxTo = cbRxTo
    local.cbRxErr = cbRxErr
    local.cbSPIErr = cbSPIErr
    local.cbSPIRdy = cbSPIRdy


def dwt_setinterrupt(bitmask_lo: int, bitmask_hi: int, INT_options: int):
    """
    Choose which SYS_STATUS events raise the IRQ line.
    DWT_ENABLE_INT_ONLY: the masks replace the enable registers.
    DWT_ENABLE_INT:      the mask bits are added (OR-ed in).
    DWT_DISABLE_INT:     the mask bits are removed.
    """
    if INT_options == R.DWT_ENABLE_INT_ONLY:
        dwt_write32bitreg(R.SYS_ENABLE_LO_ID, bitmask_lo)
        dwt_write32bitreg(R.SYS_ENABLE_HI_ID, bitmask_hi)
    elif INT_options == R.DWT_ENABLE_INT:
        dwt_or32bitoffsetreg(R.SYS_ENABLE_LO_ID, 0, bitmask_lo)
        dwt_or32bitoffsetreg(R.SYS_ENABLE_HI_ID, 0, bitmask_hi)
    else:
        dwt_and32bitoffsetreg(R.SYS_ENABLE_LO_ID, 0, (~bitmask_lo) & 0xFFFFFFFF)
        dwt_and32bitoffsetreg(R.SYS_ENABLE_HI_ID, 0, (~bitmask_hi) & 0xFFFFFFFF)


def dwt_isr():
    """
    The DW3000's general interrupt service routine: reads which events fired,
    clears them, and calls the registered callbacks. Handles TX done, RX good
    frame, RX timeout, RX error, SPI-ready and SPI-error events. Does NOT
    support double-buffered RX or automatic RX re-enabling (same as upstream).

    Ported from upstream dwt_isr(), single-RX-buffer mode only.
    """
    cb = local.cbData

    # Read the fast status register, then the full status register.
    fstat = dwt_read8bitoffsetreg(R.FINT_STAT_ID, 0)
    status = dwt_read32bitreg(R.SYS_STATUS_ID)
    cb.status = status

    nodata_sts = (local.stsconfig & R.DWT_STS_MODE_ND) == R.DWT_STS_MODE_ND
    if nodata_sts:
        # FSTAT can't be used in no-data STS mode: a frame is reported by RXFR.
        if status & R.SYS_STATUS_RXFR_BIT_MASK:
            fstat |= R.FINT_STAT_RXOK_BIT_MASK

    # System panic (AES_ERR|SPICRCERR|BRNOUT|SPI_UNF|SPI_OVR|CMD_ERR|SPI_COLLISION|PLLHILO)
    if fstat & R.FINT_STAT_SYS_PANIC_BIT_MASK:
        cb.status_hi = dwt_read16bitoffsetreg(R.SYS_STATUS_HI_ID, 0)
        spi_err_bits = (R.SYS_STATUS_HI_SPIERR_BIT_MASK | R.SYS_STATUS_HI_SPI_UNF_BIT_MASK |
                        R.SYS_STATUS_HI_SPI_OVF_BIT_MASK)

        # SPI CRC error on a write, or an SPI error: the last SPI transaction
        # did not complete correctly and the device should be reset.
        if (local.spicrc and (cb.status & R.SYS_STATUS_SPICRCE_BIT_MASK)) or (cb.status_hi & spi_err_bits):
            dwt_write32bitreg(R.SYS_STATUS_ID, R.SYS_STATUS_SPICRCE_BIT_MASK)
            dwt_write16bitoffsetreg(R.SYS_STATUS_HI_ID, 0, spi_err_bits)
            if local.cbSPIErr is not None:
                local.cbSPIErr(cb)

        # Fast-command error: the last command did not execute (e.g. it was
        # issued while the device was busy with the previous one).
        if cb.status_hi & R.SYS_STATUS_HI_CMD_ERR_BIT_MASK:
            dwt_write16bitoffsetreg(R.SYS_STATUS_HI_ID, 0, R.SYS_STATUS_HI_CMD_ERR_BIT_MASK)

    # TX frame sent. Events are cleared before the callback so the callback may
    # schedule another TX/RX.
    if fstat & R.FINT_STAT_TXOK_BIT_MASK:
        dwt_write8bitoffsetreg(R.SYS_STATUS_ID, 0, R.SYS_STATUS_ALL_TX & 0xFF)
        if local.cbTxDone is not None:
            local.cbTxDone(cb)

    # SPI ready / IDLE_RC: set when the device powers on or wakes up. Cleared
    # after the callback so the callback can still read SYS_STATUS.
    if fstat & R.FINT_STAT_SYS_EVENT_BIT_MASK:
        if local.cbSPIRdy is not None:
            local.cbSPIRdy(cb)
        dwt_write16bitoffsetreg(R.SYS_STATUS_ID, 2,
                                (R.SYS_STATUS_RCINIT_BIT_MASK | R.SYS_STATUS_SPIRDY_BIT_MASK) >> 16)

    # RX good frame.
    if fstat & R.FINT_STAT_RXOK_BIT_MASK:
        if local.dblbuffon:
            raise NotImplementedError("dwt_isr: double-buffered RX is not ported")

        cia_err = 0
        cb.rx_flags = 0

        # CIA error: clear it too, so we don't go on to the RX-error callback.
        if status & R.SYS_STATUS_CIAERR_BIT_MASK:
            cb.rx_flags |= R.DWT_CB_DATA_RX_FLAG_CER
            cia_err = R.SYS_STATUS_CIAERR_BIT_MASK
        elif status & R.SYS_STATUS_CIADONE_BIT_MASK:
            cb.rx_flags |= R.DWT_CB_DATA_RX_FLAG_CIA

        if status & R.SYS_STATUS_CPERR_BIT_MASK:
            cb.rx_flags |= R.DWT_CB_DATA_RX_FLAG_CPER
            cia_err |= R.SYS_STATUS_CPERR_BIT_MASK

        if (status & R.SYS_STATUS_RXFR_BIT_MASK) and nodata_sts:
            # No-data STS mode reports RXFR, not RXFCG.
            cb.rx_flags |= R.DWT_CB_DATA_RX_FLAG_ND
            cb.datalength = 0
            cia_err |= R.SYS_STATUS_RXFCE_BIT_MASK
        elif status & R.SYS_STATUS_RXFCG_BIT_MASK:
            finfo16 = dwt_read16bitoffsetreg(R.RX_FINFO_ID, 0)

            # Standard frames up to 127 bytes, extended frames up to 1023.
            if local.longFrames == 0:
                cb.datalength = finfo16 & R.RX_FINFO_STD_RXFLEN_MASK
            else:
                cb.datalength = finfo16 & R.RX_FINFO_RXFLEN_BIT_MASK

            if finfo16 & R.RX_FINFO_RNG_BIT_MASK:
                cb.rx_flags |= R.DWT_CB_DATA_RX_FLAG_RNG

        # Clear all status bits relating to good reception.
        dwt_write32bitreg(R.SYS_STATUS_ID, cia_err | R.SYS_STATUS_ALL_RX_GOOD)

        if local.cbRxOk is not None:
            local.cbRxOk(cb)

    # RX errors (RXFCE|RXPHE|RXFSL|ARFE|RXSTO|RXOVRR). Cleared before the
    # callback so the callback may re-enable the receiver.
    if fstat & R.FINT_STAT_RXERR_BIT_MASK:
        dwt_write32bitoffsetreg(R.SYS_STATUS_ID, 0, R.SYS_STATUS_ALL_RX_ERR)
        if local.cbRxErr is not None:
            local.cbRxErr(cb)

    # RX timeouts (RXFTO and RXPTO). Cleared before the callback, as above.
    if fstat & R.FINT_STAT_RXTO_BIT_MASK:
        dwt_write8bitoffsetreg(R.SYS_STATUS_ID, 2, (R.SYS_STATUS_ALL_RX_TO >> 16) & 0xFF)
        if local.cbRxTo is not None:
            local.cbRxTo(cb)


# ===========================================================================
# Diagnostics, accumulator and event counters (used by ex_02c)
# Ported from deca_device.c: dwt_configciadiag, dwt_readdiagnostics (the
# normal, non-double-buffer branch), dwt_readaccdata, dwt_configeventcounters,
# dwt_readeventcounters.
# ===========================================================================
def dwt_configciadiag(enable_mask: int):
    """
    Choose how much the CIA logs.  DW_CIA_DIAG_LOG_ALL (0x1) logs everything;
    0 logs a reduced set.  MAX/MID/MIN (0x8/0x4/0x2) apply to double-buffer mode.
    """
    if enable_mask & R.DW_CIA_DIAG_LOG_ALL:
        dwt_and8bitoffsetreg(R.CIA_CONF_ID, 2, (~R.CIA_DIAGNOSTIC_OFF) & 0xFF)
    else:
        dwt_or8bitoffsetreg(R.CIA_CONF_ID, 2, R.CIA_DIAGNOSTIC_OFF)

    dwt_write8bitoffsetreg(R.RDB_DIAG_MODE_ID, 0, enable_mask >> 1)

    local.cia_diagnostic = enable_mask


def _le(buf, off, n):
    """Little-endian unsigned integer of n bytes from buf[off:]."""
    return int.from_bytes(buf[off:off + n], "little")


def dwt_readdiagnostics(diag=None) -> DwtRxDiag:
    """
    Read the RX signal-quality diagnostics. Fills and returns `diag` (a new
    DwtRxDiag if none is given). Double-buffer mode is not ported.
    """
    if diag is None:
        diag = DwtRxDiag()
    if local.dblbuffon != R.DBL_BUFF_OFF:
        raise NotImplementedError("dwt_readdiagnostics: double-buffer mode not ported")

    ip = R.IP_TOA_LO_ID             # all offsets below are relative to this
    log_all = local.cia_diagnostic & R.DW_CIA_DIAG_LOG_ALL

    if log_all:
        # 0xC0000 space (108 bytes) then 0xD0000 space (108 bytes)
        temp = dwt_readfromdevice(R.IP_TOA_LO_ID, 0, 108) + dwt_readfromdevice(R.STS_DIAG_4_ID, 0, 108)
    else:
        temp = dwt_readfromdevice(R.IP_TOA_LO_ID, 0, 40)

    diag.ipatovRxTime = bytes(temp[0:5])
    diag.stsRxTime = bytes(temp[R.STS_TOA_LO_ID - ip:R.STS_TOA_LO_ID - ip + 5])
    diag.sts2RxTime = bytes(temp[R.STS1_TOA_LO_ID - ip:R.STS1_TOA_LO_ID - ip + 5])
    # tdoa is 6 bytes (the C loop copies 5 and then [5] separately)
    diag.tdoa = bytes(temp[R.CIA_TDOA_0_ID - ip:R.CIA_TDOA_0_ID - ip + 6])

    o = R.IP_TOA_HI_ID - ip
    diag.ipatovRxStatus = temp[o + R.CIA_I_STAT_OFFSET]
    diag.ipatovPOA = _le(temp, o + 1, 2)

    # (sic) upstream computes the STS statuses as (hi byte | lo byte) >> 7,
    # and for STS1 reads the low byte from the STS (not STS1) register.
    o = R.STS_TOA_HI_ID - ip
    diag.stsRxStatus = (temp[o + R.CIA_C_STAT_OFFSET + 1] | temp[o + R.CIA_C_STAT_OFFSET]) >> 7
    diag.stsPOA = _le(temp, o + 1, 2)

    o1 = R.STS1_TOA_HI_ID - ip
    diag.sts2RxStatus = (temp[o1 + R.CIA_C_STAT_OFFSET + 1] | temp[o + R.CIA_C_STAT_OFFSET]) >> 7
    diag.sts2POA = _le(temp, o1 + 1, 2)

    pdoa = _le(temp, R.CIA_TDOA_1_PDOA_ID - ip + 2, 2) & 0x3FFF
    if pdoa & 0x2000:
        pdoa |= 0xC000              # sign-extend as upstream does...
    # ...upstream stores into an int16_t, so 0xC000.. becomes negative
    diag.pdoa = pdoa - 0x10000 if pdoa & 0x8000 else pdoa

    diag.xtalOffset = _le(temp, R.CIA_DIAG_0_ID - ip, 2) & 0x1FFF
    diag.ciaDiag1 = _le(temp, R.CIA_DIAG_1_ID - ip, 4) & 0x1FFFFFFF

    if not log_all:
        return diag                 # minimal diagnostics only

    # --- Ipatov ---
    diag.ipatovPeak = _le(temp, R.IP_DIAG_0_ID - ip, 4) & 0x7FFFFFFF
    diag.ipatovPower = _le(temp, R.IP_DIAG_1_ID - ip, 4) & 0x1FFFF
    diag.ipatovF1 = _le(temp, R.IP_DIAG_2_ID - ip, 4) & 0x3FFFFF
    diag.ipatovF2 = _le(temp, R.IP_DIAG_3_ID - ip, 4) & 0x3FFFFF
    diag.ipatovF3 = _le(temp, R.IP_DIAG_4_ID - ip, 4) & 0x3FFFFF
    diag.ipatovFpIndex = _le(temp, R.IP_DIAG_8_ID - ip, 2)
    diag.ipatovAccumCount = _le(temp, R.IP_DIAG_12_ID - ip, 2) & 0xFFF

    # --- STS 1 ---
    diag.stsPeak = _le(temp, R.STS_DIAG_0_ID - ip, 4) & 0x3FFFFFFF
    diag.stsPower = _le(temp, R.STS_DIAG_1_ID - ip, 2)
    diag.stsF1 = _le(temp, R.STS_DIAG_2_ID - ip, 4) & 0x3FFFFF
    diag.stsF2 = _le(temp, R.STS_DIAG_3_ID - ip, 4) & 0x3FFFFF

    # the 0xD0000 block starts 0x6C bytes into temp (0xC0000 space is 0x6C long)
    d = 0x6C
    diag.stsF3 = _le(temp, R.STS_DIAG_4_ID - R.STS_DIAG_4_ID + d, 4) & 0x3FFFFF
    diag.stsFpIndex = _le(temp, R.STS_DIAG_8_ID - R.STS_DIAG_4_ID + d, 2) & 0x7FFF
    diag.stsAccumCount = _le(temp, R.STS_DIAG_12_ID - R.STS_DIAG_4_ID + d, 2) & 0xFFF

    # --- STS 2 ---
    diag.sts2Peak = _le(temp, R.STS1_DIAG_0_ID - R.STS_DIAG_4_ID + d, 4) & 0x3FFFFFFF
    diag.sts2Power = _le(temp, R.STS1_DIAG_1_ID - R.STS_DIAG_4_ID + d, 2)
    diag.sts2F1 = _le(temp, R.STS1_DIAG_2_ID - R.STS_DIAG_4_ID + d, 4) & 0x3FFFFF
    diag.sts2F2 = _le(temp, R.STS1_DIAG_3_ID - R.STS_DIAG_4_ID + d, 4) & 0x3FFFFF
    diag.sts2F3 = _le(temp, R.STS1_DIAG_4_ID - R.STS_DIAG_4_ID + d, 4) & 0x3FFFFF
    diag.sts2FpIndex = _le(temp, R.STS1_DIAG_8_ID - R.STS_DIAG_4_ID + d, 2) & 0x7FFF
    diag.sts2AccumCount = _le(temp, R.STS1_DIAG_12_ID - R.STS_DIAG_4_ID + d, 2) & 0xFFF
    return diag


def dwt_readaccdata(length: int, accOffset: int) -> bytes:
    """
    Read `length` bytes of the CIR accumulator starting at complex-sample index
    `accOffset`. The FIRST byte returned is a dummy and must be discarded by
    the caller (as upstream documents). Returns the raw bytes.
    """
    # Force on the ACC clocks if we are sequenced
    dwt_or16bitoffsetreg(R.CLK_CTRL_ID, 0x0, R.CLK_CTRL_ACC_MCLK_EN_BIT_MASK | R.CLK_CTRL_ACC_CLK_EN_BIT_MASK)

    try:
        if (accOffset + length) > R.ACC_BUFFER_MAX_LEN:
            raise ValueError("dwt_readaccdata: range exceeds accumulator")      # upstream: assert(0)
        if accOffset <= R.REG_DIRECT_OFFSET_MAX_LEN:
            data = dwt_readfromdevice(R.ACC_MEM_ID, accOffset, length)
        else:
            # Program the indirect offset registers A for the specified offset to ACC
            dwt_write32bitreg(R.INDIRECT_ADDR_A_ID, (R.ACC_MEM_ID >> 16))
            dwt_write32bitreg(R.ADDR_OFFSET_A_ID, accOffset)
            data = dwt_readfromdevice(R.INDIRECT_POINTER_A_ID, 0, length)
    finally:
        # Revert clocks back
        dwt_and16bitoffsetreg(R.CLK_CTRL_ID, 0x0,
                              (~(R.CLK_CTRL_ACC_MCLK_EN_BIT_MASK | R.CLK_CTRL_ACC_CLK_EN_BIT_MASK)) & 0xFFFF)
    return data


def dwt_configeventcounters(enable: int):
    """Clear (and, if `enable`, start) the event counters."""
    # Need to clear and disable, can't just clear
    dwt_write8bitoffsetreg(R.EVC_CTRL_ID, 0x0, R.EVC_CTRL_EVC_CLR_BIT_MASK)
    if enable:
        dwt_write8bitoffsetreg(R.EVC_CTRL_ID, 0x0, R.EVC_CTRL_EVC_EN_BIT_MASK)


def dwt_readeventcounters(counters=None) -> DwtDeviceEntCnts:
    """Read the event counters into `counters` (new object if None) and return it."""
    if counters is None:
        counters = DwtDeviceEntCnts()

    t = dwt_read32bitoffsetreg(R.EVC_COUNT0_ID, 0)      # sync loss (27-16), PHE (11-0)
    counters.PHE = t & 0xFFF
    counters.RSL = (t >> 16) & 0xFFF

    t = dwt_read32bitoffsetreg(R.EVC_COUNT1_ID, 0)      # CRC bad (27-16), CRC good (11-0)
    counters.CRCG = t & 0xFFF
    counters.CRCB = (t >> 16) & 0xFFF

    t = dwt_read32bitoffsetreg(R.EVC_COUNT2_ID, 0)      # overruns (23-16), address errors (7-0)
    counters.ARFE = t & 0xFF
    counters.OVER = (t >> 16) & 0xFF

    t = dwt_read32bitoffsetreg(R.EVC_COUNT3_ID, 0)      # PTO (27-16), SFDTO (11-0)
    counters.PTO = (t >> 16) & 0xFFF
    counters.SFDTO = t & 0xFFF

    t = dwt_read32bitoffsetreg(R.EVC_COUNT4_ID, 0)      # TXFRAME (27-16), RX TO (7-0)
    counters.TXF = (t >> 16) & 0xFFF
    counters.RTO = t & 0xFF

    t = dwt_read32bitoffsetreg(R.EVC_COUNT5_ID, 0)      # half period warning (7-0), SPI CRC err (23-16)
    counters.HPW = t & 0xFF
    counters.CRCE = (t >> 16) & 0xFF

    t = dwt_read32bitoffsetreg(R.EVC_COUNT6_ID, 0)      # preamble rejections (11-0)
    counters.PREJ = t & 0xFFF
    return counters


# ===========================================================================
# SNIFF mode and LNA/PA GPIO (used by ex_02d)
# Ported from deca_device.c: dwt_setlnapamode, dwt_setsniffmode.
# ===========================================================================
def dwt_setlnapamode(lna_pa: int):
    """
    Route the DW3000 GPIOs to external LNA/PA control (also handy to watch TX/RX
    activity on a scope). bit0 = LNA (GPIO6 EXTRX), bit1 = PA (GPIO4 EXTDA +
    GPIO5 EXTTX), bit2 = TXE/RXE (GPIO0/GPIO1). 0 disables them all.
    """
    gpio_mode = dwt_read32bitreg(R.GPIO_MODE_ID)
    # clear GPIO 0, 1, 4, 5, 6 configuration
    gpio_mode &= ~(R.GPIO_MODE_MSGP0_MODE_BIT_MASK | R.GPIO_MODE_MSGP1_MODE_BIT_MASK
                   | R.GPIO_MODE_MSGP4_MODE_BIT_MASK | R.GPIO_MODE_MSGP5_MODE_BIT_MASK
                   | R.GPIO_MODE_MSGP6_MODE_BIT_MASK)
    gpio_mode &= 0xFFFFFFFF
    if lna_pa & R.DWT_LNA_ENABLE:
        gpio_mode |= R.GPIO_PIN6_EXTRX
    if lna_pa & R.DWT_PA_ENABLE:
        gpio_mode |= (R.GPIO_PIN4_EXTDA | R.GPIO_PIN5_EXTTX)
    if lna_pa & R.DWT_TXRX_EN:
        gpio_mode |= (R.GPIO_PIN0_EXTTXE | R.GPIO_PIN1_EXTRXE)

    dwt_write32bitreg(R.GPIO_MODE_ID, gpio_mode)


def dwt_setsniffmode(enable: int, timeOn: int, timeOff: int):
    """
    Enable/disable SNIFF mode (receiver duty-cycled on/off).
    timeOn: ON phase in PAC sizes (+1 PAC added by the IC), 1..15.
    timeOff: OFF phase in units of 128/125 us (~1 us), max 255.
    """
    if enable:
        sniff_reg = (((timeOff & 0xFF) << 8) | (timeOn & 0xFF)) & \
            (R.RX_SNIFF_SNIFF_OFF_BIT_MASK | R.RX_SNIFF_SNIFF_ON_BIT_MASK)
        dwt_write16bitoffsetreg(R.RX_SNIFF_ID, 0, sniff_reg)
    else:
        dwt_write16bitoffsetreg(R.RX_SNIFF_ID, 0, 0x0000)


# ===========================================================================
# Crystal trim and clock offset (used by ex_02f)
# Ported from deca_device.c: dwt_setxtaltrim, dwt_getxtaltrim, dwt_readclockoffset.
# ===========================================================================
def dwt_setxtaltrim(value: int):
    """Adjust the crystal frequency. `value` is 0x00..0x3F (64 steps, ~1.65 ppm/step)."""
    reg_val = (value & R.XTAL_TRIM_BIT_MASK) >> R.XTAL_XTAL_TRIM_BIT_OFFSET
    local.init_xtrim = reg_val
    dwt_write8bitoffsetreg(R.XTAL_ID, 0, reg_val)


def dwt_getxtaltrim() -> int:
    """Current XTAL trim: the OTP/default value after dwt_initialise, or the last dwt_setxtaltrim."""
    return local.init_xtrim


def dwt_readclockoffset() -> int:
    """
    Read the crystal offset of the remote device relative to this one, as a signed
    value; multiply by CLOCK_OFFSET_PPM_TO_RATIO (1/2^26) for the ratio, or by
    CLOCK_OFFSET_PPM_TO_RATIO * 1e6 for ppm. Positive means the local RX clock is
    running faster than the remote TX clock. Call before re-enabling the receiver.
    (Double-buffer mode is not ported.)
    """
    if local.dblbuffon != R.DBL_BUFF_OFF:
        raise NotImplementedError("dwt_readclockoffset: double-buffer mode not ported")
    regval = dwt_read16bitoffsetreg(R.CIA_DIAG_0_ID, 0) & R.CIA_DIAG_0_COE_PPM_BIT_MASK
    if regval & R.B11_SIGN_EXTEND_TEST:
        regval |= R.B11_SIGN_EXTEND_MASK        # sign extend bit #12 to the whole short
    return regval - 0x10000 if regval & 0x8000 else regval   # as int16_t


# ===========================================================================
# Timestamps, antenna delays and delayed TX/RX (used by the ranging examples)
# Ported from deca_device.c. Timestamps are 40-bit device times (~15.65 ps per
# unit); the byte-array readers return the 5 raw bytes, little-endian.
# ===========================================================================
def dwt_setrxantennadelay(rxDelay: int):
    """RX antenna delay, used for automatic RX timestamp adjustment."""
    dwt_write16bitoffsetreg(R.CIA_CONF_ID, 0, rxDelay)


def dwt_settxantennadelay(txDelay: int):
    """TX antenna delay, used for automatic TX timestamp adjustment."""
    dwt_write16bitoffsetreg(R.TX_ANTD_ID, 0, txDelay)


def dwt_readtxtimestamp() -> bytes:
    """The 5-byte (40-bit) TX timestamp, adjusted with the TX antenna delay."""
    return dwt_readfromdevice(R.TX_TIME_LO_ID, 0, R.TX_TIME_TX_STAMP_LEN)


def dwt_readtxtimestamplo32() -> int:
    """Low 32 bits of the TX timestamp."""
    return dwt_read32bitreg(R.TX_TIME_LO_ID)


def dwt_readrxtimestamp() -> bytes:
    """The 5-byte (40-bit) RX timestamp, adjusted with the RX antenna delay.
    (Double-buffer mode is not ported.)"""
    if local.dblbuffon != R.DBL_BUFF_OFF:
        raise NotImplementedError("dwt_readrxtimestamp: double-buffer mode not ported")
    return dwt_readfromdevice(R.RX_TIME_0_ID, 0, R.RX_TIME_RX_STAMP_LEN)


def dwt_readrxtimestamplo32() -> int:
    """Low 32 bits of the RX timestamp."""
    return dwt_read32bitreg(R.RX_TIME_0_ID)


def dwt_setdelayedtrxtime(starttime: int):
    """
    Time for a delayed TX/RX: the high 32 bits of the 40-bit device time
    (bit 0 is ignored by the chip, so the resolution is ~8 ns).
    """
    dwt_write32bitoffsetreg(R.DX_TIME_ID, 0, starttime)
