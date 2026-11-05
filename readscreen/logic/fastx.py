"""FASTQ/FASTA helpers: opening compressed files, counting, FASTQ→FASTA with subsampling,
FASTA statistics and contig depths from assembler headers."""

from __future__ import annotations

import bz2
import gzip
import math
import re
from pathlib import Path
from typing import IO, Callable, Iterator


def open_bin(path: str | Path) -> IO[bytes]:
    """Open plain, gzip or bzip2 files for binary reading (detected by magic bytes)."""
    p = Path(path)
    with open(p, "rb") as fh:
        magic = fh.read(3)
    if magic[:2] == b"\x1f\x8b":
        return gzip.open(p, "rb")
    if magic == b"BZh":
        return bz2.open(p, "rb")
    return open(p, "rb")


def iter_fastq(path: str | Path) -> Iterator[tuple[bytes, bytes]]:
    """(header line without '@' and newline, sequence) for each record."""
    with open_bin(path) as fh:
        while True:
            header = fh.readline()
            if not header:
                return
            seq = fh.readline()
            fh.readline()
            fh.readline()
            if not header.startswith(b"@"):
                raise ValueError(f"{path}: not a FASTQ file (record header {header[:40]!r})")
            yield header[1:].rstrip(b"\r\n"), seq.rstrip(b"\r\n")


def count_fastq(path: str | Path) -> int:
    n = 0
    with open_bin(path) as fh:
        for n, _ in enumerate(fh, 1):
            pass
    return n // 4


def _read_name(header: bytes, mate: int | None) -> bytes:
    name = header.split(None, 1)[0] if header else b"read"
    if mate is not None and not name.endswith(b"/%d" % mate):
        name += b"/%d" % mate
    return name


def fastq_to_fasta(
    files: list[Path],
    out: Path,
    max_reads: int | None = None,
    check: Callable[[], None] | None = None,
) -> dict:
    """Write reads (one file, or two mate files read in lockstep) as FASTA.

    With more than `max_reads` reads (pairs count as one unit), every k-th read/pair is used,
    k = ceil(total / max_reads), from a counting pass. Mates get /1 and /2 name suffixes so
    BLAST query ids stay unique.
    """
    total = count_fastq(files[0])
    step = 1 if not max_reads or total <= max_reads else math.ceil(total / max_reads)
    paired = len(files) == 2
    iters = [iter_fastq(f) for f in files]
    written = 0
    with open(out, "wb") as fh:
        for i, recs in enumerate(zip(*iters)):
            if check is not None and i % 100_000 == 0:
                check()
            if i % step:
                continue
            for mate, (header, seq) in enumerate(recs, 1):
                fh.write(b">" + _read_name(header, mate if paired else None) + b"\n" + seq + b"\n")
            written += 1
    return {"total": total, "used": written, "step": step, "paired": paired}


def iter_fasta(path: str | Path) -> Iterator[tuple[str, str, int]]:
    """(id, full header, sequence length) for each record."""
    header, length = None, 0
    with open_bin(path) as fh:
        for raw in fh:
            line = raw.rstrip(b"\r\n")
            if line.startswith(b">"):
                if header is not None:
                    yield header.split(None, 1)[0] if header else "", header, length
                header, length = line[1:].decode(errors="replace"), 0
            elif header is not None:
                length += len(line.strip())
    if header is not None:
        yield header.split(None, 1)[0] if header else "", header, length


def fasta_stats(path: str | Path) -> dict:
    lengths = sorted((n for _, _, n in iter_fasta(path)), reverse=True)
    total = sum(lengths)
    n50, acc = 0, 0
    for n in lengths:
        acc += n
        if acc * 2 >= total:
            n50 = n
            break
    return {"sequences": len(lengths), "bases": total, "longest": lengths[0] if lengths else 0, "n50": n50}


_MULTI = re.compile(r"(?:^|\s)multi=([0-9.]+)")
_SPADES_COV = re.compile(r"_cov_([0-9.]+)")


def contig_depths(path: str | Path) -> dict[str, float]:
    """Contig depth from MEGAHIT/shortread_assembler (`multi=`) or SPAdes (`_cov_`) headers."""
    out = {}
    for cid, header, _ in iter_fasta(path):
        m = _MULTI.search(header) or _SPADES_COV.search(cid)
        if m:
            out[cid] = float(m.group(1))
    return out


def looks_like_fasta(path: str | Path) -> bool:
    with open_bin(path) as fh:
        for raw in fh:
            if raw.strip():
                return raw.startswith(b">")
    return False
