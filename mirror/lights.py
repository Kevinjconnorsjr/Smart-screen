"""Smart-light backends: Home Assistant, Philips Hue, any webhook, or none."""
from __future__ import annotations

import logging

import requests

log = logging.getLogger(__name__)
TIMEOUT = 5


class Lights:
    """Base / "none" backend. Remembers on/off so toggle works everywhere."""

    def __init__(self):
        self.is_on = False

    def turn_on(self) -> None:
        self._set(True)
        self.is_on = True

    def turn_off(self) -> None:
        self._set(False)
        self.is_on = False

    def toggle(self) -> None:
        self.turn_off() if self.is_on else self.turn_on()

    def _set(self, on: bool) -> None:
        log.info("Lights %s (no lights backend configured)", "on" if on else "off")


class HomeAssistantLights(Lights):
    def __init__(self, url: str, token: str, entity_ids: list[str]):
        super().__init__()
        self.url = url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}"}
        self.entity_ids = list(entity_ids)

    def _set(self, on: bool) -> None:
        service = "turn_on" if on else "turn_off"
        r = requests.post(f"{self.url}/api/services/light/{service}", headers=self.headers,
                          json={"entity_id": self.entity_ids}, timeout=TIMEOUT)
        r.raise_for_status()


class HueLights(Lights):
    def __init__(self, bridge_ip: str, username: str, light_ids: list):
        super().__init__()
        self.base = f"http://{bridge_ip}/api/{username}/lights"
        self.light_ids = list(light_ids)

    def _set(self, on: bool) -> None:
        for light in self.light_ids:
            r = requests.put(f"{self.base}/{light}/state", json={"on": on}, timeout=TIMEOUT)
            r.raise_for_status()


class WebhookLights(Lights):
    def __init__(self, on_url: str, off_url: str, method: str = "POST"):
        super().__init__()
        self.on_url, self.off_url, self.method = on_url, off_url, method.upper()

    def _set(self, on: bool) -> None:
        url = self.on_url if on else self.off_url
        if url:
            requests.request(self.method, url, timeout=TIMEOUT).raise_for_status()


def make_lights(cfg: dict) -> Lights:
    backend = (cfg.get("backend") or "none").lower()
    if backend == "home_assistant":
        c = cfg["home_assistant"]
        return HomeAssistantLights(c["url"], c["token"], c["entity_ids"])
    if backend == "hue":
        c = cfg["hue"]
        return HueLights(c["bridge_ip"], c["username"], c["light_ids"])
    if backend == "webhook":
        c = cfg["webhook"]
        return WebhookLights(c.get("on_url", ""), c.get("off_url", ""), c.get("method", "POST"))
    return Lights()
