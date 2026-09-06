"""Salted scrypt password hashing (W04-T001, SYSTEM_SPEC.md §21).

The administrator password is never stored or persisted as plaintext: it is
hashed with scrypt (memory-hard KDF from the Python standard library) under
a per-password random salt. The stored string is self-describing —

    scrypt$<n>$<r>$<p>$<salt hex>$<digest hex>

— so verification reads its parameters from the stored hash and future
parameter changes do not break existing accounts. Comparison uses
`hmac.compare_digest` (constant time).

Nothing in this module ever writes a log line; the plaintext exists only as
the function arguments and is gone when they go out of scope.
"""

import hashlib
import hmac
import secrets

# scrypt parameters. n=2**14, r=8, p=1 costs ~16 MiB per hash — slow enough
# to hurt offline guessing, fast enough for one administrator logging in.
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 64
# hashlib.scrypt raises when n*r*2 exceeds maxmem; the default (~32 MiB) is
# close to our 16 MiB cost, so give it explicit headroom.
_SCRYPT_MAXMEM = 64 * 1024 * 1024

_SALT_BYTES = 16
_ALGORITHM = "scrypt"
_HASH_FIELD_COUNT = 6


def hash_password(password: str) -> str:
    """Hash `password` under a fresh random salt (§21 salted scrypt)."""

    if not password:
        raise ValueError("password must not be empty")
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
        maxmem=_SCRYPT_MAXMEM,
    )
    return (
        f"{_ALGORITHM}${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}"
        f"${salt.hex()}${digest.hex()}"
    )


def verify_password(password: str, stored: str) -> bool:
    """Verify `password` against a stored scrypt hash; never raise on input.

    A malformed stored hash verifies to False (the account simply does not
    log in) instead of turning a corrupted row into a 500 page.
    """

    if not password or not stored:
        return False
    fields = stored.split("$")
    if len(fields) != _HASH_FIELD_COUNT or fields[0] != _ALGORITHM:
        return False
    try:
        n, r, p = int(fields[1]), int(fields[2]), int(fields[3])
        salt = bytes.fromhex(fields[4])
        expected = bytes.fromhex(fields[5])
    except (ValueError, TypeError):
        return False
    if n <= 0 or r <= 0 or p <= 0 or not salt or not expected:
        return False
    try:
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            dklen=len(expected),
            maxmem=_SCRYPT_MAXMEM,
        )
    except (ValueError, OverflowError):
        return False
    return hmac.compare_digest(actual, expected)
