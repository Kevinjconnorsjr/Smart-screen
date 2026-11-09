"""Voice control from Alexa: "Alexa, turn on the photo booth".

Uses fauxmo (https://github.com/n8henrie/fauxmo), which makes the Pi look like a few smart plugs
on your Wi-Fi. Alexa finds them with "Alexa, discover devices"; no Alexa skill, account or cloud
service is involved. Turning a "plug" on or off runs a Smart Screen action through the local API.

Configured under `alexa:` in config.yaml (or the phone app: Settings -> Alexa voice commands).
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)
FIRST_PORT = 52300  # each virtual device gets its own fixed port, so Alexa keeps recognising it


def fauxmo_config(devices: list[dict], app_port: int) -> dict:
    base = f"http://127.0.0.1:{app_port}"
    out = []
    for i, d in enumerate(devices):
        name = str(d.get("name", "")).strip()
        if not name or not (d.get("on") or d.get("off")):
            continue
        out.append({
            "name": name,
            "port": FIRST_PORT + i,
            "on_cmd": f"{base}/api/action/{d['on']}" if d.get("on") else f"{base}/api/noop",
            "off_cmd": f"{base}/api/action/{d['off']}" if d.get("off") else f"{base}/api/noop",
            "method": "POST",
            "use_fake_state": True,
            "initial_state": "off",
        })
    return {"FAUXMO": {"ip_address": "auto"}, "PLUGINS": {"SimpleHTTPPlugin": {"DEVICES": out}}}


class AlexaVoice:
    """Runs fauxmo as a child process (it needs the main thread of its own process) and restarts it if it stops."""

    def __init__(self, cfg: dict, app_port: int, config_path: Path):
        self.cfg = cfg or {}
        self.app_port = app_port
        self.config_path = config_path
        self.proc: subprocess.Popen | None = None
        self.error = ""

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.get("voice_commands"))

    def start(self) -> None:
        if not self.enabled:
            return
        try:
            import fauxmo  # noqa: F401
        except ImportError:
            self.error = "fauxmo isn't installed (run ./deploy/install.sh)"
            log.warning("Alexa voice commands: %s", self.error)
            return
        config = fauxmo_config(self.cfg.get("devices") or [], self.app_port)
        if not config["PLUGINS"]["SimpleHTTPPlugin"]["DEVICES"]:
            self.error = "No devices set up"
            return
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(json.dumps(config, indent=2))
        threading.Thread(target=self._run, name="alexa", daemon=True).start()

    def _run(self) -> None:
        cmd = [sys.executable, "-c", "from fauxmo.cli import cli; cli()", "-c", str(self.config_path)]
        while True:
            log.info("Alexa voice commands: starting (say \"Alexa, discover devices\" the first time)")
            self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            _, err = self.proc.communicate()
            self.error = (err or "").strip().splitlines()[-1][-200:] if err and err.strip() else "stopped"
            log.warning("Alexa voice commands stopped (%s); restarting in 30 s", self.error)
            time.sleep(30)

    def status(self) -> dict:
        running = self.proc is not None and self.proc.poll() is None
        return {"enabled": self.enabled, "running": running, "error": "" if running else self.error}
