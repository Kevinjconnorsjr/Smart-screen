"""Reading and saving settings from the phone app.

- Changes are written into config.yaml without losing its comments or layout (ruamel.yaml).
- Only keys that actually changed are written, so a short config.yaml stays short.
- Values are checked against the types in config.example.yaml.
- Some keys can only be changed on the Pi itself: anything that runs a command, points at a
  folder on disk, or changes how the server is reached.
- Most changes apply instantly; a few (lights, camera, ...) need a quick restart.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

MASK = "••••••••"

# Secrets: never sent to the phone. Saving the mask back keeps the current value.
SECRET_KEYS = {
    ("lights", "home_assistant", "token"),
    ("lights", "hue", "username"),
}

# Only editable on the Pi (they run commands, point at folders, or change how the server is reached).
LOCKED_KEYS = {
    ("display", "screen_off_command"),
    ("display", "screen_on_command"),
    ("display", "host"),
    ("display", "port"),
    ("gestures", "model_path"),
    ("photobooth", "output_dir"),
    ("photobooth", "print_options"),
    ("slideshow", "folders"),
    ("slideshow", "cache_dir"),
    ("camera", "backend"),
    ("remote",),
}

# Changing anything under these needs a restart; everything else applies live.
RESTART_KEYS = {
    ("lights",), ("camera",), ("remote",),
    ("gestures", "min_score"), ("gestures", "hold_seconds"), ("gestures", "cooldown_seconds"),
    ("display", "sleep_after_minutes"), ("display", "always_on_hours"), ("display", "wake_on_hand"),
    ("slideshow", "size"),
}

GESTURES = {"Open_Palm", "Closed_Fist", "Pointing_Up", "Thumb_Up", "Thumb_Down", "Victory", "ILoveYou"}
MAX_STRING = 4000


class SettingsError(ValueError):
    pass


def _get(d: dict, path: tuple) -> Any:
    for key in path:
        if not isinstance(d, dict) or key not in d:
            return None
        d = d[key]
    return d


def _has(d: dict, path: tuple) -> bool:
    for key in path:
        if not isinstance(d, dict) or key not in d:
            return False
        d = d[key]
    return True


def _set(d: dict, path: tuple, value: Any) -> None:
    for key in path[:-1]:
        if not isinstance(d.get(key), dict):
            d[key] = CommentedMap() if isinstance(d, CommentedMap) else {}
        d = d[key]
    d[path[-1]] = value


def _matches(path: tuple, prefixes: set) -> bool:
    return any(path[: len(p)] == p for p in prefixes)


def masked(cfg: dict) -> dict:
    """Copy of the settings that is safe to send to the phone."""
    out = copy.deepcopy(cfg)
    for path in SECRET_KEYS:
        value = _get(out, path)
        if value and "PASTE" not in str(value):
            _set(out, path, MASK)
    return out


def _check(value: Any, default: Any, path: tuple) -> Any:
    where = ".".join(path)
    if isinstance(default, bool):
        if not isinstance(value, bool):
            raise SettingsError(f"{where} must be on/off")
        return value
    if isinstance(default, (int, float)):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SettingsError(f"{where} must be a number")
        return value
    if isinstance(default, str):
        if not isinstance(value, str):
            raise SettingsError(f"{where} must be text")
        if len(value) > MAX_STRING:
            raise SettingsError(f"{where} is too long")
        return value
    if isinstance(default, list):
        if not isinstance(value, list) or len(value) > 200:
            raise SettingsError(f"{where} must be a list")
        sample = default[0] if default else None
        return [_check(v, sample, path) if sample is not None else _plain(v, path) for v in value]
    if isinstance(default, dict):
        if not isinstance(value, dict):
            raise SettingsError(f"{where} must be a group of settings")
        out = {}
        for key, v in value.items():
            if key in default:
                out[key] = _check(v, default[key], path + (key,))
            else:
                out[key] = _plain(v, path + (key,))
        return out
    return _plain(value, path)


def _plain(value: Any, path: tuple) -> Any:
    """Values with no example to compare with: allow plain JSON-ish data only."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if len(value) > MAX_STRING:
            raise SettingsError(f"{'.'.join(path)} is too long")
        return value
    if isinstance(value, list):
        return [_plain(v, path) for v in value]
    if isinstance(value, dict):
        return {str(k): _plain(v, path + (str(k),)) for k, v in value.items()}
    raise SettingsError(f"{'.'.join(path)} has an unsupported value")


def validate(new: dict, current: dict, defaults: dict, actions: list[str]) -> dict:
    """Checked copy of `new`: unknown sections dropped, locked keys and masked secrets restored."""
    if not isinstance(new, dict):
        raise SettingsError("Settings must be an object")
    out = copy.deepcopy(current)
    for section, value in new.items():
        if section not in defaults or _matches((section,), LOCKED_KEYS):
            continue
        out[section] = _check(value, defaults[section], (section,))
        # Keys the example doesn't list but the current config has are kept as given.
    for path in LOCKED_KEYS:
        if _has(current, path):
            _set(out, path, copy.deepcopy(_get(current, path)))
        elif _has(out, path):
            # remove locked keys the phone tried to add
            parent = _get(out, path[:-1]) if len(path) > 1 else out
            parent.pop(path[-1], None)
    for path in SECRET_KEYS:
        if _get(out, path) == MASK:
            _set(out, path, _get(current, path))
    bindings = _get(out, ("gestures", "bindings")) or {}
    for gesture, action in bindings.items():
        if gesture not in GESTURES:
            raise SettingsError(f"Unknown gesture {gesture}")
        if action and action not in actions:
            raise SettingsError(f"Unknown action {action}")
    for feed in _get(out, ("calendar", "feeds")) or []:
        if not isinstance(feed, dict) or not isinstance(feed.get("url", ""), str):
            raise SettingsError("Each calendar needs a name and a link")
    return out


def changed_paths(old: Any, new: Any, path: tuple = ()) -> list[tuple]:
    if isinstance(old, dict) and isinstance(new, dict):
        out = []
        for key in set(old) | set(new):
            if key not in old or key not in new:
                out.append(path + (key,))
            else:
                out += changed_paths(old[key], new[key], path + (key,))
        return out
    return [] if old == new else [path]


def needs_restart(paths: list[tuple]) -> bool:
    return any(_matches(p, RESTART_KEYS) for p in paths)


def save(config_path: Path, example_path: Path, old: dict, new: dict) -> list[tuple]:
    """Write the changed values into config.yaml (creating it from the example if missing)."""
    paths = changed_paths(old, new)
    if not paths:
        return []
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096
    yaml.indent(mapping=2, sequence=4, offset=2)  # same list style as config.example.yaml
    source = config_path if config_path.exists() else example_path
    data = yaml.load(source.read_text()) or CommentedMap()
    for path in paths:
        if _has(new, path):
            _set(data, path, copy.deepcopy(_get(new, path)))
        else:
            parent = _get(data, path[:-1]) if len(path) > 1 else data
            if isinstance(parent, dict):
                parent.pop(path[-1], None)
    tmp = config_path.with_suffix(".yaml.tmp")
    with tmp.open("w") as f:
        yaml.dump(data, f)
    tmp.replace(config_path)
    return paths


def apply_live(cfg: dict, new: dict) -> None:
    """Update the running settings in place, so every part holding a reference sees the change."""
    _replace(cfg, new)


def _replace(target: dict, src: dict) -> None:
    for key in list(target):
        if key not in src:
            del target[key]
    for key, value in src.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _replace(target[key], value)
        else:
            target[key] = copy.deepcopy(value)
