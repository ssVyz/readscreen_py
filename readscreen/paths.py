"""Project paths, data directories and lookup of the bundled binaries."""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IS_WINDOWS = os.name == "nt"
TOOLS = ("simpletrim", "shortread_assembler", "blastn", "blastdbcmd", "minimap2")
DATA_DIR_ENV = "READSCREEN_DATA_DIR"


class ToolMissing(RuntimeError):
    """A required binary is not available for this platform."""


def platform_dir() -> str:
    """Name of the folder in assets/ that holds the binaries for this machine."""
    system = platform.system()
    machine = platform.machine().lower()
    if system == "Windows" and machine in ("amd64", "x86_64"):
        return "x86-amd64-win"
    if system == "Linux" and machine in ("x86_64", "amd64"):
        return "x86-amd64-linux"
    if system == "Linux" and machine in ("aarch64", "arm64"):
        return "aarch64-linux"
    raise ToolMissing(f"no binaries available for {system}/{machine}")


@dataclass(frozen=True)
class Env:
    """Where readscreen keeps its data (db/, jobs/, logs/) and finds its binaries.

    The data directory defaults to the project root; tests and special setups can point it
    elsewhere with --data-dir or the READSCREEN_DATA_DIR environment variable.
    """

    data_dir: Path
    assets_dir: Path = ROOT / "assets"

    @classmethod
    def default(cls, data_dir: str | Path | None = None) -> Env:
        chosen = data_dir or os.environ.get(DATA_DIR_ENV) or ROOT
        return cls(Path(chosen).resolve())

    @property
    def db_path(self) -> Path:
        return self.data_dir / "db" / "readscreen.db"

    @property
    def lock_path(self) -> Path:
        return self.data_dir / "db" / "worker.lock"

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    def ensure_dirs(self) -> None:
        for d in (self.db_path.parent, self.jobs_dir, self.logs_dir):
            d.mkdir(parents=True, exist_ok=True)

    def tool(self, name: str) -> Path:
        """Path of a bundled binary; raises ToolMissing if it is not there."""
        if name not in TOOLS:
            raise ValueError(f"unknown tool: {name}")
        path = self.assets_dir / platform_dir() / (name + (".exe" if IS_WINDOWS else ""))
        if not path.is_file():
            raise ToolMissing(f"binary not found: {path}")
        return path


def resolve_project_path(value: str | Path) -> Path:
    """Relative paths in settings are relative to the project root."""
    p = Path(value)
    return p if p.is_absolute() else (ROOT / p).resolve()
