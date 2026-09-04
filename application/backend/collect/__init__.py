"""Collection subpackage (Wave 1).

Transport/adapter boundary (SYSTEM_SPEC.md §7.4): everything here only
collects, parses and normalizes device data into stable DTOs. Business
statistics, report decisions and configuration changes live elsewhere.
"""

from backend.collect.dto import (
    AggregationMapping,
    DeviceIdentity,
    EntityLoadSample,
    InterfaceSample,
    normalize_interface_name,
)

__all__ = [
    "AggregationMapping",
    "DeviceIdentity",
    "EntityLoadSample",
    "InterfaceSample",
    "normalize_interface_name",
]
