"""
config_options.py

Port of the upstream root-level config_options.c: default TX spectrum
(PG delay / TX power / PG count) values per channel, used with
dwt_configuretxrf(). Values copied verbatim from the real source --
these are calibration constants, not logic, so there's nothing to
"port" beyond the numbers themselves.
"""

from decadriver.deca_types import DwtTxConfig

# Channel 5 (ex_01a_simple_tx's config uses chan=5)
txconfig_options = DwtTxConfig(PGdly=0x34, power=0xFDFDFDFD, PGcount=0x0)

# Channel 9
txconfig_options_ch9 = DwtTxConfig(PGdly=0x34, power=0xFEFEFEFE, PGcount=0x0)
