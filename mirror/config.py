"""Loads config.yaml on top of config.example.yaml defaults."""
from __future__ import annotations

import copy
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULTS_FILE = ROOT / "config.example.yaml"


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path: str | Path | None = None) -> dict:
    defaults = yaml.safe_load(DEFAULTS_FILE.read_text()) or {}
    path = Path(path) if path else ROOT / "config.yaml"
    if not path.exists():
        return defaults
    return _merge(defaults, yaml.safe_load(path.read_text()) or {})


def resolve(path: str) -> Path:
    """Relative paths in the config are relative to the project folder."""
    p = Path(path)
    return p if p.is_absolute() else ROOT / p
