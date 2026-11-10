"""Authenticated encryption for provider archives kept in a public repository.

Version 1: magic bytes, a random 16-byte scrypt salt, then a Fernet token.
The API key is supplied at runtime and is never included in the archive.
"""
from __future__ import annotations

import base64
import hashlib
import os

MAGIC = b"FAD1\n"
SALT_SIZE = 16


def _cipher(secret: str, salt: bytes):
    from cryptography.fernet import Fernet

    if not secret:
        raise ValueError("An archive encryption key is required")
    key = hashlib.scrypt(secret.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt(plaintext: str, secret: str) -> bytes:
    salt = os.urandom(SALT_SIZE)
    return MAGIC + salt + _cipher(secret, salt).encrypt(plaintext.encode("utf-8"))


def decrypt(content: bytes, secret: str) -> str:
    from cryptography.fernet import InvalidToken

    if not content.startswith(MAGIC) or len(content) <= len(MAGIC) + SALT_SIZE:
        raise ValueError("Invalid encrypted archive format")
    salt = content[len(MAGIC):len(MAGIC) + SALT_SIZE]
    try:
        return _cipher(secret, salt).decrypt(content[len(MAGIC) + SALT_SIZE:]).decode("utf-8")
    except (InvalidToken, UnicodeDecodeError):
        raise ValueError("Cannot decrypt archive: wrong key or damaged file; preserve the original API key") from None
