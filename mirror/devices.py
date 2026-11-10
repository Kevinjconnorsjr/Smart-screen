"""Smart devices the screen can switch: fans, plugs, switches, scenes, Alexa routines, anything with a URL.

Each device in `devices:` gets three actions that gestures, Alexa voice commands and the phone
app can use:  device_on:<id>, device_off:<id>, device_toggle:<id>.

Device types
- home_assistant: any Home Assistant entity (switch.fan, light.lamp, scene.movie, script.x, …).
- alexa:          runs Alexa routines through Voice Monkey (one routine trigger for on, one for off).
                  This is how to reach things that only live in the Alexa app.
- webhook:        a URL for on and one for off (Shelly, Tasmota, IFTTT, Node-RED, …).

Shared logins live under `connections:` (Home Assistant address + token, Voice Monkey token).
"""
from __future__ import annotations

import logging
import re
from urllib.parse import urlencode

import requests

log = logging.getLogger(__name__)
TIMEOUT = 6
VOICE_MONKEY_TRIGGER = "https://api-v3.voicemonkey.io/trigger"


def _filled(value) -> str:
    value = str(value or "").strip()
    return "" if "PASTE" in value else value


def home_assistant(cfg: dict) -> tuple[str, str]:
    """(url, token) from connections, falling back to the lights' Home Assistant settings."""
    conn = (cfg.get("connections") or {}).get("home_assistant") or {}
    lights = (cfg.get("lights") or {}).get("home_assistant") or {}
    url = _filled(conn.get("url")) or _filled(lights.get("url"))
    token = _filled(conn.get("token")) or _filled(lights.get("token"))
    return url.rstrip("/"), token


def voice_monkey_token(cfg: dict) -> str:
    conn = (cfg.get("connections") or {}).get("voice_monkey") or {}
    return _filled(conn.get("token")) or _filled(((cfg.get("lights") or {}).get("alexa") or {}).get("token"))


def device_id(device: dict) -> str:
    """Stable id from the name: "Living room fan" -> "living_room_fan"."""
    return re.sub(r"[^a-z0-9]+", "_", str(device.get("name", "")).lower()).strip("_")[:40]


def device_actions(cfg: dict) -> dict[str, str]:
    """{action_name: label} for every configured device."""
    out = {}
    for d in cfg.get("devices") or []:
        did, name = device_id(d), str(d.get("name", "")).strip()
        if not did:
            continue
        out[f"device_on:{did}"] = f"{name} on"
        out[f"device_off:{did}"] = f"{name} off"
        out[f"device_toggle:{did}"] = f"{name} on/off"
    return out


class Devices:
    """Runs device actions. Remembers on/off for toggling (devices can't always report their state)."""

    def __init__(self, cfg: dict):
        self.cfg = cfg  # the live settings, so edits from the phone apply right away
        self.state: dict[str, bool] = {}

    def find(self, did: str) -> dict | None:
        return next((d for d in self.cfg.get("devices") or [] if device_id(d) == did), None)

    def run(self, action: str) -> bool:
        """Runs "device_on:x" / "device_off:x" / "device_toggle:x". Returns the new on/off state."""
        kind, _, did = action.partition(":")
        device = self.find(did)
        if device is None:
            raise ValueError(f"No device called {did!r} (Settings → Devices)")
        on = {"device_on": True, "device_off": False}.get(kind, not self.state.get(did, False))
        self._switch(device, on)
        self.state[did] = on
        return on

    def _switch(self, d: dict, on: bool) -> None:
        kind = d.get("type", "home_assistant")
        if kind == "home_assistant":
            url, token = home_assistant(self.cfg)
            entity = _filled(d.get("entity_id"))
            if not (url and token and entity):
                raise ValueError("Set up Home Assistant under Settings → Connections, and the device's entity")
            domain = entity.split(".", 1)[0]
            if domain in ("scene", "script") and not on:
                return  # scenes and scripts only "turn on"
            service = "turn_on" if on else "turn_off"
            r = requests.post(f"{url}/api/services/homeassistant/{service}",
                              headers={"Authorization": f"Bearer {token}"}, json={"entity_id": entity}, timeout=TIMEOUT)
            r.raise_for_status()
        elif kind == "alexa":
            token = voice_monkey_token(self.cfg)
            trigger = _filled(d.get("on_routine" if on else "off_routine"))
            if not trigger:
                log.info("%s: no Alexa routine set for %s", d.get("name"), "on" if on else "off")
                return
            if not token:
                raise ValueError("Add your Voice Monkey token under Settings → Connections")
            requests.get(f"{VOICE_MONKEY_TRIGGER}?{urlencode({'token': token, 'device': trigger})}",
                         timeout=TIMEOUT).raise_for_status()
        elif kind == "webhook":
            url = _filled(d.get("on_url" if on else "off_url"))
            if url:
                requests.request(str(d.get("method", "POST")).upper(), url, timeout=TIMEOUT).raise_for_status()
        else:
            raise ValueError(f"Unknown device type {kind!r}")
