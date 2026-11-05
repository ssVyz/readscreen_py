"""Milestone 1.6: all stages with the real tools on a synthetic sample (needs the bundled BLAST DB).

The sample: a BVDV-1 genome (NC_001461.1 from the DB) mutated to ~96% identity, plus random
"host" sequence that should not hit anything.
"""

import os
import random
import subprocess
import sys
import unittest

from readscreen.paths import ROOT
from readscreen.worker import Worker

from .helpers import (
    EXAMPLES, TempEnvCase, blast_db_available, fetch_reference, mutate, random_seq, simulate_pairs, write_pairs,
)

DB = "ref_viruses_rep_genomes/ref_viruses_rep_genomes"
BVDV1 = "NC_001461.1"


@unittest.skipUnless(blast_db_available(), "bundled BLAST database missing")
class SyntheticPipelineTest(TempEnvCase):
    @classmethod
    def setUpClass(cls):
        rng = random.Random(42)
        cls.virus = mutate(fetch_reference(BVDV1), 0.96, rng)
        host = random_seq(150_000, rng)
        cls.pairs = simulate_pairs(cls.virus, 2500, rng) + simulate_pairs(host, 2500, rng)
        rng.shuffle(cls.pairs)

    def run_job(self, **kw):
        r1, r2 = write_pairs(self.tmp, self.pairs)
        params = kw.pop("params", {})
        params.setdefault("blast", {})["db"] = DB
        job = self.api.create_job(platform="illumina", r1=str(r1), r2=str(r2), params=params, **kw)
        Worker(self.env, exit_when_empty=True, poll=0.1).run()
        return self.api.get_job(job["id"])

    def check_bvdv(self, job_id):
        ranking = self.api.get_ranking(job_id)
        self.assertTrue(ranking, "no BLAST hits ranked")
        group1 = [r for r in ranking if r["group"] == 1]
        self.assertIn("Bovine viral diarrhea virus 1", group1[0]["species"] or "")
        self.assertTrue(any(r["ref"] == BVDV1 and r["selected"] for r in ranking))
        res = self.api.get_results(job_id)
        self.assertTrue(res["available"])
        top = res["references"][0]
        self.assertEqual(top["ref"], BVDV1)
        self.assertGreater(top["reads"], 3000)
        self.assertGreater(top["breadth"], 0.9)
        self.assertGreater(top["identity"], 0.93)
        self.assertNotIn("divergent", top["flags"])
        self.assertIn("Bovine viral diarrhea virus 1", res["taxa"][0]["name"])
        return res

    def test_contig_mode_then_reset(self):
        job = self.run_job()
        self.assertEqual(job["status"], "done", job["error"])
        st = {s["name"]: s for s in job["stages"]}
        self.assertEqual(st["blast"]["summary"]["mode"], "contigs")
        self.assertEqual(st["blast"]["summary"]["task"], "dc-megablast")
        self.check_bvdv(job["id"])
        detail = self.api.get_result(job["id"], BVDV1)
        self.assertEqual(detail["bins"]["count"], 2000)
        self.assertIn("ref\t", self.api.export_results(job["id"], "tsv"))

        # change a mapping parameter of the finished job: reset to 'map' and rerun
        self.api.set_params(job["id"], "map", {"sensitivity": "normal"}, reset=True)
        self.api.queue_job(job["id"])
        Worker(self.env, exit_when_empty=True, poll=0.1).run()
        job = self.api.get_job(job["id"])
        self.assertEqual(job["status"], "done", job["error"])
        self.check_bvdv(job["id"])

    def test_read_mode_with_manual_selection(self):
        job = self.run_job(params={"assemble": {"mode": "skip"}, "select": {"mode": "manual"}})
        self.assertEqual((job["status"], job["pause_reason"]), ("paused", "select"), job["error"])
        st = {s["name"]: s for s in job["stages"]}
        self.assertEqual(st["blast"]["summary"]["mode"], "reads")
        self.assertEqual(st["blast"]["summary"]["queries"], 10000)
        self.api.set_selection(job["id"], [BVDV1])
        self.api.continue_job(job["id"])
        Worker(self.env, exit_when_empty=True, poll=0.1).run()
        job = self.api.get_job(job["id"])
        self.assertEqual(job["status"], "done", job["error"])
        res = self.check_bvdv(job["id"])
        self.assertEqual([r["ref"] for r in res["references"]], [BVDV1])


@unittest.skipUnless(os.environ.get("READSCREEN_E2E") == "1" and EXAMPLES.is_dir() and blast_db_available(),
                     "set READSCREEN_E2E=1 to run the ex02 end-to-end test (~2 minutes)")
class Ex02EndToEndTest(TempEnvCase):
    def cli(self, *args):
        out = subprocess.run([sys.executable, str(ROOT / "main.py"), "--data-dir", str(self.env.data_dir), *args],
                             capture_output=True, text=True, timeout=1800)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout

    def test_ex02(self):
        self.cli("jobs", "create", "--r1", str(EXAMPLES / "RV4_PC11588_RNA_R1_trimmed_subset.fastq.gz"),
                 "--r2", str(EXAMPLES / "RV4_PC11588_RNA_R2_trimmed_subset.fastq.gz"), "--db", DB)
        self.cli("worker", "run", "--exit-when-empty")
        job = self.api.get_job(1)
        self.assertEqual(job["status"], "done", job["error"])
        leaders = [r for r in self.api.get_ranking(1) if r["leader"]][:2]
        names = " ".join(r["species"] or "" for r in leaders)
        self.assertIn("Bovine viral diarrhea virus 1", names)
        self.assertIn("Bovine viral diarrhea virus 2", names)
        taxa = {t["name"]: t for t in self.api.get_results(1)["taxa"]}
        self.assertGreater(taxa["Bovine viral diarrhea virus 2"]["reads"], 100)
        self.assertIn("Bovine viral diarrhea virus 2", self.cli("results", "1"))


if __name__ == "__main__":
    unittest.main()
