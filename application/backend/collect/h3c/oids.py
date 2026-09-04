"""OID registry for H3C Comware collection (S10500X / S12500).

Standard MIBs (SNMPv2-MIB, IF-MIB) are stable. H3C enterprise OIDs and the
802.3 LAG OIDs below come from the published H3C hh3c-entity-ext.mib and
IEEE8023-LAG-MIB definitions; they are **pending confirmation against the
real S10500X/S12500** in W01-T003/T004 real-device validation. When real
device behavior differs, adjust ONLY these constants (minimal compatibility
rule in AGENTS.md) — never the parsing logic.
"""

# --- SNMPv2-MIB system group -------------------------------------------------
SYS_DESCR = "1.3.6.1.2.1.1.1.0"
SYS_OBJECT_ID = "1.3.6.1.2.1.1.2.0"
SYS_UP_TIME = "1.3.6.1.2.1.1.3.0"
SYS_NAME = "1.3.6.1.2.1.1.5.0"

# --- IF-MIB (RFC 2863 / 2233) ------------------------------------------------
IF_TABLE = "1.3.6.1.2.1.2.2.1"
IF_INDEX = f"{IF_TABLE}.1"
IF_DESCR = f"{IF_TABLE}.2"
IF_TYPE = f"{IF_TABLE}.3"
IF_SPEED = f"{IF_TABLE}.5"
IF_ADMIN_STATUS = f"{IF_TABLE}.7"
IF_OPER_STATUS = f"{IF_TABLE}.8"
IF_IN_OCTETS = f"{IF_TABLE}.10"
IF_IN_DISCARDS = f"{IF_TABLE}.13"
IF_IN_ERRORS = f"{IF_TABLE}.14"
IF_OUT_OCTETS = f"{IF_TABLE}.16"
IF_OUT_DISCARDS = f"{IF_TABLE}.19"
IF_OUT_ERRORS = f"{IF_TABLE}.20"

# ifXTable (RFC 2863), 64-bit counters preferred when present.
IF_X_TABLE = "1.3.6.1.2.1.31.1.1.1"
IF_HC_IN_OCTETS = f"{IF_X_TABLE}.6"
IF_HC_OUT_OCTETS = f"{IF_X_TABLE}.10"
IF_ALIAS = f"{IF_X_TABLE}.18"

# ifOperStatus / ifAdminStatus enumerations (RFC 2863).
IF_STATUS = {1: "up", 2: "down", 3: "testing"}

# --- H3C hh3c-entity-ext (enterprise 25506) ----------------------------------
# Per-chassis CPU / memory usage table. Column OIDs pending real-device
# verification; the table index is the entity's physical index.
HH3C_ENTITY_EXT_CPU_USAGE = "1.3.6.1.4.1.25506.2.6.1.1.1.1.6"
HH3C_ENTITY_EXT_MEM_USAGE = "1.3.6.1.4.1.25506.2.6.1.1.1.1.8"

# --- IEEE8023-LAG-MIB (dot3Agg) ----------------------------------------------
# dot3AggPortSelectedAggID: the aggregation (ifIndex) a port is attached to;
# 0 means the port is not currently selected into any aggregation.
DOT3_AGG_PORT_SELECTED_AGG_ID = "1.2.840.10006.300.43.1.2.1.1.13"


def column_index(oid: str, column: str) -> int | None:
    """Extract the table row index from a column-instance OID.

    `1.3.6.1.2.1.2.2.1.2.10` with column `1.3.6.1.2.1.2.2.1.2` -> `10`.
    Returns None when the OID does not belong to the column.
    """

    prefix = f"{column}."
    if oid == column or not oid.startswith(prefix):
        return None
    suffix = oid[len(prefix) :]
    if not suffix.isdigit():
        return None
    return int(suffix)
