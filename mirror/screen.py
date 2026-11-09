"""Screen orientation: landscape, or portrait (monitor turned on its side).

The monitor output is rotated with wlr-randr (Raspberry Pi OS desktop) through deploy/screen.sh.
It's applied when the Smart Screen starts, by the kiosk just before the browser opens (the desktop
may not be running yet when the service starts), and right away when changed in the phone app.
"""
from __future__ import annotations

import logging
import subprocess

from .config import ROOT

log = logging.getLogger(__name__)


def transform(cfg: dict) -> str:
    """wlr-randr transform for the configured orientation: normal, 90 or 270."""
    display = cfg.get("display") or {}
    if display.get("orientation", "landscape") != "portrait":
        return "normal"
    return "270" if str(display.get("portrait_turned", "right")) == "left" else "90"


def is_portrait(cfg: dict) -> bool:
    return transform(cfg) != "normal"


def apply_rotation(cfg: dict) -> None:
    script = ROOT / "deploy" / "screen.sh"
    try:
        subprocess.Popen([str(script), "rotate", transform(cfg)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:  # not on a Pi desktop (tests, laptops): nothing to rotate
        log.debug("Screen rotation not applied: %s", e)
