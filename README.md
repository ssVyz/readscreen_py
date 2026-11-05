# readscreen

Orchestrator, data layer and frontend for a metagenomic read screening pipeline: trimming
(simpletrim), assembly (shortread_assembler), BLAST against a reference database, ranking of the
hits, mapping (minimap2) and coverage reports. Python 3.10+, standard library only.

Status: backend, worker and CLI (phase 1). The web interface follows in phase 2.

## Quick start (CLI)

```sh
python main.py dbs                                  # BLAST databases in blast_db/
python main.py jobs create --r1 sample_R1.fastq.gz --r2 sample_R2.fastq.gz
python main.py worker run --exit-when-empty         # or: worker start (background) / worker stop
python main.py jobs show 1
python main.py ranking 1                            # ranked BLAST hits
python main.py results 1                            # per taxon; --references for per reference
python main.py export 1 --format tsv -o results.tsv
```

Other useful commands: `python main.py stages` (all stage parameters), `jobs set ID STAGE param=value
[--reset]`, `jobs reset ID STAGE`, `jobs cancel ID`, `jobs delete ID --yes`, `settings`. Everything
also runs with `uv run main.py ...`.

## Tests

```sh
python -m unittest                                  # ~30 s, run from the project folder
READSCREEN_E2E=1 python -m unittest tests.test_pipeline   # adds the ex02 end-to-end run (~2 min)
```

Tests that need the binaries, the bundled BLAST database or `examples/` are skipped when those are missing.
