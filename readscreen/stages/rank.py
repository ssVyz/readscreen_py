"""Stage 6: rank the BLAST hits and make the automatic selection (Python)."""

from __future__ import annotations

from .. import store
from ..logic.fastx import contig_depths
from ..logic.ranking import RankParams, auto_select, rank, read_blast
from ..params import Param, Schema
from .common import StageContext

PARAMS: Schema = {
    "min_aln_len": Param(int, 50, min=1, help="alignments shorter than this are ignored"),
    "top_pct": Param(float, 0.10, min=0, max=1, help="a query supports references within this share of its best score"),
    "max_overlap": Param(float, 0.2, min=0, max=1, help="allowed overlap between alignments of one query and reference"),
    "group_share": Param(float, 0.5, min=0, max=1, help="share of shared queries that puts a reference into a group"),
    "top_groups": Param(int, 10, min=0, help="automatic selection: number of groups"),
    "alternatives": Param(int, 2, min=0, help="automatic selection: alternatives per group besides the leader"),
    "min_score": Param(float, 100.0, min=0, help="automatic selection: minimum group score"),
}

COLUMNS = ("ref", "group", "leader", "score", "queries", "ref_cov", "identity", "depth",
           "length", "taxid", "species", "title", "weak", "auto_selected")


def run(ctx: StageContext) -> dict:
    p = ctx.params
    blast = ctx.stage_row("blast")
    hsps, info = read_blast(ctx.stage_dir("blast") / "blast.tsv", p["min_aln_len"])
    depths = None
    if blast["summary"].get("mode") == "contigs":
        depths = contig_depths(ctx.stage_dir("assemble") / "contigs.fa") or None
    rows = rank(
        hsps, info,
        RankParams(p["min_aln_len"], p["top_pct"], p["max_overlap"], p["group_share"]),
        depths=depths, check=ctx.check,
    )
    chosen = auto_select(rows, p["top_groups"], p["alternatives"], p["min_score"])
    for r in rows:
        r["auto_selected"] = r["ref"] in chosen

    with store.transaction(ctx.conn):
        ctx.conn.execute("DELETE FROM ranking WHERE job_id = ?", (ctx.job_id,))
        ctx.conn.executemany(
            "INSERT INTO ranking (job_id, ref, grp, leader, score, queries, ref_cov, identity, depth,"
            " length, taxid, species, title, weak, auto_selected, selected)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (ctx.job_id, r["ref"], r["group"], int(r["leader"]), r["score"], r["queries"],
                 r["ref_cov"], r["identity"], r["depth"], r["length"], r["taxid"], r["species"],
                 r["title"], int(r["weak"]), int(r["auto_selected"]), int(r["auto_selected"]))
                for r in rows
            ],
        )
    with open(ctx.out_dir / "ranking.tsv", "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\t".join(COLUMNS) + "\n")
        for r in rows:
            fh.write("\t".join("" if r[c] is None else str(r[c]) for c in COLUMNS) + "\n")
    leaders = [r for r in rows if r["leader"]]
    return {
        "references_hit": len(info),
        "references_supported": len(rows),
        "groups": len(leaders),
        "auto_selected": len(chosen),
        "top": [
            {"group": r["group"], "ref": r["ref"], "name": r["species"] or r["title"], "score": r["score"],
             "queries": r["queries"], "ref_cov": r["ref_cov"], "identity": r["identity"]}
            for r in leaders[:15]
        ],
    }
