"""Unit tests for salted scrypt password hashing (W04-T001, §21)."""

import pytest

from backend.auth.passwords import hash_password, verify_password

PASSWORD = "correct-horse-battery-staple-密码"


def test_hash_uses_self_describing_scrypt_format() -> None:
    stored = hash_password(PASSWORD)

    fields = stored.split("$")
    assert fields[0] == "scrypt"
    assert int(fields[1]) >= 2**14  # memory-hard cost parameter
    assert int(fields[2]) > 0
    assert int(fields[3]) > 0
    assert len(bytes.fromhex(fields[4])) >= 16  # random salt
    assert len(bytes.fromhex(fields[5])) >= 32  # derived key


def test_same_password_hashes_differ_random_salt() -> None:
    assert hash_password(PASSWORD) != hash_password(PASSWORD)


def test_verify_accepts_correct_and_rejects_wrong_password() -> None:
    stored = hash_password(PASSWORD)

    assert verify_password(PASSWORD, stored) is True
    assert verify_password("wrong-password", stored) is False
    assert verify_password(PASSWORD + " ", stored) is False
    assert verify_password(PASSWORD.upper(), stored) is False


def test_plaintext_never_appears_in_stored_hash() -> None:
    stored = hash_password(PASSWORD)

    assert PASSWORD not in stored
    assert PASSWORD.encode("utf-8").hex() not in stored


def test_unicode_and_long_password_round_trip() -> None:
    stored = hash_password(PASSWORD)

    assert verify_password("密码-P@ssw0rd-🔧", hash_password("密码-P@ssw0rd-🔧")) is True
    assert verify_password(PASSWORD, stored) is True


def test_malformed_stored_hash_verifies_false_not_raise() -> None:
    for broken in ("", "plaintext", "scrypt$", "md5$abc", "scrypt$x$8$1$zz$00"):
        assert verify_password(PASSWORD, broken) is False


def test_verify_rejects_empty_inputs() -> None:
    stored = hash_password(PASSWORD)

    assert verify_password("", stored) is False
    assert verify_password(PASSWORD, "") is False


def test_hash_rejects_empty_password() -> None:
    with pytest.raises(ValueError):
        hash_password("")
