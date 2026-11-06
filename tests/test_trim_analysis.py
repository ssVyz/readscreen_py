"""Trim mode and options, parameter presentation fields, and the read-analysis overview."""

from __future__ import annotations

import json
import time

from readscreen import jobs, layout, store
from readscreen.api import ApiError
from readscreen.logic.analysis import overview, text_lines
from readscreen.params import ParamError, describe
from readscreen.stages import trim
from readscreen.worker import Worker

from .helpers import TempEnvCase, write_fastq_gz


def short_report() -> dict:
    def one(mean_q: float) -> dict:
        return {
            "reads": 1000, "bases": 150_000, "gc_content": 0.45, "n_rate": 0.0002,
            "length": {"min": 35, "median": 150, "max": 151, "mean": 149.2},
            "quality": {"mean": mean_q, "q20_rate": 0.95, "q30_rate": 0.9, "first_cycle_below_q30": 140,
                        "first_cycle_below_q20": None, "per_cycle_mean": [36.0, 37.5, None, 35.0]},
            "poly_g": {"fraction": 0.001}, "adapter": {"detected": "AGATCGGAAGAGCACACGTCTGAACTCCAGTCA"},
        }

    return {
        "status": "ok", "paired": True, "platform": {"name": "Illumina NovaSeq", "instrument_model": "NovaSeq 6000"},
        "sample": {"records": 1000, "whole_file": False, "estimated_total_records": 50_000},
        "reads": {"r1": one(36.2), "r2": one(34.9)},
        "overlap": {"insert_size": {"median": 210, "p10": 150, "p90": 320}, "overlapping_fraction": 0.6,
                    "adapter_read_through_fraction": 0.012},
        "pairing": {"name_mismatches": 0},
    }


def long_report() -> dict:
    return {
        "status": "ok", "mode": "long",
        "platform": {"name": "Oxford Nanopore", "run": {"basecall_model": "hac", "flow_cell": "FC1"}},
        "sample": {"records": 400, "whole_file": True},
        "reads": {
            "reads": 400, "bases": 1_200_000, "gc_content": 0.5,
            "length": {"min": 100, "median": 2500, "max": 12000, "mean": 3000.4, "n50": 4100, "n90": 1200},
            "yield_by_min_length": [{"min_length": 1000, "fraction": 0.9}, {"min_length": 5000, "fraction": 0.3},
                                    {"min_length": 10000, "fraction": 0.05}, {"min_length": 20000, "fraction": 0.0}],
            "mean_quality": 12.3, "median_read_quality": 13,
            "reads_with_mean_quality_at_least": {"q7": 390, "q10": 300, "q15": 40, "q20": 0},
            "q20_rate": 0.6, "q30_rate": 0.3, "poly_x_tails": {"fraction": 0.004},
            "internal_low_quality": {"fraction": 0.02},
            "quality_profile": {"bin_size": 25, "from_start": [6.0, 12.0, None], "from_end": [9.0, 11.0, 12.0]},
        },
        "adapters": {"start": {"detected": "TTTCTGTTGGTGCTGATATTGC", "reads_with_adapter_fraction": 0.8},
                     "end": {"detected": None}, "inside_reads_fraction": 0.01},
    }


class TrimFlagsTest(TempEnvCase):
    def params(self, **kw) -> dict:
        return {**{k: p.default_for(None) for k, p in trim.PARAMS.items()}, **kw}

    def test_defaults_pass_nothing(self):
        self.assertEqual(trim.build_flags(self.params(), long=False), [])
        self.assertEqual(trim.build_flags(self.params(), long=True), [])

    def test_short_options(self):
        flags = trim.build_flags(self.params(
            min_base_quality=20, max_low_quality_percent=30, min_mean_quality=25, min_length=50, max_length=300,
            cut_front=True, cut_right=True, cut_window_size=5, cut_mean_quality=22, trim_front=3, trim_tail=2,
            adapter_preset="nextera", adapter_r1="none", poly_g="off", trim_poly_x=True, quality_filter=False,
            start_adapter="ACGT", low_quality_mode="split",  # long-read options: ignored in short mode
        ), long=False)
        text = " ".join(flags)
        for part in ("--disable-quality-filter", "--qualified-quality 20", "--unqualified-percent-limit 30",
                     "--average-quality 25", "--min-length 50", "--max-length 300", "--cut-front", "--cut-right",
                     "--cut-window-size 5", "--cut-mean-quality 22", "--trim-front1 3", "--trim-tail1 2",
                     "--adapter-preset nextera", "--adapter-r1 none", "--disable-trim-poly-g", "--trim-poly-x"):
            self.assertIn(part, text)
        self.assertNotIn("--start-adapter", text)
        self.assertNotIn("--low-quality-mode", text)

    def test_long_options(self):
        flags = " ".join(trim.build_flags(self.params(
            min_mean_quality=9, trim_front=10, start_adapter="ACGTACGT", end_adapter="none",
            chimeric_reads="discard", low_quality_mode="split", low_quality_threshold=8,
            cut_right=True, poly_g="on", adapter_r1="ACGT",  # short-read options: ignored in long mode
        ), long=True))
        for part in ("--average-quality 9", "--trim-front 10", "--start-adapter ACGTACGT", "--end-adapter none",
                     "--discard-chimeric-reads", "--low-quality-mode split", "--low-quality-threshold 8"):
            self.assertIn(part, flags)
        for part in ("--cut-right", "--trim-poly-g", "--adapter-r1", "--trim-front1"):
            self.assertNotIn(part, flags)
        keep = " ".join(trim.build_flags(self.params(chimeric_reads="keep"), long=True))
        self.assertIn("--disable-chimera-splitting", keep)

    def test_adapter_trimming_off_drops_adapter_options(self):
        flags = trim.build_flags(self.params(adapter_trimming=False, start_adapter="ACGT", chimeric_reads="discard"),
                                 long=True)
        self.assertEqual(flags, ["--disable-adapter-trimming"])
        self.assertEqual(trim._without_adapter_options(["--start-adapter", "ACGT", "--end-adapter", "none",
                                                        "--cut-front"]), ["--cut-front"])

    def test_resolve_mode(self):
        self.assertEqual(trim.resolve_mode({"mode": "auto"}, "ont"), "long")
        self.assertEqual(trim.resolve_mode({"mode": "auto"}, "illumina"), "short")
        self.assertEqual(trim.resolve_mode({"mode": "short"}, "hifi"), "short")
        self.assertEqual(trim.resolve_mode({}, "illumina"), "short")  # jobs from before the mode parameter

    def test_describe_has_presentation_fields(self):
        info = {p["name"]: p for p in describe(trim.PARAMS, "illumina")}
        self.assertEqual(info["min_length"]["tool_default"], {"short": 15, "long": 20})
        self.assertEqual(info["start_adapter"]["only"], "long")
        self.assertEqual(info["min_base_quality"]["group"], "Quality filter")


class TrimJobTest(TempEnvCase):
    def setUp(self):
        super().setUp()
        self.r1 = write_fastq_gz(self.tmp / "s_R1.fastq.gz", ["ACGT" * 30] * 5)
        self.r2 = write_fastq_gz(self.tmp / "s_R2.fastq.gz", ["TGCA" * 30] * 5, mate=2)

    def create(self, **params):
        return self.api.create_job(platform="illumina", r1=str(self.r1), r2=str(self.r2), queue=False,
                                   params={"blast": {"db": "dummy"}, **params})

    def test_long_mode_needs_single_reads(self):
        with self.assertRaisesRegex(ApiError, "takes one read file"):
            self.create(trim={"mode": "long"})
        job = self.create(trim={"mode": "short", "min_mean_quality": 20})
        with self.assertRaisesRegex(ApiError, "takes one read file"):
            self.api.set_params(job["id"], "trim", {"mode": "long"})
        single = self.api.create_job(platform="illumina", r1=str(self.r1), queue=False,
                                     params={"blast": {"db": "dummy"}, "trim": {"mode": "long"}})
        trim_params = next(s for s in single["stages"] if s["name"] == "trim")["params"]
        self.assertEqual(trim_params["mode"], "long")

    def test_fragment_overlap_checked(self):
        with self.assertRaisesRegex(ApiError, "fragment_overlap"):
            self.create(assemble={"fragment_length": 100, "fragment_overlap": 100})

    def strip_new_params(self, job_id: int, stage: str, keep: tuple[str, ...]) -> None:
        conn = store.connect(self.env.db_path)
        try:
            row = conn.execute("SELECT params FROM stages WHERE job_id = ? AND name = ?", (job_id, stage)).fetchone()
            old = {k: v for k, v in store.loads(row["params"]).items() if k in keep}
            conn.execute("UPDATE stages SET params = ?, state = 'done' WHERE job_id = ? AND name = ?",
                         (store.dumps(old), job_id, stage))
        finally:
            conn.close()

    def test_jobs_from_older_versions(self):
        job = self.create()
        old_keys = ("skip", "apply_suggestions", "min_length", "cut_front", "cut_tail", "cut_mean_quality",
                    "poly_g", "adapter_r1", "adapter_r2", "low_quality_mode", "extra_args")
        self.strip_new_params(job["id"], "trim", old_keys)
        # the same value again is no change, although the stored params lack the new keys
        self.assertIsNotNone(self.api.set_params(job["id"], "trim", {"skip": False}))
        with self.assertRaisesRegex(ApiError, "reset"):
            self.api.set_params(job["id"], "trim", {"min_mean_quality": 10})

    def test_worker_fills_missing_params(self):
        job = self.create()
        conn = store.connect(self.env.db_path)
        try:
            row = conn.execute("SELECT params FROM stages WHERE job_id = ? AND name = 'trim'", (job["id"],)).fetchone()
            old = {k: v for k, v in store.loads(row["params"]).items() if k in ("skip", "apply_suggestions")}
            conn.execute("UPDATE stages SET params = ? WHERE job_id = ? AND name = 'trim'", (store.dumps(old), job["id"]))
        finally:
            conn.close()
        seen = {}

        class Fake:
            PARAMS = trim.PARAMS

            @staticmethod
            def run(ctx):
                seen.update(ctx.params)
                return {}

        class Noop:
            PARAMS = {}

            @staticmethod
            def run(ctx):
                return {}

        stages = {name: (Fake if name == "trim" else Noop) for name in layout.STAGE_NAMES}
        self.api.queue_job(job["id"])
        Worker(self.env, exit_when_empty=True, poll=0.1, stages=stages).run()
        self.assertEqual(seen["mode"], "auto")
        self.assertIsNone(seen["min_mean_quality"])
        self.assertTrue(seen["quality_filter"])


class AnalysisOverviewTest(TempEnvCase):
    def test_short_paired(self):
        ov = overview(short_report())
        self.assertEqual((ov["mode"], ov["columns"]), ("short", ["R1", "R2"]))
        rows = {r["label"]: r["values"] for r in ov["rows"]}
        self.assertEqual(rows["Length min / median / max"], ["35 / 150 / 151"] * 2)
        self.assertEqual(rows["Mean quality"], ["Q36.2", "Q34.9"])
        self.assertEqual(rows["First cycle below Q30 / Q20"], ["140 / none"] * 2)
        self.assertEqual(rows["N bases"], ["0.02%"] * 2)
        self.assertTrue(rows["Adapter"][0].startswith("AGATCGGAAGAGCACACGTCTGAACT"))
        extra = {e["label"]: e["value"] for e in ov["extra"]}
        self.assertEqual(extra["Insert size"], "median 210 bp (10-90%: 150-320 bp)")
        self.assertEqual(extra["Adapter read-through"], "1.20%")
        self.assertEqual([s["name"] for s in ov["quality"]["series"]], ["R1", "R2"])
        self.assertEqual(ov["quality"]["series"][0]["x"], [1, 2, 3, 4])
        self.assertIn("of about 50,000", ov["inspected"])
        self.assertIn("NovaSeq 6000", ov["platform"])
        text = "\n".join(text_lines(ov))
        self.assertIn("R1", text)
        text.encode("ascii")  # printable on any console

    def test_short_single(self):
        rep = short_report()
        rep["paired"] = False
        del rep["reads"]["r2"]
        ov = overview(rep)
        self.assertEqual((ov["columns"], ov["extra"]), (["Reads"], []))

    def test_long(self):
        ov = overview(long_report())
        self.assertEqual(ov["mode"], "long")
        rows = {r["label"]: r["values"][0] for r in ov["rows"]}
        self.assertEqual(rows["Mean length / N50 / N90"], "3,000 / 4,100 / 1,200")
        self.assertEqual(rows["Reads >= Q7 / Q10 / Q15 / Q20"], "97.5% / 75.0% / 10.0% / 0.0%")
        self.assertEqual(rows["Bases in reads >= 1 / 5 / 10 / 20 kb"], "90.0% / 30.0% / 5.0% / 0.0%")
        extra = {e["label"]: e["value"] for e in ov["extra"]}
        self.assertIn("(in 80.0% of reads)", extra["Start adapter"])
        self.assertEqual(extra["End adapter"], "none found")
        q = ov["quality"]
        self.assertEqual([s["name"] for s in q["series"]], ["from the read start", "from the read end"])
        self.assertEqual(q["series"][0]["x"], [12.5, 37.5, 62.5])
        self.assertIn("basecalling hac", ov["platform"])
        "\n".join(text_lines(ov)).encode("ascii")

    def test_api_reads_the_report_file(self):
        r1 = write_fastq_gz(self.tmp / "x.fastq.gz", ["ACGT" * 30] * 5)
        job = self.api.create_job(platform="ont", r1=str(r1), queue=False, params={"blast": {"db": "dummy"}})
        with self.assertRaises(ApiError) as ctx:
            self.api.get_analysis(job["id"])
        self.assertEqual(ctx.exception.code, "not_found")
        folder = layout.stage_dir(self.env, job["id"], "analyze")
        folder.mkdir(parents=True)
        (folder / "analyze.json").write_text(json.dumps(long_report()), encoding="utf-8")
        conn = store.connect(self.env.db_path)
        conn.execute("UPDATE stages SET state = 'done', finished = ?, summary = '{}' WHERE job_id = ? AND name = 'analyze'",
                     (time.time(), job["id"]))
        conn.close()
        self.assertEqual(self.api.get_analysis(job["id"])["mode"], "long")

    def test_unknown_param_still_rejected(self):
        with self.assertRaises(ParamError):
            jobs.build_params("illumina", {}, {"trim": {"no_such_option": 1}})
