"""Create a private environment file containing a new archive key, without printing it."""
from __future__ import annotations

import argparse
import base64
import os
import secrets
from pathlib import Path


def create(path: Path):
    key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")
    # Exclusive creation prevents accidental key rotation or loss of existing settings.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write("DATA_ENCRYPTION_KEY=" + key + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / ".env")
    args = parser.parse_args()
    try:
        create(args.output)
    except FileExistsError:
        parser.error("Output already exists; it was not overwritten")
    print(f"Created private key file: {args.output}. Back it up securely; do not commit it.")
