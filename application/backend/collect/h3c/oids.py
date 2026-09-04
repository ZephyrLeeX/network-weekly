"""OID registry for H3C Comware collection (S10500X / S12500).

Sources, in authority order (AGENTS.md):
1. SYSTEM_SPEC.md — which data the collectors must provide.
2. H3C official MIB Companion / the standard MIB modules H3C Comware
   implements: IF-MIB and ifXTable (RFC 2863), EtherLike-MIB (RFC 3635),
   IEEE8023-LAG-MIB (IEEE 802.1AX), SNMPv2-MIB.
3. Real-device captures (W01-T007) — H3C enterprise OIDs below are pending
   confirmation against the real S10500X/S12500.

When real device behavior differs, adjust ONLY these constants (minimal
compatibility rule in AGENTS.md) — never the parsing logic.
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
# ifHighSpeed (RFC 2863): interface speed in Mbps. Authoritative for links
# faster than 4.29 Gb/s, where ifSpeed (32-bit) saturates at 4294967295.
IF_HIGH_SPEED = f"{IF_X_TABLE}.15"
IF_ALIAS = f"{IF_X_TABLE}.18"

# ifAdminStatus / ifOperStatus enumerations (RFC 2863, implemented as
# documented in the H3C IF-MIB MIB Companion). The two columns have
# different vocabularies and are deliberately separate maps. (The *_STATUS
# OID constants above are the column OIDs; these are their value maps.)
IF_ADMIN_STATUS_MAP = {1: "up", 2: "down", 3: "testing"}
IF_OPER_STATUS_MAP = {
    1: "up",
    2: "down",
    3: "testing",
    4: "unknown",
    5: "dormant",
    6: "notpresent",
    7: "lowerlayerdown",
}

# --- EtherLike-MIB (RFC 3635), implemented by H3C Comware --------------------
# dot3StatsTable = 1.3.6.1.2.1.10.7.2, dot3StatsEntry = ...2.1;
# dot3StatsFCSErrors = dot3StatsEntry 3 (Counter32).
# dot3HCStatsTable = 1.3.6.1.2.1.10.7.11, dot3HCStatsEntry = ...11.1;
# dot3HCStatsFCSErrors = dot3HCStatsEntry 2 (Counter64) — preferred where
# provided (required by RFC 3635 on 10 Gb/s+ interfaces, i.e. all S10500X /
# S12500 Ethernet ports); the 32-bit counter is the fallback.
# Both are indexed by ifIndex. CRC == FCS errors on Ethernet.
DOT3_STATS_FCS_ERRORS = "1.3.6.1.2.1.10.7.2.1.3"
DOT3_HC_STATS_FCS_ERRORS = "1.3.6.1.2.1.10.7.11.1.2"

# --- H3C hh3c-entity-ext (enterprise 25506) ----------------------------------
# Per-chassis CPU / memory usage table. Column OIDs pending real-device
# verification; the table index is the entity's physical index.
HH3C_ENTITY_EXT_CPU_USAGE = "1.3.6.1.4.1.25506.2.6.1.1.1.1.6"
HH3C_ENTITY_EXT_MEM_USAGE = "1.3.6.1.4.1.25506.2.6.1.1.1.1.8"

# --- IEEE8023-LAG-MIB (dot3Agg), IEEE 802.1AX --------------------------------
# dot3AggPortTable = 1.2.840.10006.300.43.1.2.1, dot3AggPortEntry = ...2.1.
# Column 12 dot3adAggPortSelectedAggID: the aggregation the LACP selection
# logic selected the port into (0 = not selected).
# Column 13 dot3adAggPortAttachedAggID: the aggregation the port is actually
# attached to / bundling into (0 = not attached).
# On Comware the aggregation id is the aggregation interface's ifIndex
# (pending real-device confirmation in W01-T007).
DOT3_AGG_PORT_SELECTED_AGG_ID = "1.2.840.10006.300.43.1.2.1.1.12"
DOT3_AGG_PORT_ATTACHED_AGG_ID = "1.2.840.10006.300.43.1.2.1.1.13"


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
