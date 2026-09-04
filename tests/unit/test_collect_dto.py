"""Unit tests for the normalized collection DTOs."""

import pytest

from backend.collect.dto import normalize_interface_name


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Ten-GigabitEthernet1/0/1", "ten-gigabitethernet1/0/1"),
        ("  Bridge-Aggregation1  ", "bridge-aggregation1"),
        ("Ten  GigabitEthernet1/0/1", "ten gigabitethernet1/0/1"),
        ("VLAN-INTERFACE100", "vlan-interface100"),
    ],
)
def test_normalize_interface_name(raw: str, expected: str) -> None:
    assert normalize_interface_name(raw) == expected


def test_normalize_is_idempotent_and_case_insensitive() -> None:
    once = normalize_interface_name("Ten-GigabitEthernet1/0/1")
    assert normalize_interface_name(once) == once
    assert normalize_interface_name("TEN-GIGABITETHERNET1/0/1") == once
