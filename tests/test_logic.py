"""Milestone 1.2: Python logic on synthetic data."""

import gzip
import random
import tempfile
import unittest
from pathlib import Path

from readscreen.logic import coverage, fastx, pairs, ranking
from readscreen.logic.fingerprint import file_record, fingerprint
from readscreen.logic.ranking import Hsp, RefInfo

from .helpers import write_fasta, write_fastq_gz


class TmpCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()


class FingerprintTest(TmpCase):
    def test_same_content_same_print(self):
        data = random.Random(1).randbytes(3_000_000)
        a, b = self.tmp / "a.bin", self.tmp / "b.bin"
        a.write_bytes(data)
        b.write_bytes(data)
        self.assertEqual(fingerprint(a), fingerprint(b))
        b.write_bytes(data[:-1] + b"x")
        self.assertNotEqual(fingerprint(a), fingerprint(b))
        rec = file_record(a)
        self.assertEqual(rec["size"], 3_000_000)
        with self.assertRaises(FileNotFoundError):
            file_record(self.tmp / "missing")


class PairsTest(TmpCase):
    def test_find_mate(self):
        cases = [
            ("s_R1_001.fastq.gz", "s_R2_001.fastq.gz"),
            ("s_R1.fq.gz", "s_R2.fq.gz"),
            ("sample_1.fastq", "sample_2.fastq"),
            ("x.R1.fastq.gz", "x.R2.fastq.gz"),
            ("R1_lib_R1_trimmed.fastq.gz", "R1_lib_R2_trimmed.fastq.gz"),
        ]
        for r1, r2 in cases:
            with self.subTest(r1=r1):
                (self.tmp / r1).write_text("x")
                (self.tmp / r2).write_text("x")
                self.assertEqual(pairs.find_mate(self.tmp / r1), self.tmp / r2)
                self.assertEqual(pairs.find_mate(self.tmp / r2), self.tmp / r1)
                self.assertEqual(pairs.mate_role(self.tmp / r1), "r1")
        (self.tmp / "lonely_R1.fastq").write_text("x")
        self.assertIsNone(pairs.find_mate(self.tmp / "lonely_R1.fastq"))
        self.assertIsNone(pairs.mate_role("reads.fastq.gz"))


class FastxTest(TmpCase):
    def test_fastq_to_fasta_paired_subsample(self):
        r1 = write_fastq_gz(self.tmp / "a_R1.fastq.gz", ["ACGTACGT" for _ in range(10)])
        r2 = write_fastq_gz(self.tmp / "a_R2.fastq.gz", ["TTTT" for _ in range(10)], mate=2)
        out = self.tmp / "q.fa"
        info = fastx.fastq_to_fasta([r1, r2], out, max_reads=4)
        self.assertEqual(info, {"total": 10, "used": 4, "step": 3, "paired": True})
        names = [l[1:] for l in out.read_text().splitlines() if l.startswith(">")]
        self.assertEqual(names, ["sim_0/1", "sim_0/2", "sim_3/1", "sim_3/2", "sim_6/1", "sim_6/2", "sim_9/1", "sim_9/2"])
        info = fastx.fastq_to_fasta([r1], out, max_reads=None)
        self.assertEqual((info["used"], info["step"]), (10, 1))
        self.assertEqual(fastx.count_fastq(r1), 10)

    def test_fasta_stats_and_depths(self):
        fa = write_fasta(self.tmp / "c.fa", [("k31_0 flag=1 multi=8.5 len=300", "A" * 300),
                                              ("NODE_2_length_100_cov_3.25", "C" * 100),
                                              ("plain", "G" * 50)])
        self.assertEqual(fastx.fasta_stats(fa), {"sequences": 3, "bases": 450, "longest": 300, "n50": 300})
        self.assertEqual(fastx.contig_depths(fa), {"k31_0": 8.5, "NODE_2_length_100_cov_3.25": 3.25})
        self.assertTrue(fastx.looks_like_fasta(fa))
        gz = self.tmp / "c.fa.gz"
        with gzip.open(gz, "wb") as fh:
            fh.write(fa.read_bytes())
        self.assertEqual(fastx.fasta_stats(gz)["sequences"], 3)


def H(bits, qs, qe, ss, se, pid=90.0):
    return Hsp(float(bits), qs, qe, ss, se, pid, qe - qs + 1)


class RankingTest(unittest.TestCase):
    def info(self, **kw):
        base = {"refA": RefInfo(10000, "1", "Virus A", "A"), "refA2": RefInfo(10000, "2", "Virus A2", "A2"),
                "refB": RefInfo(10000, "3", "Virus B", "B"), "phage": RefInfo(80000, "4", "Phage", "P")}
        base.update(kw)
        return base

    def test_repeat_counted_once(self):
        # one contig aligning 10x to the same reference spot must not beat three contigs
        hsps = {("rep", "phage"): [H(250, 1, 270, 26000 + i * 5, 26270 + i * 5) for i in range(10)],
                ("c1", "refA"): [H(500, 1, 300, 1, 300)], ("c2", "refA"): [H(500, 1, 300, 1001, 1300)],
                ("c3", "refA"): [H(500, 1, 300, 3001, 3300)]}
        rows = ranking.rank(hsps, self.info())
        self.assertEqual(rows[0]["ref"], "refA")
        phage = next(r for r in rows if r["ref"] == "phage")
        self.assertLess(phage["score"], 400)
        self.assertTrue(phage["weak"])

    def test_tiled_overlap(self):
        score, cov = ranking.tiled_score([H(100, 1, 100, 1, 100), H(50, 1, 100, 51, 150)])
        self.assertEqual(cov, 150)
        self.assertAlmostEqual(score, 100 + 50 * 0.5)

    def test_top_pct_and_grouping(self):
        hsps = {("c1", "refA"): [H(1000, 1, 600, 1, 600)], ("c1", "refA2"): [H(950, 1, 600, 1, 600)],
                ("c1", "refB"): [H(400, 1, 300, 1, 300)],
                ("c2", "refB"): [H(800, 1, 500, 2001, 2500)]}
        rows = ranking.rank(hsps, self.info())
        by = {r["ref"]: r for r in rows}
        # refB is not supported by c1 (more than 10% below c1's best), only by c2
        self.assertEqual(by["refB"]["queries"], 1)
        # refA2 shares all its score with refA -> same group, not a leader
        self.assertEqual(by["refA2"]["group"], by["refA"]["group"])
        self.assertFalse(by["refA2"]["leader"])
        self.assertTrue(by["refB"]["leader"])
        self.assertNotEqual(by["refB"]["group"], by["refA"]["group"])

    def test_same_taxid_grouped_but_not_missing_taxid(self):
        info = self.info(segS=RefInfo(3000, "99", "Flu", "S"), segL=RefInfo(3000, "99", "Flu", "L"),
                         x=RefInfo(3000, None, None, "x"), y=RefInfo(3000, None, None, "y"))
        hsps = {("c1", "segS"): [H(900, 1, 500, 1, 500)], ("c2", "segL"): [H(800, 1, 500, 1, 500)],
                ("c3", "x"): [H(700, 1, 500, 1, 500)], ("c4", "y"): [H(600, 1, 500, 1, 500)]}
        by = {r["ref"]: r for r in ranking.rank(hsps, info)}
        self.assertEqual(by["segS"]["group"], by["segL"]["group"])
        self.assertNotEqual(by["x"]["group"], by["y"]["group"])

    def test_auto_select(self):
        rows = [{"ref": f"g{g}m{m}", "group": g, "leader": m == 0, "score": 1000 / g - m} for g in (1, 2, 3)
                for m in range(4)]
        rows.append({"ref": "low", "group": 4, "leader": True, "score": 50})
        chosen = ranking.auto_select(rows, top_groups=2, alternatives=1, min_score=100)
        self.assertEqual(chosen, {"g1m0", "g1m1", "g2m0", "g2m1"})
        self.assertNotIn("low", ranking.auto_select(rows, top_groups=10, alternatives=0, min_score=100))

    def test_read_blast(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            p = Path(d) / "b.tsv"
            p.write_text("q1\tNC_1.1\t90.0\t100\t1\t100\t300\t5000\t200\t101\t1e-20\t150\t123;456\tVirus X\tVirus X genome\n"
                         "q1\tNC_2.1\t90.0\t30\t1\t30\t300\t5000\t1\t30\t1e-2\t40\tN/A\tN/A\tSomething\n")
            hsps, info = ranking.read_blast(p, min_aln_len=50)
            self.assertEqual(list(hsps), [("q1", "NC_1.1")])
            self.assertEqual(hsps["q1", "NC_1.1"][0].ss, 101)
            self.assertEqual(info["NC_1.1"].taxid, "123")


def paf(q, qlen, qs, qe, ref, rlen, rs, re_, nm, blk, mapq, cigar, tp="P"):
    return "\t".join(map(str, [q, qlen, qs, qe, "+", ref, rlen, rs, re_, nm, blk, mapq, f"tp:A:{tp}", f"cg:Z:{cigar}"])) + "\n"


class CoverageTest(TmpCase):
    def run_cov(self, lines, lengths, **params):
        p = self.tmp / "a.paf"
        p.write_text("".join(lines))
        return coverage.compute(p, lengths, coverage.CoverageParams(**params), depth_out=self.tmp / "d.tsv.gz")

    def test_cigar_depth(self):
        lines = [paf("r1", 100, 0, 100, "ref", 1000, 0, 100, 100, 100, 60, "100M"),
                 paf("r2", 100, 0, 100, "ref", 1000, 50, 160, 95, 110, 60, "50M10D50M"),
                 paf("r3", 100, 0, 100, "ref", 1000, 900, 1000, 90, 100, 0, "100M")]
        res = self.run_cov(lines, {"ref": 1000, "empty": 500}, bins=10)
        r = res["ref"]
        self.assertEqual(r["reads"], 3)
        self.assertEqual(r["ambiguous"], 1)
        self.assertEqual(r["max_depth"], 2)
        # covered: 0-160 minus the 10 bp deletion (100-110), plus 900-1000
        self.assertAlmostEqual(r["breadth"], (160 - 10 + 100) / 1000)
        self.assertAlmostEqual(r["mean_depth"], 300 / 1000)
        self.assertAlmostEqual(r["identity"], 285 / 310, places=4)
        self.assertEqual(r["bins"]["count"], 10)
        self.assertEqual(r["bins"]["max"][0], 2)
        self.assertEqual(r["bins"]["max"][5], 0)
        self.assertEqual(res["empty"]["reads"], 0)
        with gzip.open(self.tmp / "d.tsv.gz", "rt") as fh:
            self.assertEqual(sum(1 for _ in fh), 1 + 1000)  # header + only references with reads

    def test_filters_and_flags(self):
        lines = [paf(f"r{i}", 150, 0, 150, "ref", 10000, 100, 250, 130, 150, 0 if i < 8 else 60, "150M")
                 for i in range(12)]
        lines.append(paf("short", 150, 0, 30, "ref", 10000, 5000, 5030, 30, 30, 60, "30M"))
        lines.append(paf("supp", 150, 0, 150, "ref", 10000, 7000, 7150, 150, 150, 60, "150M", tp="S"))
        r = self.run_cov(lines, {"ref": 10000})["ref"]
        self.assertEqual(r["reads"], 12)  # 'short' filtered, supplementary not counted as a read
        self.assertEqual(r["ambiguous"], 8)
        self.assertEqual(set(r["flags"]), {"divergent", "uneven", "ambiguous"})
        r = self.run_cov(lines, {"ref": 10000}, min_mapq=1)["ref"]
        self.assertEqual(r["reads"], 4)
        self.assertEqual(r["ambiguous"], 8)
        r = self.run_cov(lines, {"ref": 10000}, min_aligned_fraction=None, min_aligned_length=100)["ref"]
        self.assertEqual(r["reads"], 12)


if __name__ == "__main__":
    unittest.main()
