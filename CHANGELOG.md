# Changelog

All notable changes to this project are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versioning: [SemVer](https://semver.org/).

## [0.0.5] - 2026-11-06

### Added
- Trimming mode `trim.mode` (auto, short, long). Auto keeps the previous behaviour: long for nanopore and PacBio,
  short otherwise. Long mode with paired reads is rejected.
- simpletrim options as trim parameters instead of extra arguments:
  - quality filter (base quality, max % low-quality bases, minimum mean read quality);
  - maximum length;
  - window size, and window trimming from the right;
  - fixed front/tail trimming;
  - adapters: on/off, preset, start/end adapter, chimeric reads;
  - poly-X tails;
  - low-quality stretches and their threshold.
- Parameter schemas carry presentation hints: `group`, `only` (short/long) and `tool_default`. Parameter editors
  show group headings, hide options of the other read mode, and show the tool's default as the placeholder.
  `readscreen stages` lists the tool defaults.
- Read analysis on the job page: full-width card with a statistics table per read file, extra rows (insert size and
  overlap for pairs, adapters for long reads), and a quality plot along the reads (R1/R2 per cycle, or long reads
  from either end) with tooltip, keyboard reading and table view.
  - The same statistics are available with `readscreen jobs analysis ID` (`GET /api/jobs/{id}/analysis`) and are
    written to the analyze log.
  - They are read from the stage's `analyze.json`, so existing jobs show them too.
- `assemble.fragment_overlap` (default 50): overlapping fragments of long reads for the assembler.

### Changed
- New job page: one card per pipeline step (reads, read check and trimming, assembly, BLAST, hit selection,
  mapping), each with its main choices, its own "More options" and its pause checkboxes. Trimming shows the mode,
  minimum mean read quality and minimum length directly; long-read assembly shows the fragment settings.
- Jobs created by older versions get the defaults of parameters added since (worker and parameter changes).

### Fixed
- The trim summary of long reads showed no input read count.

## [0.0.4] - 2026-11-05

### Added
- Download of the RefSeq viral reference genomes (`ref_viruses_rep_genomes`, with NCBI's taxonomy files) from
  NCBI: standard library only, resumable, md5-checked, tested with `blastdbcmd` before it is installed. Runs as its
  own background process (one at a time) with progress, cancel and error reporting.
  - CLI: `dbs check` (installed vs. NCBI version), `dbs download [--background] [--force]`, `dbs status`,
    `dbs cancel`.
  - Settings page: "Download from NCBI" card with versions, download/update, progress and cancel; the new-job
    page links there when no database is installed.
  - Setting `download_url` (NCBI or a mirror).
- Updating a database does not disturb running jobs: the folder swap waits until no job is in the blast or extract
  stage, and those stages wait while a swap is pending.
- The blast stage records the database version (shown on the job page and written to `result.json`); extract notes
  when the database was updated after blast.
- Tests for the download against a local fake NCBI server (resume, checksum, unsafe archives, cancel, waiting for
  the worker); `READSCREEN_NET=1` adds a check of NCBI's real metadata files.

### Changed
- DB schema version 2 (new `download` table); older databases are upgraded automatically.
- A worker stop request ("now" as well as graceful) is honoured between stages, before the next stage starts.

### Fixed
- Saving settings clears the web server's cached database list (a changed `blast_db_dir` showed up only after 30 s).

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
