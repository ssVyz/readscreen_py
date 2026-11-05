"""Stage 5: blastn of the contigs, or of the reads (FASTQ→FASTA, subsampled)."""

from __future__ import annotations

from .. import layout
from ..logic.fastx import fasta_stats, fastq_to_fasta
from ..logic.ranking import OUTFMT
from ..params import Param, Schema
from .common import StageContext, check_reserved, count_lines, current_reads, resolve_db

PARAMS: Schema = {
    "db": Param(str, None, help="BLAST database (name relative to the database folder)"),
    "task": Param(
        str, "auto", choices=("auto", "megablast", "dc-megablast", "blastn"),
        help="auto: dc-megablast for contigs, megablast for reads",
    ),
    "evalue": Param(float, None, min=0, help="e-value cutoff; default 1e-10 for contigs, 1e-5 for reads"),
    "max_target_seqs": Param(int, 100, min=1, help="maximum hits per query"),
    "max_reads": Param(int, 2_000_000, min=1, help="read mode: reads (pairs) above this are subsampled"),
    "extra_args": Param(list, [], help="additional blastn arguments"),
}

RESERVED = ("-query", "-db", "-out", "-outfmt", "-num_threads", "-subject", "-task", "-evalue", "-max_target_seqs")


def run(ctx: StageContext) -> dict:
    p = ctx.params
    check_reserved(p["extra_args"], RESERVED, "blast")
    db = resolve_db(ctx.settings, p["db"])
    out_tsv = ctx.out_dir / "blast.tsv"
    assembly = ctx.stage_row("assemble")
    subsample = None
    if assembly["state"] == "done":
        mode = "contigs"
        query = ctx.stage_dir("assemble") / "contigs.fa"
        n_queries = fasta_stats(query)["sequences"]
    else:
        mode = "reads"
        reads = current_reads(ctx)
        query = ctx.out_dir / "query.fa"
        ctx.log(f"converting {reads.source} reads to FASTA (max {p['max_reads']} reads/pairs)")
        subsample = fastq_to_fasta(reads.files, query, p["max_reads"], check=ctx.check)
        n_queries = subsample["used"] * (2 if reads.paired else 1)
    task = p["task"] if p["task"] != "auto" else ("dc-megablast" if mode == "contigs" else "megablast")
    evalue = p["evalue"] if p["evalue"] is not None else (1e-10 if mode == "contigs" else 1e-5)
    summary = {"mode": mode, "task": task, "evalue": evalue, "db": str(db), "queries": n_queries,
               "subsample": subsample}
    if n_queries == 0:
        out_tsv.write_text("")
        ctx.log("no query sequences; BLAST skipped")
        return {**summary, "hsps": 0, "queries_with_hits": 0}
    ctx.run_tool(
        [ctx.tool("blastn"), "-task", task, "-query", query, "-db", db, "-evalue", evalue,
         "-max_target_seqs", p["max_target_seqs"], "-num_threads", ctx.threads,
         "-outfmt", OUTFMT, "-out", out_tsv, *p["extra_args"]],
        env={"BLASTDB": str(db.parent)},
    )
    if mode == "reads":
        layout.safe_rmtree(ctx.env, query)
    hit_queries = set()
    with open(out_tsv, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            hit_queries.add(line.split("\t", 1)[0])
    return {**summary, "hsps": count_lines(out_tsv), "queries_with_hits": len(hit_queries)}
