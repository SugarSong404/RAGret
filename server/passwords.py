"""Password hashing (stdlib only; easy to swap for argon2 later)."""
from __future__ import annotations

import hashlib
import hmac
import secrets


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=2**14,
        r=8,
        p=1,
        dklen=32,
    )
    return "scrypt1$" + salt.hex() + "$" + dk.hex()


def verify_password(password: str, stored: str) -> bool:
    if not stored or not stored.startswith("scrypt1$"):
        return False
    parts = stored.split("$")
    if len(parts) != 3:
        return False
    try:
        salt = bytes.fromhex(parts[1])
        want = bytes.fromhex(parts[2])
    except ValueError:
        return False
    dk = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=2**14,
        r=8,
        p=1,
        dklen=len(want),
    )
    return hmac.compare_digest(dk, want)
