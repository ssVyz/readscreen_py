"""Quick file identity check: size plus a hash of the first and last megabyte."""

from __future__ import annotations

import hashlib
from pathlib import Path

CHUNK = 1 << 20


def fingerprint(path: str | Path) -> str:
    p = Path(path)
    size = p.stat().st_size
    h = hashlib.sha256()
    h.update(str(size).encode())
    with open(p, "rb") as fh:
        h.update(fh.read(CHUNK))
        if size > 2 * CHUNK:
            fh.seek(size - CHUNK)
            h.update(fh.read(CHUNK))
        elif size > CHUNK:
            h.update(fh.read())
    return h.hexdigest()


def file_record(path: str | Path) -> dict:
    """Path (absolute), size and fingerprint of an existing file."""
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise FileNotFoundError(f"file not found: {p}")
    return {"path": str(p), "size": p.stat().st_size, "fingerprint": fingerprint(p)}
