"""Pipeline stages. Each stage module provides:

- PARAMS: parameter schema (readscreen.params.Schema)
- run(ctx: StageContext) -> dict: does the work in ctx.out_dir and returns a summary
- optional is_skipped(params) -> bool and check(params, platform) (extra validation)
"""

from __future__ import annotations

from types import ModuleType

from ..layout import STAGE_NAMES

_REGISTRY: dict[str, ModuleType] | None = None


def registry() -> dict[str, ModuleType]:
    """Stage name -> module, in pipeline order."""
    global _REGISTRY
    if _REGISTRY is None:
        from . import (
            analyze,
            assemble,
            blast,
            extract,
            input_files,
            mapping,
            rank,
            report,
            selection,
            trim,
        )

        mods = {
            "input": input_files,
            "analyze": analyze,
            "trim": trim,
            "assemble": assemble,
            "blast": blast,
            "rank": rank,
            "select": selection,
            "extract": extract,
            "map": mapping,
            "report": report,
        }
        assert tuple(mods) == STAGE_NAMES
        _REGISTRY = mods
    return _REGISTRY
