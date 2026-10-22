"""
decadriver/deca_types.py

Simple attribute-holder equivalents of the C structs from
decadriver/deca_device_api.h that examples fill in and pass to
dwt_configure() / dwt_configuretxrf(). Field names/order match the
upstream structs exactly.
"""


class DwtConfig:
    """Equivalent of dwt_config_t."""

    def __init__(self, chan, txPreambLength, rxPAC, txCode, rxCode, sfdType,
                 dataRate, phrMode, phrRate, sfdTO, stsMode, stsLength, pdoaMode):
        self.chan = chan
        self.txPreambLength = txPreambLength
        self.rxPAC = rxPAC
        self.txCode = txCode
        self.rxCode = rxCode
        self.sfdType = sfdType
        self.dataRate = dataRate
        self.phrMode = phrMode
        self.phrRate = phrRate
        self.sfdTO = sfdTO
        self.stsMode = stsMode
        self.stsLength = stsLength
        self.pdoaMode = pdoaMode


class DwtTxConfig:
    """Equivalent of dwt_txconfig_t."""

    def __init__(self, PGdly, power, PGcount):
        self.PGdly = PGdly
        self.power = power
        self.PGcount = PGcount


class DwtCbData:
    """
    Equivalent of dwt_cb_data_t: what dwt_isr() hands to each callback.
    A single instance is reused for every event (as upstream does), so the
    interrupt path allocates nothing.
    """

    def __init__(self):
        self.status = 0      # SYS_STATUS (low 32 bits) as the ISR was entered
        self.status_hi = 0   # SYS_STATUS_HI, only valid for panic/SPI-error events
        self.datalength = 0  # received frame length (RX good events)
        self.rx_flags = 0    # DWT_CB_DATA_RX_FLAG_* bits (RX good events)


class DwtRxDiag:
    """Equivalent of dwt_rxdiag_t. Byte-array fields are bytes/bytearray."""

    def __init__(self):
        self.ipatovRxTime = bytes(5)
        self.ipatovRxStatus = 0
        self.ipatovPOA = 0
        self.stsRxTime = bytes(5)
        self.stsRxStatus = 0
        self.stsPOA = 0
        self.sts2RxTime = bytes(5)
        self.sts2RxStatus = 0
        self.sts2POA = 0
        self.tdoa = bytes(6)
        self.pdoa = 0
        self.xtalOffset = 0
        self.ciaDiag1 = 0
        self.ipatovPeak = 0
        self.ipatovPower = 0
        self.ipatovF1 = 0
        self.ipatovF2 = 0
        self.ipatovF3 = 0
        self.ipatovFpIndex = 0
        self.ipatovAccumCount = 0
        self.stsPeak = 0
        self.stsPower = 0
        self.stsF1 = 0
        self.stsF2 = 0
        self.stsF3 = 0
        self.stsFpIndex = 0
        self.stsAccumCount = 0
        self.sts2Peak = 0
        self.sts2Power = 0
        self.sts2F1 = 0
        self.sts2F2 = 0
        self.sts2F3 = 0
        self.sts2FpIndex = 0
        self.sts2AccumCount = 0


class DwtDeviceEntCnts:
    """Equivalent of dwt_deviceentcnts_t (event counters)."""

    def __init__(self):
        self.PHE = 0     # received header errors (12 bit)
        self.RSL = 0     # frame sync losses (12 bit)
        self.CRCG = 0    # good CRC frames (12 bit)
        self.CRCB = 0    # bad CRC frames (12 bit)
        self.ARFE = 0    # address filter errors (8 bit)
        self.OVER = 0    # RX buffer overruns (8 bit)
        self.SFDTO = 0   # SFD timeouts (12 bit)
        self.PTO = 0     # preamble timeouts (12 bit)
        self.RTO = 0     # RX frame wait timeouts (8 bit)
        self.TXF = 0     # transmitted frames (12 bit)
        self.HPW = 0     # half period warnings (8 bit)
        self.CRCE = 0    # SPI CRC errors (8 bit)
        self.PREJ = 0    # preamble rejections (12 bit)
