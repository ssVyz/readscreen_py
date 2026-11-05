# readscreen_py

Python project. Entry point: `main.py`. Dirs: `assets/`, `db/`, `jobs/`.

## Rules
- Every code change = patch version bump + entry in `CHANGELOG.md` (Keep a Changelog format, newest on top).
- Bump minor/major only when explicitly requested.
- Never `git commit` / `git push` — commits are done by humans.
- Do not modify `CLAUDE.md` or `.gitignore` unless instructed.

## Version
Current version = top entry in `CHANGELOG.md`. If a version is added elsewhere (e.g. `pyproject.toml`), keep it in sync.

## repos for binaries used in this project (read only, do not edit from out of this project)
- blastn and blastdbcmd -> official blast suite, you can refer to official documentation
- minimap2 -> unchanged from official version, you can refer to official documentation
- shortread_assembler -> Repo available under C://repos/shortread_assembler_rs
- simpletrim -> Repo available under C://repos/simpletrim_rs

Binaries are in their respective folders in assets/. If you require changes for any of them, ask and describe what exactly is needed and why.
