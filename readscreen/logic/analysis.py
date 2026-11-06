"""Read statistics from `simpletrim analyze --json`, prepared for display.

`overview()` turns the report into table rows (one column per read file), extra rows (read
pairs, long-read adapters) and quality-plot series. The web page, the CLI and the stage log all
use it, so they show the same numbers. The text is ASCII only (Windows consoles).
"""

from __future__ import annotations

from typing import Any


def _int(v: Any) -> str:
    return "-" if v is None else f"{round(v):,}"


def _num(v: Any, d: int = 1) -> str:
    return "-" if v is None else f"{v:,.{d}f}"


def _pct(f: Any, d: int = 1) -> str:
    if f is None:
        return "-"
    if 0 < f < 0.5 * 10 ** -(d + 2):
        return f"<{10 ** -d:.{d}f}%"
    return f"{100 * f:.{d}f}%"


def _q(v: Any) -> str:
    return "-" if v is None else f"Q{v:.1f}"


def _bases(n: Any) -> str:
    if n is None:
        return "-"
    for unit, size in (("Gb", 1e9), ("Mb", 1e6), ("kb", 1e3)):
        if n >= size:
            return f"{n / size:.1f} {unit}"
    return f"{n} bp"


def _seq(s: str | None, n: int = 28) -> str:
    if not s:
        return "none found"
    return s if len(s) <= n else s[:n] + "..."


def _platform(report: dict) -> str:
    p = report.get("platform") or {}
    text = p.get("name") or "unknown"
    if p.get("instrument_model") and p["instrument_model"] not in text:
        text += f" ({p['instrument_model']})"
    run = p.get("run") or {}
    for key, label in (("basecall_model", "basecalling"), ("flow_cell", "flow cell"), ("barcode", "barcode")):
        if run.get(key):
            text += f"; {label} {run[key]}"
    return text


def _inspected(report: dict) -> str:
    s = report.get("sample") or {}
    text = f"{_int(s.get('records'))} reads"
    if s.get("whole_file"):
        return text + " (the whole file)"
    if s.get("estimated_total_records"):
        text += f" of about {_int(s['estimated_total_records'])}"
    return text


def is_long_report(report: dict) -> bool:
    reads = report.get("reads") or {}
    return report.get("mode") == "long" or ("r1" not in reads and "length" in reads)


def _short(report: dict) -> dict:
    reads = report.get("reads") or {}
    names = [k for k in ("r1", "r2") if reads.get(k)]
    cols = [reads[k] for k in names]

    def row(label: str, fn) -> dict:
        return {"label": label, "values": [fn(r) for r in cols]}

    def first_below(r: dict) -> str:
        q = r.get("quality") or {}
        a, b = q.get("first_cycle_below_q30"), q.get("first_cycle_below_q20")
        return f"{a if a is not None else 'none'} / {b if b is not None else 'none'}"

    rows = [
        row("Reads", lambda r: _int(r.get("reads"))),
        row("Bases", lambda r: _bases(r.get("bases"))),
        row("Length min / median / max", lambda r: " / ".join(
            _int((r.get("length") or {}).get(k)) for k in ("min", "median", "max"))),
        row("Mean length", lambda r: _num((r.get("length") or {}).get("mean"))),
        row("Mean quality", lambda r: _q((r.get("quality") or {}).get("mean"))),
        row("Bases >= Q20 / >= Q30", lambda r: " / ".join(
            _pct((r.get("quality") or {}).get(k)) for k in ("q20_rate", "q30_rate"))),
        row("First cycle below Q30 / Q20", first_below),
        row("GC content", lambda r: _pct(r.get("gc_content"))),
        row("N bases", lambda r: _pct(r.get("n_rate"), 2)),
        row("Poly-G tails", lambda r: _pct((r.get("poly_g") or {}).get("fraction"), 2)),
        row("Adapter", lambda r: _seq((r.get("adapter") or {}).get("detected"))),
    ]
    extra = []
    if report.get("paired"):
        ov = report.get("overlap") or {}
        ins = ov.get("insert_size") or {}
        if ins.get("median") is not None:
            extra.append({"label": "Insert size", "value": f"median {_int(ins['median'])} bp"
                          f" (10-90%: {_int(ins.get('p10'))}-{_int(ins.get('p90'))} bp)"})
        extra.append({"label": "Overlapping pairs", "value": _pct(ov.get("overlapping_fraction"))})
        extra.append({"label": "Adapter read-through", "value": _pct(ov.get("adapter_read_through_fraction"), 2)})
        mism = (report.get("pairing") or {}).get("name_mismatches")
        if mism is not None:
            extra.append({"label": "Read name mismatches", "value": _int(mism)})
    series = []
    for name, r in zip(names, cols):
        y = (r.get("quality") or {}).get("per_cycle_mean") or []
        if y:
            series.append({"name": name.upper(), "x": list(range(1, len(y) + 1)), "y": y})
    quality = ({"x_label": "cycle (position in the read)", "x_name": "cycle", "x_bin": None,
                "y_label": "mean quality", "series": series} if series else None)
    return {"columns": [n.upper() for n in names] if len(names) > 1 else ["Reads"], "rows": rows,
            "extra": extra, "quality": quality, "footnote": None}


def _long(report: dict) -> dict:
    r = report.get("reads") or {}
    length = r.get("length") or {}
    total = r.get("reads") or 0
    yields = {y.get("min_length"): y.get("fraction") for y in r.get("yield_by_min_length") or []}
    levels = r.get("reads_with_mean_quality_at_least") or {}
    median_q = r.get("median_read_quality")
    rows = [
        ("Reads", _int(total)),
        ("Bases", _bases(r.get("bases"))),
        ("Length min / median / max", " / ".join(_int(length.get(k)) for k in ("min", "median", "max"))),
        ("Mean length / N50 / N90", " / ".join(_int(length.get(k)) for k in ("mean", "n50", "n90"))),
        ("Bases in reads >= 1 / 5 / 10 / 20 kb", " / ".join(_pct(yields.get(k)) for k in (1000, 5000, 10000, 20000))),
        ("Mean quality", _q(r.get("mean_quality"))),
        ("Median read quality", "-" if median_q is None else f"Q{median_q}"),
        ("Reads >= Q7 / Q10 / Q15 / Q20", " / ".join(
            _pct(levels[f"q{k}"] / total) if total and f"q{k}" in levels else "-" for k in (7, 10, 15, 20))),
        ("Bases >= Q20 / >= Q30", f"{_pct(r.get('q20_rate'))} / {_pct(r.get('q30_rate'))}"),
        ("GC content", _pct(r.get("gc_content"))),
        ("Poly-X tails", _pct((r.get("poly_x_tails") or {}).get("fraction"), 2)),
        ("Low-quality stretches inside reads", _pct((r.get("internal_low_quality") or {}).get("fraction"), 2)),
    ]
    ad = report.get("adapters") or {}
    extra = []
    for key, label in (("start", "Start adapter"), ("end", "End adapter")):
        a = ad.get(key) or {}
        value = _seq(a.get("detected"))
        if a.get("detected") and a.get("reads_with_adapter_fraction") is not None:
            value += f" (in {_pct(a['reads_with_adapter_fraction'])} of reads)"
        extra.append({"label": label, "value": value})
    if ad.get("inside_reads_fraction") is not None:
        extra.append({"label": "Reads with an adapter inside", "value": _pct(ad["inside_reads_fraction"], 2)})
    prof = r.get("quality_profile") or {}
    size = prof.get("bin_size") or 1
    series = []
    for key, name in (("from_start", "from the read start"), ("from_end", "from the read end")):
        y = prof.get(key) or []
        if y:
            series.append({"name": name, "x": [i * size + size / 2 for i in range(len(y))], "y": y})
    quality = ({"x_label": f"bases from the read start or end (mean per {size} bases)", "x_name": "bases",
                "x_bin": size, "y_label": "mean quality", "series": series} if series else None)
    return {"columns": ["Reads"], "rows": [{"label": a, "values": [b]} for a, b in rows], "extra": extra,
            "quality": quality,
            "footnote": "Long-read qualities are error-probability means (as in simpletrim long)."}


def overview(report: dict) -> dict:
    out = _long(report) if is_long_report(report) else _short(report)
    return {"mode": "long" if is_long_report(report) else "short", "platform": _platform(report),
            "inspected": _inspected(report), **out}


def text_lines(ov: dict) -> list[str]:
    """The overview as plain text (stage log, CLI)."""
    lines = [f"Platform: {ov['platform']}", f"Inspected: {ov['inspected']}", ""]
    width = max(len(r["label"]) for r in ov["rows"]) + 2
    colw = max([len(c) for c in ov["columns"]] + [len(v) for r in ov["rows"] for v in r["values"]]) + 2
    if len(ov["columns"]) > 1:
        lines.append(" " * width + "".join(c.rjust(colw) for c in ov["columns"]))
    for r in ov["rows"]:
        lines.append(r["label"].ljust(width) + "".join(v.rjust(colw) for v in r["values"]))
    if ov["extra"]:
        lines.append("")
        w = max(len(e["label"]) for e in ov["extra"]) + 2
        lines += [e["label"].ljust(w) + e["value"] for e in ov["extra"]]
    if ov.get("footnote"):
        lines += ["", ov["footnote"]]
    return lines
