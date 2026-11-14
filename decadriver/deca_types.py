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


class DwtAesKey:
    """Equivalent of dwt_aes_key_t: 128/192/256-bit key as eight 32-bit words."""

    def __init__(self, key0=0, key1=0, key2=0, key3=0, key4=0, key5=0, key6=0, key7=0):
        self.key0 = key0
        self.key1 = key1
        self.key2 = key2
        self.key3 = key3
        self.key4 = key4
        self.key5 = key5
        self.key6 = key6
        self.key7 = key7


class DwtAesConfig:
    """Equivalent of dwt_aes_config_t."""

    def __init__(self, aes_key_otp_type, aes_core_type, mic, key_src, key_load,
                 key_addr, key_size, mode):
        self.aes_key_otp_type = aes_key_otp_type   # key from RAM or OTP
        self.aes_core_type = aes_core_type         # GCM or CCM*
        self.mic = mic                             # message integrity code size (MIC_x enum)
        self.key_src = key_src                     # key in registers (128 bit) or RAM/OTP
        self.key_load = key_load                   # load key from RAM or use the registers
        self.key_addr = key_addr                   # address offset of the key when in RAM
        self.key_size = key_size                   # AES_KEY_128/192/256bit
        self.mode = mode                           # encrypt / decrypt


class DwtAesJob:
    """
    Equivalent of dwt_aes_job_t. Where the C struct holds pointers to the
    header/payload buffers, this holds bytes/bytearray objects (or None):
    on decryption dwt_do_aes() copies the plain header/payload INTO them, so
    pass bytearrays of the right size there.
    """

    def __init__(self, nonce=None, header=None, payload=None, header_len=0, payload_len=0,
                 src_port=0, dst_port=0, mode=0, mic_size=0):
        self.nonce = nonce            # 12 bytes
        self.header = header          # not encrypted/decrypted
        self.payload = payload        # encrypted/decrypted
        self.header_len = header_len
        self.payload_len = payload_len
        self.src_port = src_port
        self.dst_port = dst_port
        self.mode = mode
        self.mic_size = mic_size      # tag size in bytes
