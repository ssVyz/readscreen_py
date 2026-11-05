"""Job folder layout, stage order and safe deletion inside jobs/."""

from __future__ import annotations

import os
import shutil
import stat
import sys
import time
from pathlib import Path

from .paths import Env

STAGE_NAMES = ("input", "analyze", "trim", "assemble", "blast", "rank", "select", "extract", "map", "report")
PLATFORMS = ("illumina", "ont", "hifi", "clr")
LONG_READ_PLATFORMS = ("ont", "hifi", "clr")


def is_long(platform: str) -> bool:
    return platform in LONG_READ_PLATFORMS


def stage_index(name: str) -> int:
    """1-based position of a stage."""
    try:
        return STAGE_NAMES.index(name) + 1
    except ValueError:
        raise ValueError(f"unknown stage: {name} (stages: {', '.join(STAGE_NAMES)})") from None


def job_dir(env: Env, job_id: int) -> Path:
    return env.jobs_dir / f"{job_id:05d}"


def stage_dir(env: Env, job_id: int, name: str) -> Path:
    return job_dir(env, job_id) / f"{stage_index(name):02d}_{name}"


def stage_tmp(env: Env, job_id: int, name: str) -> Path:
    return job_dir(env, job_id) / f"{stage_index(name):02d}_{name}.tmp"


def stage_log(env: Env, job_id: int, name: str) -> Path:
    return job_dir(env, job_id) / "logs" / f"{stage_index(name):02d}_{name}.log"


def imports_dir(env: Env, job_id: int) -> Path:
    return job_dir(env, job_id) / "imports"


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return path.resolve() != parent.resolve()


def _on_rm_error(func, path, _exc):
    os.chmod(path, stat.S_IWRITE)
    func(path)


def _rmtree(path: Path) -> None:
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_on_rm_error)
    else:
        shutil.rmtree(path, onerror=_on_rm_error)


def safe_rmtree(env: Env, path: Path) -> None:
    """Delete a folder or file, but only strictly inside jobs/."""
    if not _inside(path, env.jobs_dir):
        raise RuntimeError(f"refusing to delete outside the jobs folder: {path}")
    if not path.exists():
        return
    for attempt in range(5):
        try:
            if path.is_dir():
                _rmtree(path)
            else:
                path.unlink()
            return
        except OSError:
            if attempt == 4:
                raise
            time.sleep(0.2 * (attempt + 1))  # Windows: virus scanner or indexer holding a handle


def safe_rename(src: Path, dst: Path) -> None:
    for attempt in range(5):
        try:
            os.replace(src, dst)
            return
        except OSError:
            if attempt == 4:
                raise
            time.sleep(0.2 * (attempt + 1))
