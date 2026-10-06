"""Decrypt a Giftly SQL backup locally without importing it into a database."""

from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt


def decrypt_backup(source: Path, target: Path, password: str) -> None:
    """Verify the authenticated backup before publishing plaintext SQL."""
    if source.resolve() == target.resolve() or target.exists():
        raise ValueError("Choose a new output file.")
    partial = target.with_name(target.name + ".partial")
    created = False
    try:
        with source.open("rb") as incoming:
            header = incoming.read(38)
            if len(header) != 38 or header[:10] != b"GIFTLYSQL1":
                raise ValueError("Unsupported backup format.")
            incoming.seek(0, 2)
            remaining = incoming.tell() - 54
            if remaining < 0:
                raise ValueError("Incomplete encrypted backup.")
            incoming.seek(-16, 2)
            tag = incoming.read(16)
            key = Scrypt(salt=header[10:26], length=32, n=32768, r=8, p=1).derive(
                password.encode("utf-8")
            )
            decryptor = Cipher(algorithms.AES(key), modes.GCM(header[26:38], tag)).decryptor()
            decryptor.authenticate_additional_data(header)
            incoming.seek(38)
            with partial.open("xb") as outgoing:
                created = True
                os.chmod(partial, 0o600)
                while remaining:
                    chunk = incoming.read(min(65536, remaining))
                    if not chunk:
                        raise ValueError("Incomplete encrypted backup.")
                    remaining -= len(chunk)
                    outgoing.write(decryptor.update(chunk))
                outgoing.write(decryptor.finalize())
        # Publishing with a hard link refuses to overwrite an existing destination.
        os.link(partial, target)
    finally:
        if created:
            partial.unlink(missing_ok=True)


def main() -> None:
    """Prompt for the password without exposing it through command arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    try:
        decrypt_backup(args.source, args.target, getpass.getpass("Backup password: "))
    except (OSError, ValueError, InvalidTag):
        parser.exit(1, "Decryption failed: check the password, file, and output path.\n")


if __name__ == "__main__":
    main()
