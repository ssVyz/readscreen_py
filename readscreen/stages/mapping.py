"""Stage 9: map the (trimmed, unfragmented) reads to the selected references with minimap2."""

from __future__ import annotations

from ..params import Param, Schema
from .common import StageContext, check_reserved, count_lines, current_reads

PARAMS: Schema = {
    "sensitivity": Param(
        str, "sensitive", choices=("normal", "sensitive", "very_sensitive"),
        help="short reads: normal (-x sr), sensitive (-k15 -w5), very_sensitive (-k11 -w3 -m15 -s25)",
    ),
    "preset": Param(
        str, "auto", choices=("auto", "sr", "map-ont", "lr:hq", "map-hifi", "map-pb"),
        help="minimap2 preset; auto chooses by platform",
    ),
    "extra_args": Param(list, [], help="additional minimap2 arguments"),
}

PRESETS = {"illumina": "sr", "ont": "map-ont", "hifi": "map-hifi", "clr": "map-pb"}
SENSITIVITY = {
    "normal": [],
    "sensitive": ["-k15", "-w5"],
    "very_sensitive": ["-k11", "-w3", "-m15", "-s25"],
}
RESERVED = ("-a", "-o", "-c", "--secondary", "-x", "-t", "--cs", "--MD")


def run(ctx: StageContext) -> dict:
    p = ctx.params
    check_reserved(p["extra_args"], RESERVED, "map")
    refs = ctx.stage_dir("extract") / "refs.fa"
    paf = ctx.out_dir / "alignments.paf"
    if ctx.stage_row("extract")["summary"].get("references", 0) == 0:
        paf.write_text("")
        ctx.log("no references selected; mapping skipped")
        return {"alignments": 0, "skipped": "no references selected"}
    reads = current_reads(ctx)
    preset = PRESETS[ctx.platform] if p["preset"] == "auto" else p["preset"]
    sens = SENSITIVITY[p["sensitivity"]] if preset == "sr" else []
    ctx.run_tool([
        ctx.tool("minimap2"), "-x", preset, *sens, "-c", "--secondary=no", "-t", ctx.threads,
        *p["extra_args"], "-o", paf, refs, *reads.files,
    ])
    return {
        "preset": preset,
        "sensitivity": p["sensitivity"] if preset == "sr" else None,
        "reads": reads.source,
        "alignments": count_lines(paf),
    }
