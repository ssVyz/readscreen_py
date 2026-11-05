"""Stage 1: check that the input files are present and unchanged."""

from __future__ import annotations

from .. import layout
from ..params import Schema
from .common import StageContext, StageError, checked_input

PARAMS: Schema = {}


def run(ctx: StageContext) -> dict:
    files = []
    for role, row in sorted(ctx.inputs().items()):
        if role == "assembly":
            p = layout.imports_dir(ctx.env, ctx.job_id) / "assembly.fa"
            if not p.is_file():
                raise StageError(f"imported assembly missing: {p} (reassign the assembly)")
        else:
            p = checked_input(row)
        files.append({"role": role, "path": str(p), "size": row["size"]})
    if not any(f["role"] == "r1" for f in files):
        raise StageError("the job has no input reads")
    return {"files": files}
