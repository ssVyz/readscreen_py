"""Finding the mate file of paired reads (R1 <-> R2) from the file name."""

from __future__ import annotations

import re
from pathlib import Path

_FASTQ_END = r"(?=\.(?:fastq|fq)(?:\.gz|\.bz2)?$)"
# (marker of read 1, marker of read 2, what must follow the marker)
_PATTERNS = [
    ("_R1", "_R2", r"(?=[_.])"),
    (".R1", ".R2", r"(?=[_.])"),
    ("_r1", "_r2", r"(?=[_.])"),
    (".r1", ".r2", r"(?=[_.])"),
    ("_1", "_2", _FASTQ_END),
    (".1", ".2", _FASTQ_END),
]


def _find(name: str, marker: str, follow: str) -> list[re.Match]:
    return list(re.finditer(re.escape(marker) + follow, name))


def mate_role(path: str | Path) -> str | None:
    """'r1' or 'r2' if the file name marks the file as one mate, else None."""
    name = Path(path).name
    for m1, m2, follow in _PATTERNS:
        if _find(name, m1, follow):
            return "r1"
        if _find(name, m2, follow):
            return "r2"
    return None


def find_mate(path: str | Path) -> Path | None:
    """The existing mate file of `path`, or None."""
    p = Path(path)
    role = mate_role(p)
    if role is None:
        return None
    for m1, m2, follow in _PATTERNS:
        src, dst = (m1, m2) if role == "r1" else (m2, m1)
        for m in reversed(_find(p.name, src, follow)):
            cand = p.with_name(p.name[: m.start()] + dst + p.name[m.end():])
            if cand != p and cand.is_file():
                return cand
    return None
