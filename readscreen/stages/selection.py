"""Stage 7: final selection of references (automatic, or by the user on the website)."""

from __future__ import annotations

from ..params import Param, Schema
from .common import PauseRequest, StageContext

PARAMS: Schema = {
    "mode": Param(str, "auto", choices=("auto", "manual"),
                  help="auto: take the automatic selection; manual: pause for selection on the website"),
    "confirmed": Param(bool, False, internal=True),
}


def run(ctx: StageContext) -> dict:
    mode = ctx.params["mode"]
    if mode == "manual" and not ctx.params["confirmed"]:
        raise PauseRequest("select")
    if mode == "auto":
        ctx.conn.execute("UPDATE ranking SET selected = auto_selected WHERE job_id = ?", (ctx.job_id,))
    refs = [
        r["ref"]
        for r in ctx.conn.execute(
            "SELECT ref FROM ranking WHERE job_id = ? AND selected = 1 ORDER BY grp, leader DESC, score DESC",
            (ctx.job_id,),
        )
    ]
    (ctx.out_dir / "selected.txt").write_text("".join(r + "\n" for r in refs), encoding="utf-8")
    return {"mode": mode, "selected": len(refs), "refs": refs[:500]}
