"""Stage 4: shortread_assembler, or an imported external assembly."""

from __future__ import annotations

import shutil

from .. import layout
from ..layout import is_long
from ..logic.fastx import fasta_stats, looks_like_fasta
from ..params import Param, ParamError, Schema
from .common import StageContext, StageError, check_reserved, current_reads

PARAMS: Schema = {
    "mode": Param(
        str, lambda platform: "skip" if platform and is_long(platform) else "assemble",
        choices=("assemble", "external", "skip"),
        help="assemble the reads, use an imported assembly, or skip (BLAST the reads)",
    ),
    "k": Param(int, 31, min=15, max=63, help="k-mer size (odd)"),
    "min_count": Param(int, 2, min=1, help="minimum (k+1)-mer count"),
    "min_contig_len": Param(int, 200, min=1, help="minimum contig length"),
    "memory": Param(str, None, help="memory limit (fraction or size like 16G); default from settings"),
    "fragment_length": Param(int, 150, min=50, help="long reads: fragment length for the assembler"),
    "extra_args": Param(list, [], help="additional shortread_assembler arguments"),
}

RESERVED = ("-1", "--r1", "-2", "--r2", "-r", "--read", "-o", "--out-dir", "-f", "--force", "-t", "--threads")


def is_skipped(params: dict) -> bool:
    return params["mode"] == "skip"


def check(params: dict, platform: str) -> None:
    if params["k"] % 2 == 0:
        raise ParamError("k: must be odd")


def run(ctx: StageContext) -> dict:
    p = ctx.params
    contigs = ctx.out_dir / "contigs.fa"
    if p["mode"] == "external":
        src = layout.imports_dir(ctx.env, ctx.job_id) / "assembly.fa"
        if not src.is_file():
            raise StageError("no imported assembly found (assign one with the assembly input)")
        if not looks_like_fasta(src):
            raise StageError("the imported assembly is not a FASTA file")
        shutil.copyfile(src, contigs)
        return {"mode": "external", **fasta_stats(contigs)}

    check_reserved(p["extra_args"], RESERVED, "assemble")
    reads = current_reads(ctx)
    asm_dir = ctx.out_dir / "asm"
    fragments = ctx.out_dir / "fragments.fastq.gz"
    if is_long(ctx.platform):
        ctx.run_tool([
            ctx.tool("simpletrim"), "long", "-w", ctx.threads,
            "-i", reads.files[0], "-o", fragments, "--force",
            "--disable-adapter-trimming", "--disable-quality-filter", "--disable-length-filter",
            "--fragment-length", p["fragment_length"],
        ])
        read_args = ["-r", fragments]
    elif reads.paired:
        read_args = ["-1", reads.files[0], "-2", reads.files[1]]
    else:
        read_args = ["-r", reads.files[0]]
    memory = p["memory"] or ctx.settings["assembler_memory"]
    ctx.run_tool([
        ctx.tool("shortread_assembler"), *read_args, "-o", asm_dir, "--force",
        "-k", p["k"], "--min-count", p["min_count"], "--min-contig-len", p["min_contig_len"],
        "-t", ctx.threads, "-m", memory, *p["extra_args"],
    ])
    produced = asm_dir / "contigs.fa"
    if not produced.is_file():
        raise StageError("shortread_assembler did not write contigs.fa")
    layout.safe_rename(produced, contigs)
    layout.safe_rmtree(ctx.env, asm_dir)
    if fragments.exists():
        layout.safe_rmtree(ctx.env, fragments)
    return {"mode": "assemble", "reads": reads.source, **fasta_stats(contigs)}
