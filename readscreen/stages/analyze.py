"""Stage 2: simpletrim analyze (input check and suggested trim settings)."""

from __future__ import annotations

import json

from ..layout import is_long
from ..logic.analysis import overview, text_lines
from ..params import Param, Schema
from .common import StageContext, StageError, raw_reads

PARAMS: Schema = {
    "skip": Param(bool, False, help="skip the analysis"),
    "max_reads": Param(int, 1_000_000, min=0, help="reads (pairs) to inspect from the start; 0 = all"),
}

_LONG_PLATFORMS = ("oxford_nanopore", "pac_bio")


def is_skipped(params: dict) -> bool:
    return bool(params["skip"])


def run(ctx: StageContext) -> dict:
    reads = raw_reads(ctx)
    cmd = [ctx.tool("simpletrim"), "analyze", "--json", "-w", ctx.threads, "--r1", reads.files[0]]
    if reads.paired:
        cmd += ["--r2", reads.files[1]]
    cmd += ["--max-reads", ctx.params["max_reads"]]
    out = ctx.out_dir / "analyze.json"
    ctx.run_tool(cmd, stdout=out)
    try:
        report = json.loads(out.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StageError(f"cannot read the simpletrim analyze report: {exc}") from None

    platform = report.get("platform") or {}
    detected = platform.get("platform", "unknown")
    warnings = list(report.get("warnings") or [])
    if detected not in ("unknown", None) and (detected in _LONG_PLATFORMS) != is_long(ctx.platform):
        warnings.append(
            f"readscreen: the read headers look like {platform.get('name', detected)}, "
            f"but the job platform is '{ctx.platform}'"
        )
    command = str(report.get("suggested_command", ""))
    mode = command.split()[1] if len(command.split()) > 1 else None
    try:
        lines = text_lines(overview(report))
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:  # an unexpected report layout
        lines = [f"(no readable summary: {exc!r})"]
    ctx.log("read analysis:\n  " + "\n  ".join(lines))
    for w in warnings:
        ctx.log(f"warning: {w}")
    for s in report.get("suggestions") or []:
        ctx.log(f"suggestion: {' '.join(map(str, s.get('flags', [])))} ({s.get('reason', '')})")
    return {
        "platform": platform,
        "paired": report.get("paired"),
        "sample": report.get("sample"),
        "warnings": warnings,
        "notes": report.get("notes") or [],
        "suggestions": report.get("suggestions") or [],
        "suggestion_mode": mode,
        "suggested_command": command,
    }
