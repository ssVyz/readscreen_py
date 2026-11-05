# Changelog

All notable changes to this project are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versioning: [SemVer](https://semver.org/).

## [0.0.2] - 2026-11-05

Phase 1 of `implementation_plan.md`: backend, worker and CLI.

### Added
- `pyproject.toml` (Python >= 3.10, no dependencies) and the `readscreen` package.
- Foundation: data folders with `--data-dir`/`READSCREEN_DATA_DIR` override, binary lookup per platform,
  SQLite store (WAL, schema version), settings table with `default.<stage>.<param>` overrides, parameter schemas.
- Python logic: FASTQ→FASTA with even subsampling, file fingerprints, R1/R2 mate detection, hit ranking
  (non-redundant reference score, top-percent filter, grouping, automatic selection), coverage from PAF
  (CIGAR-based depth, breadth, identity, expected breadth, binned depth, flags divergent/uneven/ambiguous).
- Subprocess runner: process groups, per-stage logs, cancellation with process-tree kill.
- Ten pipeline stages: input, analyze, trim, assemble (incl. external assembly and long-read fragmenting),
  blast (contigs or reads), rank, select (automatic or manual), extract, map, report (`result.json`, depth file).
- Job/stage model: create, parameters per stage, pauses, reset, cancel, delete (only inside `jobs/`),
  input reassignment with fingerprint check, hit selection.
- Worker: single instance via file lock, FIFO queue, graceful and immediate stop, job cancellation,
  heartbeat, crash recovery, detached start.
- CLI: `dbs`, `stages`, `jobs ...`, `ranking`, `results`, `export`, `worker ...`, `settings ...`.
- Tests (`python -m unittest`): unit, tool, worker and synthetic pipeline tests;
  the ex02 end-to-end test runs with `READSCREEN_E2E=1`.

## [0.0.1] - 2026-11-05

### Added
- Initial project setup: `main.py` entry point, `assets/`, `db/`, `jobs/` directories.
- `CHANGELOG.md` and `CLAUDE.md` housekeeping files.
