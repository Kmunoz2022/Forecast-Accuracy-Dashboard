"""Authenticated archives: FAD2 uses a dedicated random key; FAD1 is read-only."""
from __future__ import annotations

import base64
import hashlib
import os

MAGIC = b"FAD1\n"
MAGIC_V2 = b"FAD2\n"
SALT_SIZE = 16


def _cipher(secret: str, salt: bytes):
    from cryptography.fernet import Fernet

    if not secret:
        raise ValueError("An archive encryption key is required")
    key = hashlib.scrypt(secret.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return Fernet(base64.urlsafe_b64encode(key))


def validate_key(secret: str) -> str:
    try:
        decoded = base64.b64decode(secret.encode("ascii"), altchars=b"-_", validate=True)
        if len(decoded) != 32 or base64.urlsafe_b64encode(decoded).decode() != secret:
            raise ValueError()
    except (ValueError, UnicodeError, AttributeError):
        raise ValueError("DATA_ENCRYPTION_KEY must be a URL-safe base64 encoding of 32 random bytes") from None
    return secret


def key_id(secret: str) -> str:
    return hashlib.sha256(base64.urlsafe_b64decode(validate_key(secret))).hexdigest()[:16]


def configured_key() -> str:
    value = os.getenv("DATA_ENCRYPTION_KEY")
    if not value:
        raise ValueError("Set DATA_ENCRYPTION_KEY before collecting or migrating protected forecasts")
    return validate_key(value)


def encrypt(plaintext: str, secret: str) -> bytes:
    from cryptography.fernet import Fernet

    # Fernet supplies a fresh IV and authenticates every ciphertext. The identifier
    # distinguishes archive keys without storing an API credential or the key itself.
    return MAGIC_V2 + key_id(secret).encode("ascii") + b"\n" + Fernet(secret.encode()).encrypt(plaintext.encode("utf-8"))


def decrypt(content: bytes, secret: str) -> str:
    from cryptography.fernet import Fernet, InvalidToken

    try:
        if content.startswith(MAGIC_V2):
            identifier, token = content[len(MAGIC_V2):].split(b"\n", 1)
            if identifier.decode("ascii") != key_id(secret):
                raise ValueError()
            return Fernet(secret.encode()).decrypt(token).decode("utf-8")
        if content.startswith(MAGIC) and len(content) > len(MAGIC) + SALT_SIZE:
            salt = content[len(MAGIC):len(MAGIC) + SALT_SIZE]
            return _cipher(secret, salt).decrypt(content[len(MAGIC) + SALT_SIZE:]).decode("utf-8")
        raise ValueError()
    except (InvalidToken, ValueError, UnicodeError):
        raise ValueError("Cannot decrypt archive: wrong key or damaged file; preserve the original encryption key") from None
