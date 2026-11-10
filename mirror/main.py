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

ACTION_LABELS = {
    "lights_on": "Lights on", "lights_off": "Lights off", "lights_toggle": "Toggle lights",
    "lights_dim": "Dim lights", "lights_bright": "Bright lights", "lights_warm": "Warm light",
    "lights_cool": "Cool light", "lights_party": "Party colours", "photo_booth": "Photo booth",
    "show_cameras": "Show/hide cameras", "hide_cameras": "Hide cameras",
    "wake": "Wake screen", "sleep": "Sleep screen",
}


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
        from .devices import Devices

        self.devices = Devices(cfg)
        self.cameras_shown = False
        self.base_actions = {
            "lights_on": self.lights_on,
            "lights_off": self.lights_off,
            "lights_toggle": self.lights_toggle,
            "lights_dim": lambda: self._lights(lambda: self.lights.set_brightness(30)),
            "lights_bright": lambda: self._lights(lambda: self.lights.set_brightness(100)),
            "lights_warm": lambda: self._lights(lambda: self.lights.set_color("warm")),
            "lights_cool": lambda: self._lights(lambda: self.lights.set_color("cool")),
            "lights_party": lambda: self._lights(self.lights.next_party_color),
            "photo_booth": self.photo_booth,
            "show_cameras": lambda: self.show_cameras(None),
            "hide_cameras": lambda: self.show_cameras(False),
            "wake": self.power.wake,
            "sleep": self.power.sleep,
        }

    @property
    def actions(self) -> dict:
        """Built-in actions plus one on/off/toggle action per device in the settings (always current)."""
        from .devices import device_actions

        out = dict(self.base_actions)
        for name in device_actions(self.cfg):
            out[name] = lambda a=name: self.run_device(a)
        return out

    def action_labels(self) -> dict[str, str]:
        from .devices import device_actions

        return {**ACTION_LABELS, **device_actions(self.cfg)}

    def run_device(self, action: str) -> None:
        on = self.devices.run(action)
        name = (self.devices.find(action.partition(":")[2]) or {}).get("name", "Device")
        self.bus.emit("status", {"text": f"{name} {'on' if on else 'off'}"})

    def show_cameras(self, show: bool | None) -> None:
        """Show the security cameras on the screen (None = toggle). They hide again after a while."""
        self.cameras_shown = (not self.cameras_shown) if show is None else show
        seconds = int(((self.cfg.get("cameras") or {}).get("show_seconds")) or 60)
        blink = getattr(self, "blink", None)
        if self.cameras_shown and blink is not None and (self.cfg.get("blink") or {}).get("new_picture_on_show"):
            for name in blink.camera_names():  # fresh Blink pictures (uses some battery)
                threading.Thread(target=blink.snap, args=(name,), daemon=True).start()
        self.bus.emit("cameras", {"show": self.cameras_shown, "seconds": seconds})
        self._camera_timer = getattr(self, "_camera_timer", 0) + 1
        if self.cameras_shown and seconds > 0:
            mine = self._camera_timer

            def hide_later():
                if self._camera_timer == mine and self.cameras_shown:
                    self.show_cameras(False)

            t = threading.Timer(seconds, hide_later)
            t.daemon = True
            t.start()

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
        self.bus.emit("lights", self.lights.state())

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
    mirror = Mirror(cfg, bus, make_lights(cfg["lights"], cfg), power=power)
    power.start()

    from .custom_gestures import CustomGestures, Teacher

    custom = CustomGestures(resolve("data/custom_gestures.json"))
    teacher = Teacher()
    camera = watcher = None
    problems: list[str] = []
    if not args.no_camera:
        # The screen must keep working even if the camera or gesture recognition can't start
        # (no webcam plugged in, model missing, ...): log it, show it, and carry on without them.
        try:
            from .camera import Camera
            from .gestures import MODEL_URL, GestureWatcher
            from .photobooth import PhotoBooth

            model = resolve(cfg["gestures"]["model_path"])
            if not model.exists():
                raise RuntimeError(f"Gesture model missing ({model}). Download it with: curl -L -o {model} {MODEL_URL}")
            camera = Camera(cfg["camera"])
            camera.start()
            mirror.booth = PhotoBooth(camera, cfg["photobooth"], photos_dir, bus.emit)
            watcher = GestureWatcher(camera, cfg["gestures"], str(model), on_fire=mirror.on_gesture,
                                     on_progress=mirror.on_hold, custom=custom, teacher=teacher, blink=blink)
            mirror.watcher = watcher
            watcher.start()
        except Exception as e:
            log.error("Camera / gestures are off: %s", e)
            problems.append(f"Camera off: {e}")
            if camera is not None:
                try:
                    camera.stop()
                except Exception:
                    pass
            camera = watcher = None
            mirror.booth = mirror.watcher = None

    display = cfg["display"]
    from .alexa import AlexaVoice
    from .screen import apply_rotation

    apply_rotation(cfg)  # also done by the kiosk when the desktop starts
    alexa = AlexaVoice(cfg.get("alexa") or {}, int(display["port"]), resolve(".cache/alexa.json"))
    alexa.start()
    from .blink import BlinkService

    blink = BlinkService(cfg, resolve(".secrets/blink.json"), bus.emit, on_motion=power.activity)
    blink.start()
    mirror.blink = blink
    app = create_app(cfg, bus, photos_dir, mirror, camera=camera, version=code_version(), problems=problems,
                     config_path=resolve(args.config) if args.config else None, start_background=True, alexa=alexa,
                     custom=custom, teacher=teacher)
    host = display.get("host", "127.0.0.1")
    if (cfg.get("remote") or {}).get("enabled", True) and host in ("127.0.0.1", "localhost"):
        host = "0.0.0.0"  # the phone app needs to reach us; only paired phones get in (see remote.py)
    log.info("Screen at http://localhost:%s (phone remote: http://<this Pi>:%s/remote)", display["port"], display["port"])
    try:
        app.run(host=host, port=int(display["port"]), threaded=True)
    finally:
        if watcher:
            watcher.stop()
        if camera:
            camera.stop()


if __name__ == "__main__":
    main()
