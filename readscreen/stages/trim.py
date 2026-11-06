"""Stage 3: simpletrim short/long.

Parameters left empty (None) are not passed, so simpletrim uses its own default for the mode;
`tool_default` documents that value for the web interface.
"""

from __future__ import annotations

import json

from ..layout import is_long
from ..params import Param, Schema
from .common import StageContext, StageError, check_reserved, merge_flags, raw_reads

QUALITY = "Quality filter"
LENGTH = "Length filter"
WINDOW = "Window trimming"
FIXED = "Fixed trimming"
ADAPTERS = "Adapters"
TAILS = "Tails"
STRETCHES = "Low-quality stretches"
OTHER = "Other"

PARAMS: Schema = {
    "skip": Param(bool, False, help="skip trimming; later stages use the input reads"),
    "mode": Param(
        str, "auto", choices=("auto", "short", "long"),
        help="simpletrim mode; auto: long for nanopore and PacBio, short for the other platforms",
    ),
    "apply_suggestions": Param(bool, True, help="apply the settings suggested by the analyze stage"),
    # quality
    "quality_filter": Param(bool, True, group=QUALITY,
                            help="discard reads with too many low-quality bases (the next two settings)"),
    "min_base_quality": Param(int, None, min=0, max=93, group=QUALITY, tool_default=15,
                              help="a base counts as good from this phred quality (--qualified-quality)"),
    "max_low_quality_percent": Param(
        int, None, min=0, max=100, group=QUALITY, tool_default=40,
        help="maximum percentage of bases below that quality (--unqualified-percent-limit)",
    ),
    "min_mean_quality": Param(
        int, None, min=0, max=93, group=QUALITY, tool_default="0 (off)",
        help="minimum mean read quality, 0 = off (--average-quality); long reads use the error-probability"
             " mean, Q7 to Q10 is common for nanopore",
    ),
    # length
    "min_length": Param(int, None, min=0, group=LENGTH, tool_default={"short": 15, "long": 20},
                        help="discard reads shorter than this after trimming"),
    "max_length": Param(int, None, min=0, group=LENGTH, tool_default="0 (no limit)",
                        help="discard reads longer than this after trimming, 0 = no limit"),
    # sliding window
    "cut_front": Param(bool, False, group=WINDOW, help="cut low-quality bases from the 5' end (sliding window)"),
    "cut_tail": Param(bool, False, group=WINDOW, help="cut low-quality bases from the 3' end (sliding window)"),
    "cut_right": Param(bool, False, group=WINDOW, only="short",
                       help="from the 5' end, cut at the first low-quality window and drop the rest"),
    "cut_window_size": Param(int, None, min=1, group=WINDOW, tool_default=4, help="window size"),
    "cut_mean_quality": Param(
        int, None, min=1, max=60, group=WINDOW, tool_default={"short": 20, "long": 17},
        help="mean quality required in the window (long reads: error-probability mean)",
    ),
    # fixed
    "trim_front": Param(int, None, min=0, group=FIXED, tool_default=0,
                        help="bases removed from the 5' end of every read (both reads of a pair)"),
    "trim_tail": Param(int, None, min=0, group=FIXED, tool_default=0,
                       help="bases removed from the 3' end of every read (both reads of a pair)"),
    # adapters
    "adapter_trimming": Param(bool, True, group=ADAPTERS, help="trim adapters (off: --disable-adapter-trimming)"),
    "adapter_preset": Param(str, None, choices=("truseq", "nextera", "smallrna", "mgi"), group=ADAPTERS,
                            only="short", help="a common adapter pair; explicit adapters below take precedence"),
    "adapter_r1": Param(str, None, group=ADAPTERS, only="short", tool_default="auto",
                        help="adapter of read 1: a sequence, auto or none"),
    "adapter_r2": Param(str, None, group=ADAPTERS, only="short", tool_default="auto",
                        help="adapter of read 2: a sequence, auto or none"),
    "start_adapter": Param(str, None, group=ADAPTERS, only="long", tool_default="auto",
                           help="adapter at the read start: a sequence, auto or none"),
    "end_adapter": Param(str, None, group=ADAPTERS, only="long", tool_default="auto",
                         help="adapter at the read end: a sequence, auto or none"),
    "chimeric_reads": Param(
        str, None, choices=("split", "discard", "keep"), group=ADAPTERS, only="long", tool_default="split",
        help="reads with an adapter inside: split them there, discard them, or keep them",
    ),
    # tails
    "poly_g": Param(str, "auto", choices=("auto", "on", "off"), group=TAILS, only="short",
                    help="poly-G tail trimming; auto: on for two-colour instruments (NextSeq, NovaSeq)"),
    "trim_poly_x": Param(bool, False, group=TAILS, help="trim homopolymer tails of any base"),
    # long reads: low-quality stretches inside reads
    "low_quality_mode": Param(
        str, None, choices=("split", "longest"), group=STRETCHES, only="long", tool_default="off",
        help="remove low-quality stretches inside reads: split keeps every remaining piece, longest only"
             " the longest",
    ),
    "low_quality_threshold": Param(int, None, min=1, max=60, group=STRETCHES, only="long", tool_default=7,
                                   help="windows with a mean quality below this are low quality"),
    "extra_args": Param(list, [], group=OTHER, help="additional simpletrim arguments"),
}

RESERVED = (
    "--r1", "--r2", "--o1", "--o2", "-i", "--input", "-o", "--output", "--report", "--json",
    "--force", "-w", "--threads", "--orphan1", "--orphan2", "--failed-output",
)
ADAPTER_OPTIONS = ("--adapter-r1", "--adapter-r2", "--adapter-preset", "--start-adapter", "--end-adapter",
                   "-s", "-e")


def is_skipped(params: dict) -> bool:
    return bool(params["skip"])


def resolve_mode(params: dict, platform: str) -> str:
    """'short' or 'long': the chosen mode, or the one that fits the platform."""
    mode = params.get("mode") or "auto"
    if mode == "auto":
        return "long" if is_long(platform) else "short"
    return mode


def build_flags(params: dict, long: bool) -> list[str]:
    p = params
    flags: list[str] = []

    def opt(name: str, flag: str) -> None:
        if p.get(name) is not None:
            flags.extend([flag, str(p[name])])

    if not p.get("quality_filter", True):
        flags.append("--disable-quality-filter")
    opt("min_base_quality", "--qualified-quality")
    opt("max_low_quality_percent", "--unqualified-percent-limit")
    opt("min_mean_quality", "--average-quality")
    opt("min_length", "--min-length")
    opt("max_length", "--max-length")
    if p.get("cut_front"):
        flags.append("--cut-front")
    if p.get("cut_tail"):
        flags.append("--cut-tail")
    opt("cut_window_size", "--cut-window-size")
    opt("cut_mean_quality", "--cut-mean-quality")
    if not p.get("adapter_trimming", True):
        flags.append("--disable-adapter-trimming")
    if p.get("trim_poly_x"):
        flags.append("--trim-poly-x")
    if long:
        opt("trim_front", "--trim-front")
        opt("trim_tail", "--trim-tail")
        if p.get("adapter_trimming", True):
            opt("start_adapter", "--start-adapter")
            opt("end_adapter", "--end-adapter")
            if p.get("chimeric_reads") == "discard":
                flags.append("--discard-chimeric-reads")
            elif p.get("chimeric_reads") == "keep":
                flags.append("--disable-chimera-splitting")
        opt("low_quality_mode", "--low-quality-mode")
        opt("low_quality_threshold", "--low-quality-threshold")
    else:
        opt("trim_front", "--trim-front1")
        opt("trim_tail", "--trim-tail1")
        if p.get("cut_right"):
            flags.append("--cut-right")
        if p.get("poly_g") == "on":
            flags.append("--trim-poly-g")
        elif p.get("poly_g") == "off":
            flags.append("--disable-trim-poly-g")
        if p.get("adapter_trimming", True):
            opt("adapter_preset", "--adapter-preset")
            opt("adapter_r1", "--adapter-r1")
            opt("adapter_r2", "--adapter-r2")
    return flags


def _without_adapter_options(flags: list[str]) -> list[str]:
    out, skip = [], False
    for t in flags:
        if t.startswith("-"):
            skip = t in ADAPTER_OPTIONS
        if not skip:
            out.append(t)
    return out


def run(ctx: StageContext) -> dict:
    p = ctx.params
    mode = resolve_mode(p, ctx.platform)
    long = mode == "long"
    reads = raw_reads(ctx)
    if long and reads.paired:
        raise StageError("trim.mode 'long' takes one read file, but this job has paired reads")
    check_reserved(p["extra_args"], RESERVED, "trim")
    report = ctx.out_dir / "report.json"
    if long:
        outputs = {"r1": "trimmed.fastq.gz"}
        cmd = [ctx.tool("simpletrim"), "long", "-w", ctx.threads,
               "-i", reads.files[0], "-o", ctx.out_dir / outputs["r1"]]
    else:
        outputs = {"r1": "trimmed_R1.fastq.gz"}
        cmd = [ctx.tool("simpletrim"), "short", "-w", ctx.threads,
               "--r1", reads.files[0], "--o1", ctx.out_dir / outputs["r1"]]
        if reads.paired:
            outputs["r2"] = "trimmed_R2.fastq.gz"
            cmd += ["--r2", reads.files[1], "--o2", ctx.out_dir / outputs["r2"]]
    cmd += ["--report", report, "--force"]

    flags = build_flags(p, long) + list(p["extra_args"])
    applied: list[str] = []
    if p["apply_suggestions"]:
        analysis = ctx.stage_row("analyze")
        summary = analysis["summary"]
        if analysis["state"] == "done" and summary.get("suggestion_mode") == mode:
            for s in summary.get("suggestions", []):
                extra = [str(x) for x in s.get("flags", [])]
                if not p.get("adapter_trimming", True):
                    extra = _without_adapter_options(extra)
                flags, added = merge_flags(flags, extra)
                applied += added
    ctx.log(f"simpletrim {mode} mode" + ("" if p.get("mode", "auto") != "auto" else " (from the platform)"))
    ctx.run_tool(cmd + flags)
    try:
        rep = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StageError(f"cannot read the simpletrim report: {exc}") from None
    for name in outputs.values():
        if not (ctx.out_dir / name).is_file():
            raise StageError(f"simpletrim did not write {name}")
    for key in ("inputs", "outputs"):  # paths of the temporary folder; see "outputs" below instead
        rep.pop(key, None)
    return {
        "mode": mode,
        "outputs": outputs,
        "reads_in": rep.get("records_in", rep.get("reads_in")),  # short: records_in, long: reads_in
        "reads_out": rep.get("records_written"),
        "applied_suggestions": applied,
        "flags": flags,
        "report": rep,
    }
