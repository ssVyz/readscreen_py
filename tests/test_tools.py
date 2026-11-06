"""Milestone 1.3: the real binaries on tiny generated data, called the way the stages call them."""

import random
import unittest

from readscreen import runner
from readscreen.logic import coverage
from readscreen.logic.ranking import OUTFMT, read_blast
from readscreen.paths import Env
from readscreen.stages.mapping import SENSITIVITY
from readscreen.worker import Worker

from .helpers import (
    BLAST_DB, TempEnvCase, blast_db_available, fetch_reference, mutate, random_seq, simulate_pairs,
    tools_available, write_fasta, write_fastq_gz, write_pairs,
)


@unittest.skipUnless(tools_available(), "binaries for this platform are missing")
class ShortReadStagesTest(TempEnvCase):
    """input → analyze → trim → assemble with the real simpletrim and shortread_assembler."""

    def test_stages_up_to_assembly(self):
        rng = random.Random(7)
        genome = random_seq(6000, rng)
        r1, r2 = write_pairs(self.tmp, simulate_pairs(genome, 900, rng))
        trim = {  # every short-mode option, so simpletrim checks the flags readscreen builds
            "min_base_quality": 15, "max_low_quality_percent": 40, "min_mean_quality": 20, "min_length": 30,
            "max_length": 500, "cut_tail": True, "cut_window_size": 4, "cut_mean_quality": 20, "trim_front": 0,
            "trim_tail": 0, "adapter_preset": "truseq", "poly_g": "auto", "trim_poly_x": True,
        }
        job = self.api.create_job(platform="illumina", r1=str(r1), r2=str(r2),
                                  params={"blast": {"db": "unused"}, "trim": trim}, pauses=["assemble"])
        Worker(self.env, exit_when_empty=True, poll=0.1).run()
        job = self.api.get_job(job["id"])
        self.assertEqual(job["status"], "paused", job["error"])
        st = {s["name"]: s for s in job["stages"]}
        self.assertEqual(st["analyze"]["state"], "done")
        self.assertTrue(st["analyze"]["summary"]["paired"])
        self.assertEqual(st["trim"]["summary"]["reads_in"], 900)
        self.assertGreater(st["trim"]["summary"]["reads_out"], 850)
        self.assertEqual(st["trim"]["summary"]["mode"], "short")
        self.assertIn("--average-quality", st["trim"]["summary"]["flags"])
        analysis = self.api.get_analysis(job["id"])
        self.assertEqual(analysis["columns"], ["R1", "R2"])
        self.assertEqual(len(analysis["quality"]["series"]), 2)
        self.assertIn("Length min / median / max", self.api.stage_log(job["id"], "analyze"))
        asm = st["assemble"]["summary"]
        self.assertGreaterEqual(asm["sequences"], 1)
        self.assertGreater(asm["longest"], 2000)

    def test_long_read_fragmenting(self):
        rng = random.Random(8)
        genome = random_seq(8000, rng)
        reads = []
        for _ in range(150):
            n = rng.randint(2000, 4000)
            s = rng.randint(0, len(genome) - n)
            reads.append(mutate(genome[s:s + n], 0.995, rng))
        fq = write_fastq_gz(self.tmp / "hifi.fastq.gz", reads)
        trim = {  # every long-mode option, so simpletrim checks the flags readscreen builds
            "min_base_quality": 10, "min_mean_quality": 7, "min_length": 500, "max_length": 0, "cut_front": True,
            "cut_mean_quality": 17, "trim_front": 0, "trim_tail": 0, "start_adapter": "auto", "end_adapter": "none",
            "chimeric_reads": "discard", "low_quality_mode": "split", "low_quality_threshold": 7, "trim_poly_x": True,
        }
        job = self.api.create_job(platform="hifi", r1=str(fq), pauses=["assemble"],
                                  params={"blast": {"db": "unused"}, "trim": trim,
                                          "assemble": {"mode": "assemble", "fragment_overlap": 40}})
        Worker(self.env, exit_when_empty=True, poll=0.1).run()
        job = self.api.get_job(job["id"])
        self.assertEqual(job["status"], "paused", job["error"])
        st = {s["name"]: s for s in job["stages"]}
        self.assertEqual(st["trim"]["summary"]["mode"], "long")
        self.assertEqual(st["trim"]["summary"]["reads_in"], 150)
        self.assertGreater(st["trim"]["summary"]["reads_out"], 100)
        self.assertEqual(self.api.get_analysis(job["id"])["mode"], "long")
        self.assertIn("--fragment-overlap 40", self.api.stage_log(job["id"], "assemble"))
        self.assertGreaterEqual(st["assemble"]["summary"]["sequences"], 1)
        self.assertFalse(any((self.env.jobs_dir).rglob("fragments.fastq.gz")))


@unittest.skipUnless(tools_available(), "binaries for this platform are missing")
class Minimap2Test(TempEnvCase):
    def test_sensitive_mapping_and_coverage(self):
        rng = random.Random(3)
        genome = random_seq(5000, rng)
        ref = write_fasta(self.tmp / "ref.fa", [("ref1 test", genome)])
        sample = mutate(genome, 0.95, rng)
        r1, r2 = write_pairs(self.tmp, simulate_pairs(sample, 400, rng))
        paf = self.tmp / "a.paf"
        runner.run([Env.default().tool("minimap2"), "-x", "sr", *SENSITIVITY["sensitive"], "-c", "--secondary=no",
                    "-t", 2, "-o", paf, ref, r1, r2], log_path=self.tmp / "mm2.log")
        self.assertIn("cg:Z:", paf.read_text())
        res = coverage.compute(paf, {"ref1": 5000})["ref1"]
        self.assertGreater(res["reads"], 600)
        self.assertGreater(res["breadth"], 0.95)
        self.assertGreater(res["identity"], 0.9)
        self.assertNotIn("divergent", res["flags"])  # ~95% identity is above the 90% threshold


@unittest.skipUnless(blast_db_available(), "bundled BLAST database missing")
class BlastTest(TempEnvCase):
    def test_list_dbs(self):
        dbs = self.api.list_dbs()
        self.assertIn("ref_viruses_rep_genomes/ref_viruses_rep_genomes", [d["name"] for d in dbs])

    def test_blastn_outfmt_parses(self):
        seq = fetch_reference("NC_001461.1")
        self.assertGreater(len(seq), 12000)
        q = write_fasta(self.tmp / "q.fa", [("query1", mutate(seq[1000:3000], 0.97, random.Random(1)))])
        out = self.tmp / "b.tsv"
        runner.run([Env.default().tool("blastn"), "-task", "dc-megablast", "-query", q, "-db", BLAST_DB,
                    "-evalue", "1e-10", "-max_target_seqs", 20, "-num_threads", 2, "-outfmt", OUTFMT, "-out", out],
                   log_path=self.tmp / "blast.log", env={"BLASTDB": str(BLAST_DB.parent)})
        hsps, info = read_blast(out)
        self.assertIn("NC_001461.1", info)
        self.assertIsNotNone(info["NC_001461.1"].taxid)
        self.assertIsNotNone(info["NC_001461.1"].species)  # taxdb found through BLASTDB


if __name__ == "__main__":
    unittest.main()
