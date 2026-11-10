"""Smart lights: on/off, brightness and colour, for several systems.

Backends
- home_assistant: any light Home Assistant controls (most brands). Full brightness + colour.
- hue:            Philips Hue bridge directly. Full brightness + colour.
- alexa:          lights that live in the Alexa app, through Alexa routines (Voice Monkey skill).
                  Each action (on, off, dim, a colour…) runs the routine you link to it.
- webhook:        any URL per action (Shelly, Tasmota, IFTTT, Node-RED…).
- none:           nothing (the screen still shows what would happen).
"""
from __future__ import annotations

import colorsys
import logging

import requests

log = logging.getLogger(__name__)
TIMEOUT = 6

# Colour presets used by the gestures, the phone app and the photo booth.
# ("kelvin" = white light colour temperature; "rgb" = a colour.)
COLORS: dict[str, dict] = {
    "warm": {"kelvin": 2700},
    "daylight": {"kelvin": 4000},
    "cool": {"kelvin": 6000},
    "red": {"rgb": (255, 40, 30)},
    "orange": {"rgb": (255, 140, 20)},
    "yellow": {"rgb": (255, 220, 60)},
    "green": {"rgb": (40, 220, 80)},
    "blue": {"rgb": (40, 90, 255)},
    "purple": {"rgb": (150, 60, 255)},
    "pink": {"rgb": (255, 80, 180)},
}
PARTY = ["red", "orange", "yellow", "green", "blue", "purple", "pink"]


class Lights:
    """Base / "none" backend. Remembers state so toggle and the app's display work everywhere."""

    supports_brightness = False
    supports_color = False

    def __init__(self):
        self.is_on = False
        self.brightness = 100  # percent
        self.color: str | None = None
        self._party = 0

    # ---- what the rest of the app calls ----
    def turn_on(self) -> None:
        self._set(True)
        self.is_on = True

    def turn_off(self) -> None:
        self._set(False)
        self.is_on = False

    def toggle(self) -> None:
        self.turn_off() if self.is_on else self.turn_on()

    def set_brightness(self, percent: int) -> None:
        percent = max(1, min(100, int(percent)))
        self._brightness(percent)
        self.brightness, self.is_on = percent, True

    def set_color(self, name: str) -> None:
        if name not in COLORS:
            raise ValueError(f"Unknown colour {name!r}")
        self._color(name)
        self.color, self.is_on = name, True

    def next_party_color(self) -> None:
        self.set_color(PARTY[self._party % len(PARTY)])
        self._party += 1

    def state(self) -> dict:
        return {"on": self.is_on, "brightness": self.brightness, "color": self.color,
                "supports_brightness": self.supports_brightness, "supports_color": self.supports_color,
                "colors": list(COLORS)}

    # ---- what each backend implements ----
    def _set(self, on: bool) -> None:
        log.info("Lights %s (no lights system set up)", "on" if on else "off")

    def _brightness(self, percent: int) -> None:
        log.info("Lights to %d%% (no lights system set up)", percent)

    def _color(self, name: str) -> None:
        log.info("Lights %s (no lights system set up)", name)


class HomeAssistantLights(Lights):
    supports_brightness = supports_color = True

    def __init__(self, url: str, token: str, entity_ids: list[str]):
        super().__init__()
        self.url = url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}"}
        self.entity_ids = list(entity_ids)

    def _call(self, service: str, **data) -> None:
        r = requests.post(f"{self.url}/api/services/light/{service}", headers=self.headers,
                          json={"entity_id": self.entity_ids, **data}, timeout=TIMEOUT)
        r.raise_for_status()

    def _set(self, on: bool) -> None:
        self._call("turn_on" if on else "turn_off")

    def _brightness(self, percent: int) -> None:
        self._call("turn_on", brightness_pct=percent)

    def _color(self, name: str) -> None:
        c = COLORS[name]
        if "kelvin" in c:
            self._call("turn_on", color_temp_kelvin=c["kelvin"])
        else:
            self._call("turn_on", rgb_color=list(c["rgb"]))


class HueLights(Lights):
    supports_brightness = supports_color = True

    def __init__(self, bridge_ip: str, username: str, light_ids: list):
        super().__init__()
        self.base = f"http://{bridge_ip}/api/{username}/lights"
        self.light_ids = list(light_ids)

    def _put(self, state: dict) -> None:
        for light in self.light_ids:
            r = requests.put(f"{self.base}/{light}/state", json=state, timeout=TIMEOUT)
            r.raise_for_status()

    def _set(self, on: bool) -> None:
        self._put({"on": on})

    def _brightness(self, percent: int) -> None:
        self._put({"on": True, "bri": max(1, round(percent * 254 / 100))})

    def _color(self, name: str) -> None:
        c = COLORS[name]
        if "kelvin" in c:
            self._put({"on": True, "ct": max(153, min(500, round(1_000_000 / c["kelvin"])))})
        else:
            h, s, _ = colorsys.rgb_to_hsv(*(v / 255 for v in c["rgb"]))
            self._put({"on": True, "hue": round(h * 65535), "sat": round(s * 254)})


class ActionURLLights(Lights):
    """Lights driven by one URL per action: webhooks, or Alexa routines through Voice Monkey."""

    def __init__(self, urls: dict[str, str], method: str = "POST"):
        super().__init__()
        self.urls = {k: v for k, v in (urls or {}).items() if v}
        self.method = method.upper()
        self.supports_brightness = bool(self.urls.get("dim") or self.urls.get("bright"))
        self.supports_color = any(name in self.urls for name in COLORS)

    def _fire(self, key: str) -> None:
        url = self.urls.get(key)
        if not url:
            log.info("No link set up for lights %r", key)
            return
        r = requests.request(self.method, url, timeout=TIMEOUT)
        r.raise_for_status()

    def _set(self, on: bool) -> None:
        self._fire("on" if on else "off")

    def _brightness(self, percent: int) -> None:
        self._fire("dim" if percent < 60 else "bright")

    def _color(self, name: str) -> None:
        self._fire(name)

    def next_party_color(self) -> None:
        if "party" in self.urls:  # an Alexa routine / scene that does the party lights itself
            self._fire("party")
            self.color, self.is_on = "party", True
            return
        colors = [c for c in PARTY if c in self.urls]
        if colors:
            self.set_color(colors[self._party % len(colors)])
            self._party += 1


VOICE_MONKEY_TRIGGER = "https://api-v3.voicemonkey.io/trigger"


def alexa_urls(token: str, routines: dict[str, str]) -> dict[str, str]:
    """Voice Monkey trigger URL for each action that has a routine trigger ID."""
    from urllib.parse import urlencode

    return {action: f"{VOICE_MONKEY_TRIGGER}?{urlencode({'token': token, 'device': device})}"
            for action, device in (routines or {}).items() if device and token and "PASTE" not in token}


def make_lights(cfg: dict, full_cfg: dict | None = None) -> Lights:
    """`cfg` is the lights section; `full_cfg` (the whole config) lets shared connections fill in logins."""
    backend = (cfg.get("backend") or "none").lower()
    if backend == "home_assistant":
        c = cfg["home_assistant"]
        url, token = c.get("url", ""), c.get("token", "")
        if full_cfg is not None:
            from .devices import home_assistant

            url, token = home_assistant(full_cfg)
        return HomeAssistantLights(url, token, c["entity_ids"])
    if backend == "hue":
        c = cfg["hue"]
        return HueLights(c["bridge_ip"], c["username"], c["light_ids"])
    if backend == "alexa":
        c = cfg.get("alexa") or {}
        token = c.get("token", "")
        if full_cfg is not None:
            from .devices import voice_monkey_token

            token = voice_monkey_token(full_cfg)
        return ActionURLLights(alexa_urls(token, c.get("routines") or {}), method="GET")
    if backend == "webhook":
        c = cfg.get("webhook") or {}
        urls = {"on": c.get("on_url", ""), "off": c.get("off_url", ""), **(c.get("urls") or {})}
        return ActionURLLights(urls, c.get("method", "POST"))
    return Lights()
