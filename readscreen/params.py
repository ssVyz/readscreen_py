"""Parameter schemas and validation, shared by stage parameters and settings.

Values may come from code, the CLI (strings) or JSON (the web interface), so `coerce`
accepts strings for every type.
"""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass
from typing import Any


class ParamError(ValueError):
    """Invalid parameter name or value."""


@dataclass(frozen=True)
class Param:
    type: type  # bool, int, float, str or list
    default: Any = None  # a value, or a callable(platform) -> value
    choices: tuple | None = None
    min: float | None = None
    max: float | None = None
    help: str = ""
    internal: bool = False  # set by readscreen itself, not by the user

    def default_for(self, platform: str | None) -> Any:
        value = self.default(platform) if callable(self.default) else self.default
        return list(value) if isinstance(value, list) else value


Schema = dict[str, Param]

_TRUE = {"1", "true", "yes", "on", "y"}
_FALSE = {"0", "false", "no", "off", "n"}


def coerce(name: str, p: Param, value: Any) -> Any:
    """Convert `value` to the parameter type and check choices and limits.

    None (or the string "default"/"none" for optional values) means "not set".
    """
    if isinstance(value, str) and value.strip().lower() in ("none", "null", "default", ""):
        if p.type is not str or value.strip().lower() in ("none", "null", "default"):
            value = None
    if value is None:
        return None
    try:
        if p.type is bool:
            if isinstance(value, bool):
                out: Any = value
            elif isinstance(value, (int, float)) and value in (0, 1):
                out = bool(value)
            elif isinstance(value, str) and value.strip().lower() in _TRUE | _FALSE:
                out = value.strip().lower() in _TRUE
            else:
                raise ValueError
        elif p.type is int:
            if isinstance(value, bool):
                raise ValueError
            if isinstance(value, float):
                if not value.is_integer():
                    raise ValueError
                out = int(value)
            else:
                out = int(str(value).replace("_", "").replace(",", ""))
        elif p.type is float:
            if isinstance(value, bool):
                raise ValueError
            out = float(value)
        elif p.type is list:
            if isinstance(value, (list, tuple)):
                out = [str(v) for v in value]
            elif isinstance(value, str):
                text = value.strip()
                out = [str(v) for v in json.loads(text)] if text.startswith("[") else shlex.split(text)
            else:
                raise ValueError
        elif p.type is str:
            out = str(value)
        else:  # pragma: no cover - schema error
            raise TypeError(f"unsupported parameter type {p.type}")
    except (ValueError, TypeError, json.JSONDecodeError):
        raise ParamError(f"{name}: invalid value {value!r} (expected {p.type.__name__})") from None
    if p.choices is not None and out not in p.choices:
        raise ParamError(f"{name}: {out!r} is not one of {', '.join(map(str, p.choices))}")
    if p.min is not None and out < p.min:
        raise ParamError(f"{name}: {out} is below the minimum {p.min}")
    if p.max is not None and out > p.max:
        raise ParamError(f"{name}: {out} is above the maximum {p.max}")
    return out


def defaults(schema: Schema, platform: str | None) -> dict:
    return {k: p.default_for(platform) for k, p in schema.items()}


def validate(
    schema: Schema,
    updates: dict,
    platform: str | None,
    base: dict | None = None,
    allow_internal: bool = False,
) -> dict:
    """Merge `updates` into `base` (or the defaults) and return the complete, checked dict."""
    out = dict(base) if base is not None else defaults(schema, platform)
    for k in schema:
        out.setdefault(k, schema[k].default_for(platform))
    for key, value in updates.items():
        if key not in schema:
            raise ParamError(f"unknown parameter: {key} (known: {', '.join(sorted(schema))})")
        p = schema[key]
        if p.internal and not allow_internal:
            raise ParamError(f"{key} is set by readscreen and cannot be changed")
        v = coerce(key, p, value)
        out[key] = p.default_for(platform) if v is None else v
    return out


def describe(schema: Schema, platform: str | None) -> list[dict]:
    """Schema as plain data, for the CLI help and the web interface."""
    return [
        {
            "name": k,
            "type": p.type.__name__,
            "default": p.default_for(platform),
            "choices": list(p.choices) if p.choices else None,
            "min": p.min,
            "max": p.max,
            "help": p.help,
        }
        for k, p in schema.items()
        if not p.internal
    ]


def parse_assignment(text: str) -> tuple[str, Any]:
    """'key=value' -> (key, value); the value stays a string unless it is JSON."""
    if "=" not in text:
        raise ParamError(f"expected key=value, got {text!r}")
    key, value = text.split("=", 1)
    value = value.strip()
    if value[:1] in "[{":
        try:
            return key.strip(), json.loads(value)
        except json.JSONDecodeError:
            pass
    return key.strip(), value
