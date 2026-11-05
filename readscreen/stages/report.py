"""Stage 10: coverage statistics, flags, per-taxon summary; results into the DB and result.json."""

from __future__ import annotations

import json

from .. import store
from ..layout import is_long
from ..logic.coverage import CoverageParams, compute
from ..logic.fastx import iter_fasta
from ..params import Param, Schema
from .common import StageContext

PARAMS: Schema = {
    "min_aligned_fraction": Param(float, 0.8, min=0, max=1, help="short reads: minimum aligned fraction of a read"),
    "min_aligned_length": Param(int, 500, min=1, help="long reads: minimum aligned length"),
    "min_identity": Param(float, None, min=0, max=1, help="minimum alignment identity (off if empty)"),
    "min_mapq": Param(int, 0, min=0, max=255, help="minimum MAPQ; MAPQ 0 reads are always counted as ambiguous"),
    "min_depth": Param(int, 1, min=1, help="depth at which a base counts as covered"),
    "bins": Param(int, 2000, min=10, help="maximum number of bins of the coverage plot"),
    "divergent_identity": Param(float, 0.90, min=0, max=1, help="flag 'divergent' below this mean identity"),
    "uneven_ratio": Param(float, 0.5, min=0, max=1, help="flag 'uneven' below this observed/expected breadth"),
    "ambiguous_fraction": Param(float, 0.5, min=0, max=1, help="flag 'ambiguous' above this share of MAPQ 0 reads"),
    "flag_min_reads": Param(int, 10, min=1, help="minimum reads before the 'uneven' flag is set"),
}


def run(ctx: StageContext) -> dict:
    p = ctx.params
    long = is_long(ctx.platform)
    refs_fa = ctx.stage_dir("extract") / "refs.fa"
    lengths, headers = {}, {}
    for cid, header, n in iter_fasta(refs_fa):
        lengths[cid] = n
        headers[cid] = header[len(cid):].strip()
    cp = CoverageParams(
        min_aligned_fraction=None if long else p["min_aligned_fraction"],
        min_aligned_length=p["min_aligned_length"] if long else None,
        min_identity=p["min_identity"], min_mapq=p["min_mapq"], min_depth=p["min_depth"],
        bins=p["bins"], divergent_identity=p["divergent_identity"], uneven_ratio=p["uneven_ratio"],
        ambiguous_fraction=p["ambiguous_fraction"], flag_min_reads=p["flag_min_reads"],
    )
    results = compute(ctx.stage_dir("map") / "alignments.paf", lengths, cp,
                      depth_out=ctx.out_dir / "depth.tsv.gz", check=ctx.check)
    meta = {
        r["ref"]: r
        for r in ctx.conn.execute(
            "SELECT ref, grp, score, taxid, species, title FROM ranking WHERE job_id = ?", (ctx.job_id,)
        )
    }
    refs = []
    for ref, res in results.items():
        m = meta.get(ref)
        res.update(
            title=(m["title"] if m else None) or headers.get(ref, ""),
            taxid=m["taxid"] if m else None,
            species=m["species"] if m else None,
            group=m["grp"] if m else None,
            blast_score=m["score"] if m else None,
        )
        refs.append(res)
    refs.sort(key=lambda r: (-r["reads"], -r["breadth"], r["ref"]))
    taxa = summarize_taxa(refs)

    with store.transaction(ctx.conn):
        ctx.conn.execute("DELETE FROM results WHERE job_id = ?", (ctx.job_id,))
        ctx.conn.executemany(
            "INSERT INTO results (job_id, ref, data) VALUES (?, ?, ?)",
            [(ctx.job_id, r["ref"], store.dumps(r)) for r in refs],
        )
    doc = {
        "job": {"id": ctx.job_id, "name": ctx.job["name"], "platform": ctx.platform},
        "params": p,
        "taxa": taxa,
        "references": refs,
    }
    (ctx.out_dir / "result.json").write_text(json.dumps(doc, separators=(",", ":")), encoding="utf-8")
    return {
        "references": len(refs),
        "with_reads": sum(1 for r in refs if r["reads"] > 0),
        "taxa": taxa,
    }


def _brief(r: dict) -> dict:
    return {k: r[k] for k in ("ref", "reads", "ambiguous", "breadth", "mean_depth", "max_depth", "identity", "flags")}


def summarize_taxa(refs: list[dict]) -> list[dict]:
    """Reads summed per taxon (taxid, or the reference itself without one) and the best reference."""
    groups: dict[str, list[dict]] = {}
    for r in refs:
        key = f"taxid:{r['taxid']}" if r.get("taxid") else f"ref:{r['ref']}"
        groups.setdefault(key, []).append(r)
    taxa = []
    for key, members in groups.items():
        best = max(members, key=lambda r: (r["reads"], r["breadth"]))
        taxa.append({
            "key": key,
            "taxid": best.get("taxid"),
            "name": best.get("species") or best.get("title") or best["ref"],
            "reads": sum(r["reads"] for r in members),
            "ambiguous": sum(r["ambiguous"] for r in members),
            "references": [r["ref"] for r in members],
            "best": _brief(best),
        })
    taxa.sort(key=lambda t: (-t["reads"], t["name"]))
    return taxa
