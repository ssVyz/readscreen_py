"""Stage 3: simpletrim short/long."""

from __future__ import annotations

import json

from ..layout import is_long
from ..params import Param, Schema
from .common import StageContext, StageError, check_reserved, merge_flags, raw_reads

PARAMS: Schema = {
    "skip": Param(bool, False, help="skip trimming; later stages use the input reads"),
    "apply_suggestions": Param(bool, True, help="apply the suggestions of the analyze stage"),
    "min_length": Param(int, None, min=0, help="minimum read length after trimming (tool default if empty)"),
    "cut_front": Param(bool, False, help="sliding-window quality cutting from the 5' end"),
    "cut_tail": Param(bool, False, help="sliding-window quality cutting from the 3' end"),
    "cut_mean_quality": Param(int, None, min=1, max=60, help="mean quality required by the cutting windows"),
    "poly_g": Param(str, "auto", choices=("auto", "on", "off"), help="short reads: poly-G tail trimming"),
    "adapter_r1": Param(str, None, help="short reads: adapter of read 1 (sequence, auto or none)"),
    "adapter_r2": Param(str, None, help="short reads: adapter of read 2 (sequence, auto or none)"),
    "low_quality_mode": Param(
        str, None, choices=("split", "longest"), help="long reads: handling of low-quality stretches"
    ),
    "extra_args": Param(list, [], help="additional simpletrim arguments"),
}

RESERVED = (
    "--r1", "--r2", "--o1", "--o2", "-i", "--input", "-o", "--output", "--report", "--json",
    "--force", "-w", "--threads", "--orphan1", "--orphan2", "--failed-output",
)


def is_skipped(params: dict) -> bool:
    return bool(params["skip"])


def build_flags(params: dict, long: bool) -> list[str]:
    flags: list[str] = []
    if params["min_length"] is not None:
        flags += ["--min-length", str(params["min_length"])]
    if params["cut_front"]:
        flags.append("--cut-front")
    if params["cut_tail"]:
        flags.append("--cut-tail")
    if params["cut_mean_quality"] is not None:
        flags += ["--cut-mean-quality", str(params["cut_mean_quality"])]
    if long:
        if params["low_quality_mode"]:
            flags += ["--low-quality-mode", params["low_quality_mode"]]
    else:
        if params["poly_g"] == "on":
            flags.append("--trim-poly-g")
        elif params["poly_g"] == "off":
            flags.append("--disable-trim-poly-g")
        for key, opt in (("adapter_r1", "--adapter-r1"), ("adapter_r2", "--adapter-r2")):
            if params[key]:
                flags += [opt, params[key]]
    return flags


def run(ctx: StageContext) -> dict:
    p = ctx.params
    long = is_long(ctx.platform)
    reads = raw_reads(ctx)
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
        if analysis["state"] == "done" and summary.get("suggestion_mode") == ("long" if long else "short"):
            for s in summary.get("suggestions", []):
                flags, added = merge_flags(flags, [str(x) for x in s.get("flags", [])])
                applied += added
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
        "outputs": outputs,
        "reads_in": rep.get("records_in"),
        "reads_out": rep.get("records_written"),
        "applied_suggestions": applied,
        "flags": flags,
        "report": rep,
    }
