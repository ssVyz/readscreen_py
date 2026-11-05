"""Command-line frontend. A thin layer over readscreen.api."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime

from . import __version__
from .api import Api, ApiError
from .layout import PLATFORMS, STAGE_NAMES
from .params import ParamError, parse_assignment
from .paths import Env
from .worker import AlreadyRunning, Worker, setup_logging


# ---------------------------------------------------------------------------- formatting helpers


def _ts(t: float | None) -> str:
    return datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M") if t else "-"


def _table(rows: list[list], headers: list[str]) -> str:
    cells = [[str(h) for h in headers]] + [["" if c is None else str(c) for c in r] for r in rows]
    widths = [max(len(r[i]) for r in cells) for i in range(len(headers))]
    lines = ["  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip() for r in cells]
    lines.insert(1, "  ".join("-" * w for w in widths))
    return "\n".join(lines)


def _pct(v: float | None) -> str:
    return "-" if v is None else f"{100 * v:.1f}%"


def _print_json(data) -> None:
    print(json.dumps(data, indent=1, default=str))


def _assignments(items: list[str]) -> dict[str, dict]:
    """['stage.param=value', ...] -> {stage: {param: value}}"""
    out: dict[str, dict] = {}
    for item in items or []:
        key, value = parse_assignment(item)
        if "." not in key:
            raise ParamError(f"expected stage.param=value, got {item!r}")
        stage, name = key.split(".", 1)
        out.setdefault(stage, {})[name] = value
    return out


# ---------------------------------------------------------------------------- commands


def cmd_dbs(api: Api, args) -> int:
    dbs = api.list_dbs()
    if not dbs:
        print("no BLAST databases found")
        return 0
    print(_table([[d["name"], d["title"], d["sequences"], d["bases"]] for d in dbs],
                 ["name", "title", "sequences", "bases"]))
    return 0


def cmd_stages(api: Api, args) -> int:
    for st in api.stage_info(args.platform):
        print(f"{st['index']:>2} {st['name']}")
        for p in st["params"]:
            choices = f" [{'|'.join(map(str, p['choices']))}]" if p["choices"] else ""
            print(f"     {p['name']} ({p['type']}, default {p['default']!r}){choices}: {p['help']}")
    return 0


def cmd_create(api: Api, args) -> int:
    params = _assignments(args.set)
    for stage in args.skip or []:
        if stage in ("analyze", "trim"):
            params.setdefault(stage, {})["skip"] = True
        elif stage == "assemble":
            params.setdefault("assemble", {})["mode"] = "skip"
        else:
            raise ApiError(f"--skip: only analyze, trim and assemble can be skipped (got {stage})")
    if args.db:
        params.setdefault("blast", {})["db"] = args.db
    if args.select_manual:
        params.setdefault("select", {})["mode"] = "manual"
    job = api.create_job(
        platform=args.platform, r1=args.r1, r2=args.r2, assembly=args.assembly, name=args.name,
        notes=args.notes or "", params=params, pauses=args.pause_after or [], queue=not args.no_queue,
    )
    print(f"created job {job['id']} '{job['name']}' ({job['status']})")
    return 0


def cmd_list(api: Api, args) -> int:
    jobs = api.list_jobs()
    if not jobs:
        print("no jobs")
        return 0
    rows = []
    for j in jobs:
        status = j["status"] + (f" ({j['pause_reason']})" if j["pause_reason"] else "")
        rows.append([j["id"], j["name"], status, j["current_stage"] or "-", j["platform"], _ts(j["created"]),
                     _ts(j["updated"])])
    print(_table(rows, ["id", "name", "status", "stage", "platform", "created", "updated"]))
    return 0


def cmd_show(api: Api, args) -> int:
    job = api.get_job(args.id)
    if args.json:
        _print_json(job)
        return 0
    print(f"job {job['id']}: {job['name']}")
    print(f"  status:   {job['status']}" + (f" ({job['pause_reason']})" if job["pause_reason"] else ""))
    if job["error"]:
        print(f"  error:    {job['error']}")
    print(f"  platform: {job['platform']}, {'paired' if job['paired'] else 'single'}")
    if job["notes"]:
        print(f"  notes:    {job['notes']}")
    for role, rec in job["inputs"].items():
        print(f"  {role}: {rec['path']}")
    rows = []
    for s in job["stages"]:
        took = f"{s['finished'] - s['started']:.0f}s" if s["started"] and s["finished"] else ""
        rows.append([s["idx"], s["name"], s["state"], "yes" if s["pause_after"] else "", took,
                     _brief_summary(s["name"], s["summary"])])
    print()
    print(_table(rows, ["#", "stage", "state", "pause", "time", "summary"]))
    if args.params:
        print()
        for s in job["stages"]:
            if s["params"]:
                shown = {k: v for k, v in s["params"].items() if k != "confirmed"}
                print(f"  {s['name']}: {json.dumps(shown)}")
    return 0


def _brief_summary(stage: str, s: dict | None) -> str:
    if not s:
        return ""
    if stage == "analyze":
        return f"{(s.get('platform') or {}).get('name', '?')}, {len(s.get('suggestions', []))} suggestion(s), {len(s.get('warnings', []))} warning(s)"
    if stage == "trim":
        return f"{s.get('reads_in')} -> {s.get('reads_out')} reads"
    if stage == "assemble":
        return f"{s.get('sequences')} contigs, {s.get('bases')} bp, N50 {s.get('n50')}"
    if stage == "blast":
        return f"{s.get('mode')}, {s.get('task')}, {s.get('queries')} queries, {s.get('hsps')} hits"
    if stage == "rank":
        return f"{s.get('references_supported')} refs, {s.get('groups')} groups, {s.get('auto_selected')} auto-selected"
    if stage == "select":
        return f"{s.get('selected')} selected ({s.get('mode')})"
    if stage == "extract":
        return f"{s.get('references')} references, {s.get('bases')} bp"
    if stage == "map":
        return f"{s.get('alignments')} alignments" + (f" ({s.get('preset')})" if s.get("preset") else "")
    if stage == "report":
        return f"{s.get('with_reads')} of {s.get('references')} references with reads"
    return ""


def cmd_set(api: Api, args) -> int:
    updates = dict(parse_assignment(a) for a in args.values)
    p = api.set_params(args.id, args.stage, updates, reset=args.reset)
    print(json.dumps({k: v for k, v in p.items() if k != "confirmed"}))
    return 0


def cmd_pause(api: Api, args) -> int:
    api.set_pause(args.id, args.stage, args.state == "on")
    return 0


def cmd_edit(api: Api, args) -> int:
    api.update_job(args.id, args.name, args.notes)
    return 0


def cmd_simple(action: str):
    def run(api: Api, args) -> int:
        result = getattr(api, action)(args.id)
        if isinstance(result, str):
            print(result)
        return 0

    return run


def cmd_reset(api: Api, args) -> int:
    api.reset_job(args.id, args.stage, queue=args.queue)
    return 0


def cmd_delete(api: Api, args) -> int:
    if not args.yes:
        print("deleting removes the job and its folder in jobs/; repeat with --yes", file=sys.stderr)
        return 1
    print(api.delete_job(args.id))
    return 0


def cmd_reassign(api: Api, args) -> int:
    res = api.reassign_inputs(args.id, args.r1, args.r2, args.assembly, force=args.force)
    if res["reset_to"]:
        print(f"inputs changed; job reset to '{res['reset_to']}'")
    else:
        print("same files, new location; results kept")
    return 0


def cmd_log(api: Api, args) -> int:
    print(api.stage_log(args.id, args.stage, args.lines))
    return 0


def cmd_ranking(api: Api, args) -> int:
    rows = api.get_ranking(args.id)
    if not args.all:
        rows = [r for r in rows if r["leader"] or r["selected"]]
    out = [[r["group"], "*" if r["leader"] else "", "x" if r["selected"] else "", r["ref"],
            (r["species"] or r["title"] or "")[:40], f"{r['score']:.0f}", r["queries"], _pct(r["ref_cov"]),
            f"{r['identity']:.1f}", "weak" if r["weak"] else ""] for r in rows]
    print(_table(out, ["grp", "lead", "sel", "reference", "name", "score", "queries", "refcov", "%id", "note"]))
    return 0


def cmd_results(api: Api, args) -> int:
    res = api.get_results(args.id)
    if not res["available"]:
        print("no results yet")
        return 0
    if args.references:
        rows = [[r["ref"], (r.get("species") or r.get("title") or "")[:40], r["reads"], r["ambiguous"],
                 _pct(r["breadth"]), f"{r['mean_depth']:.2f}", r["max_depth"],
                 _pct(r["identity"]), ",".join(r["flags"])] for r in res["references"]]
        print(_table(rows, ["reference", "name", "reads", "ambig", "breadth", "mean", "max", "identity", "flags"]))
        return 0
    rows = []
    for t in res["taxa"]:
        b = t["best"]
        rows.append([t["name"][:40], t["reads"], t["ambiguous"], b["ref"], _pct(b["breadth"]),
                     f"{b['mean_depth']:.2f}", b["max_depth"], _pct(b["identity"]), ",".join(b["flags"])])
    print(_table(rows, ["taxon", "reads", "ambig", "best reference", "breadth", "mean", "max", "identity", "flags"]))
    return 0


def cmd_export(api: Api, args) -> int:
    text = api.export_results(args.id, args.format)
    if args.output:
        with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)
    return 0


def cmd_worker(api: Api, args) -> int:
    if args.action == "run":
        setup_logging(api.env, console=True)
        try:
            return Worker(api.env, exit_when_empty=args.exit_when_empty).run()
        except AlreadyRunning as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            print("interrupted", file=sys.stderr)
            return 130
    if args.action == "start":
        pid = api.start_worker()
        print(f"worker started (pid {pid})")
        return 0
    if args.action == "stop":
        print(api.stop_worker(now=args.now, wait=args.wait))
        return 0
    st = api.worker_status()
    if not st["running"]:
        print("worker: not running")
        return 0
    age = f", heartbeat {time.time() - st['heartbeat']:.0f}s ago" if st["heartbeat"] else ""
    job = f", job {st['job_id']} stage {st['stage'] or '-'}" if st["job_id"] else ", idle"
    stop = f", stop requested ({st['stop_request']})" if st["stop_request"] else ""
    print(f"worker: running (pid {st['pid']}, since {_ts(st['started'])}{age}{job}{stop})")
    return 0


def cmd_serve(api: Api, args) -> int:
    from .web.server import serve

    return serve(api, args.host, args.port, open_browser=args.open)


def cmd_settings(api: Api, args) -> int:
    if args.action == "set":
        print(api.set_setting(args.key, args.value))
    elif args.action == "unset":
        print(api.set_setting(args.key, None))
    else:
        for k, v in sorted(api.get_settings().items()):
            print(f"{k} = {json.dumps(v)}")
    return 0


# ---------------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="readscreen", description="Metagenomic read screening pipeline")
    ap.add_argument("--version", action="version", version=f"readscreen {__version__}")
    ap.add_argument("--data-dir", help="folder for db/, jobs/ and logs/ (default: the project folder)")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("dbs", help="list the BLAST databases")
    p.set_defaults(func=cmd_dbs)
    p = sub.add_parser("stages", help="list the stages and their parameters")
    p.add_argument("--platform", choices=PLATFORMS, default="illumina")
    p.set_defaults(func=cmd_stages)

    jobs = sub.add_parser("jobs", help="create and manage jobs").add_subparsers(dest="jobs_cmd", required=True)
    p = jobs.add_parser("create", help="create a job (queued unless --no-queue)")
    p.add_argument("--r1", required=True, help="reads (read 1 of a pair, or the single read file)")
    p.add_argument("--r2", help="read 2 of a pair")
    p.add_argument("--platform", choices=PLATFORMS, default="illumina")
    p.add_argument("--name")
    p.add_argument("--notes")
    p.add_argument("--db", help="BLAST database (see 'readscreen dbs')")
    p.add_argument("--assembly", help="use this assembly (FASTA) instead of assembling")
    p.add_argument("--skip", action="append", choices=("analyze", "trim", "assemble"), help="skip a stage (repeatable)")
    p.add_argument("--pause-after", action="append", choices=STAGE_NAMES, help="pause after a stage (repeatable)")
    p.add_argument("--select-manual", action="store_true", help="pause for hit selection on the website")
    p.add_argument("--set", action="append", metavar="STAGE.PARAM=VALUE", help="set a stage parameter (repeatable)")
    p.add_argument("--no-queue", action="store_true", help="create the job stopped")
    p.set_defaults(func=cmd_create)

    p = jobs.add_parser("list", help="list all jobs")
    p.set_defaults(func=cmd_list)
    p = jobs.add_parser("show", help="show a job with its stages")
    p.add_argument("id", type=int)
    p.add_argument("--params", action="store_true", help="also show the stage parameters")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_show)
    p = jobs.add_parser("set", help="change stage parameters")
    p.add_argument("id", type=int)
    p.add_argument("stage", choices=STAGE_NAMES)
    p.add_argument("values", nargs="+", metavar="PARAM=VALUE")
    p.add_argument("--reset", action="store_true", help="allow resetting a finished stage")
    p.set_defaults(func=cmd_set)
    p = jobs.add_parser("pause", help="set or clear a pause after a stage")
    p.add_argument("id", type=int)
    p.add_argument("stage", choices=STAGE_NAMES)
    p.add_argument("state", choices=("on", "off"))
    p.set_defaults(func=cmd_pause)
    p = jobs.add_parser("edit", help="change name or notes")
    p.add_argument("id", type=int)
    p.add_argument("--name")
    p.add_argument("--notes")
    p.set_defaults(func=cmd_edit)
    for name, action, help_text in (
        ("queue", "queue_job", "queue a stopped or failed job"),
        ("continue", "continue_job", "continue a paused job (accepts the current hit selection)"),
        ("cancel", "cancel_job", "take a job out of the queue, or stop it while it runs"),
    ):
        p = jobs.add_parser(name, help=help_text)
        p.add_argument("id", type=int)
        p.set_defaults(func=cmd_simple(action))
    p = jobs.add_parser("reset", help="discard a stage and all later ones")
    p.add_argument("id", type=int)
    p.add_argument("stage", choices=STAGE_NAMES)
    p.add_argument("--queue", action="store_true", help="queue the job right away")
    p.set_defaults(func=cmd_reset)
    p = jobs.add_parser("reassign", help="point the job to moved or new input files")
    p.add_argument("id", type=int)
    p.add_argument("--r1")
    p.add_argument("--r2")
    p.add_argument("--assembly")
    p.add_argument("--force", action="store_true", help="accept different files (resets the job)")
    p.set_defaults(func=cmd_reassign)
    p = jobs.add_parser("delete", help="delete a job and its folder")
    p.add_argument("id", type=int)
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_delete)
    p = jobs.add_parser("log", help="show the log of a stage")
    p.add_argument("id", type=int)
    p.add_argument("stage", choices=STAGE_NAMES)
    p.add_argument("--lines", type=int, default=200)
    p.set_defaults(func=cmd_log)

    p = sub.add_parser("ranking", help="show the ranked BLAST hits of a job")
    p.add_argument("id", type=int)
    p.add_argument("--all", action="store_true", help="all references, not only group leaders and selected ones")
    p.set_defaults(func=cmd_ranking)
    p = sub.add_parser("results", help="show the results of a job")
    p.add_argument("id", type=int)
    p.add_argument("--references", action="store_true", help="per reference instead of per taxon")
    p.set_defaults(func=cmd_results)
    p = sub.add_parser("export", help="export the results of a job")
    p.add_argument("id", type=int)
    p.add_argument("--format", choices=("json", "tsv"), default="json")
    p.add_argument("-o", "--output")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("worker", help="control the worker")
    p.add_argument("action", choices=("start", "run", "stop", "status"))
    p.add_argument("--exit-when-empty", action="store_true", help="run: exit when the queue is empty")
    p.add_argument("--now", action="store_true", help="stop: kill the running tool instead of finishing the stage")
    p.add_argument("--wait", type=float, default=0.0, help="stop: wait up to this many seconds")
    p.set_defaults(func=cmd_worker)

    p = sub.add_parser("serve", help="start the web interface")
    p.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1)")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--open", action="store_true", help="open the browser")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("settings", help="show or change settings")
    p.add_argument("action", nargs="?", choices=("list", "set", "unset"), default="list")
    p.add_argument("key", nargs="?")
    p.add_argument("value", nargs="?")
    p.set_defaults(func=cmd_settings)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "settings" and args.action in ("set", "unset") and not args.key:
        print("error: settings set/unset need a key", file=sys.stderr)
        return 2
    try:
        api = Api(Env.default(args.data_dir))
        return args.func(api, args)
    except (ApiError, ParamError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
