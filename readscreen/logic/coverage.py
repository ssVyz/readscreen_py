"""Coverage statistics from minimap2 PAF output (written with -c, so CIGARs are present).

Per reference a difference array (4 bytes per base) is updated from the CIGAR: M/=/X add
depth, D/N move on without adding depth (like the default of `samtools depth`).
"""

from __future__ import annotations

import gzip
import math
import re
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

_CIGAR = re.compile(r"(\d+)([MIDNSHP=X])")


@dataclass
class CoverageParams:
    min_aligned_fraction: float | None = 0.8  # short reads
    min_aligned_length: int | None = None  # long reads
    min_identity: float | None = None
    min_mapq: int = 0
    min_depth: int = 1
    bins: int = 2000
    divergent_identity: float = 0.90
    uneven_ratio: float = 0.5
    ambiguous_fraction: float = 0.5
    flag_min_reads: int = 10


@dataclass
class _Acc:
    length: int
    diff: array | None = None
    reads: int = 0
    ambiguous: int = 0
    candidates: int = 0  # primary alignments passing all filters except MAPQ
    matches: int = 0
    block: int = 0


def _passes(f: list[str], p: CoverageParams) -> bool:
    qlen, qs, qe = int(f[1]), int(f[2]), int(f[3])
    aligned = qe - qs
    if p.min_aligned_fraction is not None and aligned < p.min_aligned_fraction * qlen:
        return False
    if p.min_aligned_length is not None and aligned < p.min_aligned_length:
        return False
    if p.min_identity is not None:
        block = int(f[10])
        if block == 0 or int(f[9]) / block < p.min_identity:
            return False
    return True


def compute(
    paf: str | Path,
    ref_lengths: dict[str, int],
    params: CoverageParams | None = None,
    depth_out: str | Path | None = None,
    check: Callable[[], None] | None = None,
) -> dict[str, dict]:
    """Coverage results for every reference in `ref_lengths` (and any other one in the PAF)."""
    p = params or CoverageParams()
    acc = {r: _Acc(n) for r, n in ref_lengths.items()}
    with open(paf, encoding="utf-8", errors="replace") as fh:
        for n, line in enumerate(fh):
            if check is not None and n % 200_000 == 0:
                check()
            f = line.rstrip("\r\n").split("\t")
            if len(f) < 12 or not _passes(f, p):
                continue
            ref = f[5]
            a = acc.get(ref)
            if a is None:
                a = acc[ref] = _Acc(int(f[6]))
            tags = f[12:]
            primary = not any(t == "tp:A:S" or t == "tp:A:I" for t in tags)
            mapq = int(f[11])
            if primary:
                a.candidates += 1
                if mapq == 0:
                    a.ambiguous += 1
            if mapq < p.min_mapq:
                continue
            if primary:
                a.reads += 1
                a.matches += int(f[9])
                a.block += int(f[10])
            if a.diff is None:
                a.diff = array("i", bytes(4 * (a.length + 1)))
            d = a.diff
            pos = int(f[7])
            cg = next((t[5:] for t in tags if t.startswith("cg:Z:")), None)
            if cg is None:
                d[pos] += 1
                d[min(int(f[8]), a.length)] -= 1
                continue
            for num, op in _CIGAR.findall(cg):
                k = int(num)
                if op in "M=X":
                    end = min(pos + k, a.length)
                    if pos < end:
                        d[pos] += 1
                        d[end] -= 1
                    pos += k
                elif op in "DN":
                    pos += k

    results: dict[str, dict] = {}
    depth_fh = gzip.open(depth_out, "wt", compresslevel=1, newline="\n") if depth_out else None
    try:
        if depth_fh:
            depth_fh.write("ref\tpos\tdepth\n")
        for ref, a in acc.items():
            if check is not None:
                check()
            results[ref] = _stats(ref, a, p, depth_fh)
    finally:
        if depth_fh:
            depth_fh.close()
    return results


def _stats(ref: str, a: _Acc, p: CoverageParams, depth_fh) -> dict:
    L = a.length
    nbins = max(1, min(p.bins, L)) if L else 1
    bin_mean = [0.0] * nbins
    bin_max = [0] * nbins
    covered = covered_min = total = mx = 0
    if a.diff is not None and L:
        d = a.diff
        depth = 0
        b, bstart = 0, 0
        bend = L * 1 // nbins
        bsum, bmax = 0, 0
        lines: list[str] = []
        for i in range(L):
            depth += d[i]
            if depth:
                covered += 1
                total += depth
                if depth > mx:
                    mx = depth
                if depth >= p.min_depth:
                    covered_min += 1
            if i >= bend:
                bin_mean[b] = bsum / max(1, bend - bstart)
                bin_max[b] = bmax
                b += 1
                bstart, bend = i, L * (b + 1) // nbins
                bsum, bmax = 0, 0
            bsum += depth
            if depth > bmax:
                bmax = depth
            if depth_fh is not None:
                lines.append(f"{ref}\t{i + 1}\t{depth}\n")
                if len(lines) >= 100_000:
                    depth_fh.write("".join(lines))
                    lines.clear()
        bin_mean[b] = bsum / max(1, bend - bstart)
        bin_max[b] = bmax
        if depth_fh is not None and lines:
            depth_fh.write("".join(lines))
    mean = total / L if L else 0.0
    expected = 1 - math.exp(-mean)
    breadth_1x = covered / L if L else 0.0
    identity = a.matches / a.block if a.block else None
    amb_frac = a.ambiguous / a.candidates if a.candidates else 0.0
    ratio = breadth_1x / expected if expected > 0 else None
    flags = []
    if a.reads > 0 and identity is not None and identity < p.divergent_identity:
        flags.append("divergent")
    if a.reads >= p.flag_min_reads and ratio is not None and ratio < p.uneven_ratio:
        flags.append("uneven")
    if a.candidates > 0 and amb_frac > p.ambiguous_fraction:
        flags.append("ambiguous")
    return {
        "ref": ref,
        "length": L,
        "reads": a.reads,
        "ambiguous": a.ambiguous,
        "breadth": round(covered_min / L, 5) if L else 0.0,
        "breadth_1x": round(breadth_1x, 5),
        "mean_depth": round(mean, 4),
        "mean_depth_covered": round(total / covered, 4) if covered else 0.0,
        "max_depth": mx,
        "identity": None if identity is None else round(identity, 4),
        "expected_breadth": round(expected, 5),
        "breadth_ratio": None if ratio is None else round(ratio, 4),
        "flags": flags,
        "bins": {
            "count": nbins,
            "mean": [round(v, 3) for v in bin_mean],
            "max": bin_max,
        },
    }
