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
