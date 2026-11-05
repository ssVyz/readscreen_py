"""Ranking of BLAST hits: which reference sequences are potentially relevant for a read set.

Steps (see instructions.md, "Hit ranking"):
1. drop alignments shorter than `min_aln_len`;
2. per query and reference keep non-overlapping alignments (greedy by bitscore; an alignment
   overlapping accepted ones by more than `max_overlap` on the query or the reference is skipped);
   pair score = sum of the accepted bitscores;
3. a query supports only references within `top_pct` of its best pair score;
4. reference score = bitscore per base summed over the reference, each position counted once
   (the alignment with the highest bitscore per base wins);
5. grouping: same taxid, or >= `group_share` of the score from queries the group already has.
"""

from __future__ import annotations

import bisect
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

BLAST_COLUMNS = (
    "qseqid", "saccver", "pident", "length", "qstart", "qend", "qlen", "slen",
    "sstart", "send", "evalue", "bitscore", "staxids", "sscinames", "stitle",
)
OUTFMT = "6 " + " ".join(BLAST_COLUMNS)


@dataclass(frozen=True)
class Hsp:
    bits: float
    qs: int
    qe: int
    ss: int
    se: int
    pident: float
    length: int


@dataclass
class RefInfo:
    length: int
    taxid: str | None
    species: str | None
    title: str


@dataclass
class RankParams:
    min_aln_len: int = 50
    top_pct: float = 0.10
    max_overlap: float = 0.2
    group_share: float = 0.5


@dataclass
class _Ref:
    ref: str
    score: float
    cov: int
    qs: dict[str, float]
    identity: float
    depth: float | None
    info: RefInfo
    group: int = 0
    leader: bool = False
    weak: bool = False
    auto_selected: bool = False


@dataclass
class _Group:
    members: list[_Ref] = field(default_factory=list)
    queries: set[str] = field(default_factory=set)
    taxa: set[str] = field(default_factory=set)


def _clean(value: str) -> str | None:
    v = value.strip()
    return None if v in ("", "N/A", "0") else v


def read_blast(path: str | Path, min_aln_len: int = 50) -> tuple[dict, dict[str, RefInfo]]:
    """Parse BLAST tabular output written with OUTFMT."""
    hsps: dict[tuple[str, str], list[Hsp]] = defaultdict(list)
    info: dict[str, RefInfo] = {}
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            f = line.rstrip("\r\n").split("\t")
            if len(f) < len(BLAST_COLUMNS):
                continue
            q, r, pid, ln, qs, qe, _ql, sl, ss, se, _ev, bs, tax, sci, title = f[:15]
            length = int(ln)
            if length < min_aln_len:
                continue
            s1, s2 = sorted((int(ss), int(se)))
            hsps[q, r].append(Hsp(float(bs), int(qs), int(qe), s1, s2, float(pid), length))
            if r not in info:
                info[r] = RefInfo(int(sl), _clean(tax.split(";")[0]), _clean(sci), title)
    return hsps, info


def _overlap(a: int, b: int, accepted: list[tuple[int, int]]) -> int:
    return sum(max(0, min(b, y) - max(a, x) + 1) for x, y in accepted)


def _pair_hsps(hs: list[Hsp], max_overlap: float) -> list[Hsp]:
    acc: list[Hsp] = []
    qa: list[tuple[int, int]] = []
    sa: list[tuple[int, int]] = []
    for h in sorted(hs, key=lambda x: -x.bits):
        n = h.qe - h.qs + 1
        if _overlap(h.qs, h.qe, qa) <= max_overlap * n and _overlap(h.ss, h.se, sa) <= max_overlap * n:
            acc.append(h)
            qa.append((h.qs, h.qe))
            sa.append((h.ss, h.se))
    return acc


def tiled_score(hs: list[Hsp]) -> tuple[float, int]:
    """(score, covered bases): each reference position counts once, best bits per base wins."""
    segs = sorted(((h.bits / (h.se - h.ss + 1), h.ss, h.se) for h in hs), reverse=True)
    starts: list[int] = []
    ends: list[int] = []
    score, cov = 0.0, 0
    for dens, s, e in segs:
        free, pos, i = 0, s, bisect.bisect_right(ends, s - 1)
        while pos <= e:
            if i < len(starts) and starts[i] <= e:
                if starts[i] > pos:
                    free += starts[i] - pos
                pos = max(pos, ends[i] + 1)
                i += 1
            else:
                free += e - pos + 1
                break
        score += free * dens
        cov += free
        j = bisect.bisect_left(ends, s - 1)
        k = bisect.bisect_right(starts, e + 1)
        ns, ne = (min(s, starts[j]), max(e, ends[k - 1])) if j < k else (s, e)
        starts[j:k] = [ns]
        ends[j:k] = [ne]
    return score, cov


def rank(
    hsps: dict[tuple[str, str], list[Hsp]],
    info: dict[str, RefInfo],
    params: RankParams | None = None,
    depths: dict[str, float] | None = None,
    check: Callable[[], None] | None = None,
) -> list[dict]:
    """Rank references; returns one dict per supported reference, best group first."""
    p = params or RankParams()
    pair: dict[tuple[str, str], tuple[float, list[Hsp]]] = {}
    for n, (key, hs) in enumerate(hsps.items()):
        if check is not None and n % 10_000 == 0:
            check()
        acc = _pair_hsps(hs, p.max_overlap)
        pair[key] = (sum(h.bits for h in acc), acc)

    best: dict[str, float] = defaultdict(float)
    for (q, _), (s, _) in pair.items():
        best[q] = max(best[q], s)
    support: dict[str, dict[str, tuple[float, list[Hsp]]]] = defaultdict(dict)
    for (q, r), (s, acc) in pair.items():
        if s > 0 and s >= (1 - p.top_pct) * best[q]:
            support[r][q] = (s, acc)

    refs: list[_Ref] = []
    for r, qs in support.items():
        allh = [h for _, acc in qs.values() for h in acc]
        score, cov = tiled_score(allh)
        aln = sum(h.length for h in allh)
        identity = sum(h.pident * h.length for h in allh) / aln if aln else 0.0
        depth = sum(depths.get(q, 0.0) for q in qs) if depths else None
        refs.append(_Ref(r, score, cov, {q: s for q, (s, _) in qs.items()}, identity, depth, info[r]))
    refs.sort(key=lambda x: (-x.score, x.ref))

    groups: list[_Group] = []
    for ref in refs:
        total = sum(ref.qs.values())
        for gi, g in enumerate(groups, 1):
            shared = sum(s for q, s in ref.qs.items() if q in g.queries)
            if (ref.info.taxid and ref.info.taxid in g.taxa) or shared >= p.group_share * total:
                break
        else:
            groups.append(_Group())
            gi, g = len(groups), groups[-1]
            ref.leader = True
        ref.group = gi
        g.members.append(ref)
        g.queries.update(ref.qs)
        if ref.info.taxid:
            g.taxa.add(ref.info.taxid)
        cov_frac = ref.cov / ref.info.length if ref.info.length else 0.0
        ref.weak = len(ref.qs) == 1 and cov_frac < 0.05

    out = []
    for g in groups:
        for ref in g.members:
            out.append(
                {
                    "ref": ref.ref,
                    "group": ref.group,
                    "leader": ref.leader,
                    "score": round(ref.score, 1),
                    "queries": len(ref.qs),
                    "ref_cov": round(ref.cov / ref.info.length, 5) if ref.info.length else 0.0,
                    "identity": round(ref.identity, 2),
                    "depth": None if ref.depth is None else round(ref.depth, 2),
                    "length": ref.info.length,
                    "taxid": ref.info.taxid,
                    "species": ref.info.species,
                    "title": ref.info.title,
                    "weak": ref.weak,
                }
            )
    return out


def auto_select(rows: list[dict], top_groups: int = 10, alternatives: int = 2, min_score: float = 100) -> set[str]:
    """Leader plus up to `alternatives` members of the best `top_groups` groups above `min_score`."""
    by_group: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        by_group[r["group"]].append(r)
    chosen: set[str] = set()
    taken = 0
    for gid in sorted(by_group):
        if taken >= top_groups:
            break
        members = sorted(by_group[gid], key=lambda r: (not r["leader"], -r["score"]))
        if members[0]["score"] < min_score:
            continue
        taken += 1
        chosen.update(r["ref"] for r in members[: 1 + alternatives])
    return chosen
