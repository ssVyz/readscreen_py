"""Test helpers: temporary data folders and synthetic sequence data (generated, never stored)."""

from __future__ import annotations

import gzip
import random
import subprocess
import tempfile
import unittest
from pathlib import Path

from readscreen.api import Api
from readscreen.paths import ROOT, Env, ToolMissing

BLAST_DB = ROOT / "blast_db" / "ref_viruses_rep_genomes" / "ref_viruses_rep_genomes"
EXAMPLES = ROOT / "examples" / "ex02_reduced_set"
_COMP = str.maketrans("ACGT", "TGCA")


class TempEnvCase(unittest.TestCase):
    """A fresh data folder (db/, jobs/, logs/) per test."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.tmp = Path(self._tmp.name)
        self.env = Env(self.tmp / "data")
        self.api = Api(self.env)

    def tearDown(self):
        self._tmp.cleanup()


def tools_available() -> bool:
    try:
        Env.default().tool("simpletrim")
    except ToolMissing:
        return False
    return True


def blast_db_available() -> bool:
    return any(BLAST_DB.parent.glob(BLAST_DB.name + ".*")) and tools_available()


def random_seq(n: int, rng: random.Random) -> str:
    return "".join(rng.choice("ACGT") for _ in range(n))


def mutate(seq: str, identity: float, rng: random.Random) -> str:
    out = list(seq)
    for i in range(len(out)):
        if rng.random() > identity:
            out[i] = rng.choice([b for b in "ACGT" if b != out[i]])
    return "".join(out)


def revcomp(seq: str) -> str:
    return seq.translate(_COMP)[::-1]


def simulate_pairs(genome: str, n: int, rng: random.Random, read_len: int = 150,
                   frag: tuple[int, int] = (250, 400), error: float = 0.002) -> list[tuple[str, str]]:
    pairs = []
    for _ in range(n):
        f = rng.randint(*frag)
        start = rng.randint(0, max(0, len(genome) - f))
        fragment = genome[start:start + f]
        if rng.random() < 0.5:
            fragment = revcomp(fragment)
        pairs.append((mutate(fragment[:read_len], 1 - error, rng),
                      mutate(revcomp(fragment)[:read_len], 1 - error, rng)))
    return pairs


def write_fastq_gz(path: Path, reads: list[str], prefix: str = "sim", mate: int = 1) -> Path:
    with gzip.open(path, "wt", newline="\n") as fh:
        for i, s in enumerate(reads):
            fh.write(f"@{prefix}_{i} {mate}:N:0:1\n{s}\n+\n{'I' * len(s)}\n")
    return path


def write_pairs(folder: Path, pairs: list[tuple[str, str]], name: str = "sample") -> tuple[Path, Path]:
    r1 = write_fastq_gz(folder / f"{name}_R1.fastq.gz", [p[0] for p in pairs], mate=1)
    r2 = write_fastq_gz(folder / f"{name}_R2.fastq.gz", [p[1] for p in pairs], mate=2)
    return r1, r2


def write_fasta(path: Path, records: list[tuple[str, str]]) -> Path:
    with open(path, "w", newline="\n") as fh:
        for name, seq in records:
            fh.write(f">{name}\n")
            for i in range(0, len(seq), 80):
                fh.write(seq[i:i + 80] + "\n")
    return path


def fetch_reference(accession: str) -> str:
    """Sequence of a reference from the bundled BLAST database."""
    exe = Env.default().tool("blastdbcmd")
    out = subprocess.run([str(exe), "-db", str(BLAST_DB), "-entry", accession, "-outfmt", "%s"],
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()
