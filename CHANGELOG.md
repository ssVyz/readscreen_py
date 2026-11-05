# Changelog

All notable changes to this project are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versioning: [SemVer](https://semver.org/).

## [0.0.3] - 2026-11-05

Phase 2 of `implementation_plan.md`: web server and frontend.

### Added
- `readscreen serve [--host] [--port] [--open]`: `ThreadingHTTPServer` on 127.0.0.1:8765 with a JSON API that maps
  1:1 to `readscreen.api`, and static file serving (explicit MIME types, no path traversal).
- API: file browsing for the picker (`fs_roots`, `browse`, `suggest_inputs` with R1/R2 detection), `info`,
  `settings_schema`.
- Single-page frontend (plain HTML/CSS/JS modules, no libraries, light and dark mode):
  - jobs page with worker status and start/stop/stop-now, live job queue with queue/continue/cancel/delete;
  - new-job form with file picker (drives, home, folder path, FASTQ/FASTA filter), automatic R1/R2 detection,
    platform, workflow choices, pauses and all other stage parameters;
  - job page with stages (state, time, summary, log viewer, parameter editor with reset confirmation,
    reset to a stage, pause toggles), inputs with reassignment, notes, analysis results and suggestions;
  - hit selection with groups and alternatives, search, automatic preselection, save and continue;
  - results per taxon or per reference with flags, TSV/JSON download, and a reference page with stat tiles
    and a coverage plot (mean depth per bin, linear/log, crosshair tooltip, keyboard reading, table view);
  - settings page: general settings, defaults for new jobs, BLAST databases, version and data folder.
- Tests for the HTTP API and static serving.

### Fixed
- The worker clears its current job when the job ends (the status no longer shows a finished job).
- The "extra argument not allowed" error no longer repeats the stage name.

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
