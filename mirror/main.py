"""Smart display entry point: python -m mirror.main [--config config.yaml] [--no-camera]"""
from __future__ import annotations

import argparse
import logging
import subprocess
import threading
import time

from .config import ROOT, load_config, resolve
from .lights import make_lights
from .power import ScreenPower
from .web import EventBus, create_app

log = logging.getLogger("mirror")


def code_version() -> str:
    """Changes on every restart, so the browser reloads itself after an update."""
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                                text=True, timeout=5).stdout.strip()
    except Exception:
        commit = ""
    return f"{commit or 'dev'}-{int(time.time())}"


class Mirror:
    def __init__(self, cfg: dict, bus: EventBus, lights, booth=None, watcher=None, power: ScreenPower | None = None):
        self.cfg = cfg
        self.bus = bus
        self.lights = lights
        self.booth = booth
        self.watcher = watcher
        self.power = power or ScreenPower({}, bus.emit)
        self.actions = {
            "lights_on": self.lights_on,
            "lights_off": self.lights_off,
            "lights_toggle": self.lights_toggle,
            "photo_booth": self.photo_booth,
            "wake": self.power.wake,
            "sleep": self.power.sleep,
        }

    # Gesture name -> action name -> run it.
    def on_gesture(self, gesture: str) -> None:
        action = (self.cfg["gestures"].get("bindings") or {}).get(gesture)
        self.bus.emit("gesture", {"name": gesture, "action": action})
        if action:
            self.run(action)

    def on_hold(self, gesture: str | None, progress: float) -> None:
        """A hand is holding a gesture: show the progress ring and wake the screen."""
        if gesture and progress > 0 and self.cfg["display"].get("wake_on_hand", True):
            self.power.activity()
        self.bus.emit("hold", {"name": gesture, "progress": progress})

    def run(self, action: str) -> bool:
        fn = self.actions.get(action)
        if fn is None:
            log.warning("Unknown action %r (check gestures.bindings in config.yaml)", action)
            return False
        if action != "sleep":
            self.power.activity()
        threading.Thread(target=self._safe, args=(action, fn), daemon=True).start()
        return True

    def _safe(self, action: str, fn) -> None:
        try:
            fn()
        except Exception as e:
            log.exception("Action %s failed", action)
            self.bus.emit("status", {"text": f"{action.replace('_', ' ')} failed: {e}"})

    def _lights(self, change) -> None:
        change()
        self.bus.emit("lights", {"on": self.lights.is_on})

    def lights_on(self):
        self._lights(self.lights.turn_on)

    def lights_off(self):
        self._lights(self.lights.turn_off)

    def lights_toggle(self):
        self._lights(self.lights.toggle)

    def photo_booth(self):
        if self.booth is None:
            self.bus.emit("status", {"text": "No camera connected"})
            return
        if self.watcher:
            self.watcher.paused.set()  # don't let poses for the photos trigger actions
        started = self.booth.start(on_done=self.watcher.paused.clear if self.watcher else None)
        if not started:
            self.bus.emit("status", {"text": "Already taking photos!"})


def main() -> None:
    parser = argparse.ArgumentParser(description="Gesture-controlled smart display and photo booth")
    parser.add_argument("--config", help="path to config.yaml")
    parser.add_argument("--no-camera", action="store_true", help="run the screen only (for testing the UI)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    cfg = load_config(args.config)
    bus = EventBus()
    photos_dir = resolve(cfg["photobooth"].get("output_dir", "photos"))
    photos_dir.mkdir(parents=True, exist_ok=True)
    power = ScreenPower(cfg["display"], bus.emit)
    mirror = Mirror(cfg, bus, make_lights(cfg["lights"]), power=power)
    power.start()

    camera = watcher = None
    if not args.no_camera:
        from .camera import Camera
        from .gestures import MODEL_URL, GestureWatcher
        from .photobooth import PhotoBooth

        model = resolve(cfg["gestures"]["model_path"])
        if not model.exists():
            raise SystemExit(f"Gesture model missing: {model}\nDownload it with:\n  curl -L -o {model} {MODEL_URL}")
        camera = Camera(cfg["camera"])
        camera.start()
        mirror.booth = PhotoBooth(camera, cfg["photobooth"], photos_dir, bus.emit)
        watcher = GestureWatcher(camera, cfg["gestures"], str(model), on_fire=mirror.on_gesture,
                                 on_progress=mirror.on_hold)
        mirror.watcher = watcher
        watcher.start()

    display = cfg["display"]
    app = create_app(cfg, bus, photos_dir, mirror.run, list(mirror.actions), camera=camera, version=code_version())
    log.info("Screen at http://%s:%s", display["host"], display["port"])
    try:
        app.run(host=display["host"], port=int(display["port"]), threaded=True)
    finally:
        if watcher:
            watcher.stop()
        if camera:
            camera.stop()


if __name__ == "__main__":
    main()
