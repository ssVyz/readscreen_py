"""Stage 8: extract the selected reference sequences with blastdbcmd."""

from __future__ import annotations

from ..logic.fastx import iter_fasta
from ..params import Schema
from .common import StageContext, StageError, describe_db, read_db_metadata, resolve_db

PARAMS: Schema = {}


def run(ctx: StageContext) -> dict:
    selected = ctx.stage_dir("select") / "selected.txt"
    refs = [line.strip() for line in selected.read_text(encoding="utf-8").splitlines() if line.strip()]
    out = ctx.out_dir / "refs.fa"
    if not refs:
        out.write_text("")
        ctx.log("no references selected")
        return {"references": 0, "bases": 0, "missing": []}
    blast = ctx.stage_row("blast")
    db = resolve_db(ctx.settings, blast["params"].get("db"))
    used = (blast["summary"].get("database") or {}).get("updated")
    now = describe_db(read_db_metadata(ctx.tool("blastdbcmd"), db))["updated"]
    changed = bool(used and now and used != now)
    if changed:
        # accession.version identifiers do not change their sequence, so the extraction stays valid;
        # references removed from the new version are reported as missing
        ctx.log(f"note: the database was updated after the blast stage ({used} -> {now})")
    batch = ctx.out_dir / "selected.txt"
    batch.write_text("".join(r + "\n" for r in refs), encoding="utf-8")
    ctx.run_tool(
        [ctx.tool("blastdbcmd"), "-db", db, "-entry_batch", batch, "-out", out],
        env={"BLASTDB": str(db.parent)},
    )
    got = {cid: n for cid, _, n in iter_fasta(out)}
    if not got:
        raise StageError("blastdbcmd returned no sequences for the selected references")
    missing = [r for r in refs if r not in got and r.split(".")[0] not in {g.split(".")[0] for g in got}]
    summary = {"references": len(got), "bases": sum(got.values()), "missing": missing}
    if changed:
        summary["database_changed"] = {"blast": used, "extract": now}
    return summary
