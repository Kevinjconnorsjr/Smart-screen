"""Hand-gesture recognition with Google MediaPipe, plus hold/cooldown logic
so a gesture fires once, deliberately, instead of on every video frame."""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

log = logging.getLogger(__name__)

MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/gesture_recognizer/"
    "gesture_recognizer/float16/latest/gesture_recognizer.task"
)


class GestureTrigger:
    """Turns a stream of per-frame gesture guesses into single "fire" events.

    A gesture fires when it is seen continuously for `hold_seconds`. It will
    not fire again until the hand changes gesture (or leaves), and no gesture
    fires during `cooldown_seconds` after any fire.
    """

    def __init__(self, hold_seconds: float = 0.8, cooldown_seconds: float = 2.5, min_score: float = 0.65):
        self.hold = hold_seconds
        self.cooldown = cooldown_seconds
        self.min_score = min_score
        self._current: str | None = None
        self._since = 0.0
        self._fired_current = False
        self._last_fire = float("-inf")

    def update(self, name: str | None, score: float, now: float) -> str | None:
        if name in (None, "", "None") or score < self.min_score:
            name = None
        if name != self._current:
            self._current, self._since, self._fired_current = name, now, False
            return None
        if (
            name is not None
            and not self._fired_current
            and now - self._since >= self.hold
            and now - self._last_fire >= self.cooldown
        ):
            self._fired_current = True
            self._last_fire = now
            return name
        return None

    def progress(self, now: float) -> float:
        """0..1 of how long the current gesture has been held (for the on-screen ring)."""
        if self._current is None or self._fired_current or self.hold <= 0:
            return 0.0
        return min(1.0, (now - self._since) / self.hold)

    @property
    def current(self) -> str | None:
        return self._current


class GestureWatcher:
    """Runs the MediaPipe recognizer on camera frames in a background thread."""

    def __init__(self, camera, cfg: dict, model_path: str, on_fire: Callable[[str], None],
                 on_progress: Callable[[str | None, float], None] | None = None):
        self.camera = camera
        self.cfg = cfg
        self.model_path = model_path
        self.on_fire = on_fire
        self.on_progress = on_progress
        self.paused = threading.Event()
        self.trigger = GestureTrigger(
            hold_seconds=float(cfg.get("hold_seconds", 0.8)),
            cooldown_seconds=float(cfg.get("cooldown_seconds", 2.5)),
            min_score=float(cfg.get("min_score", 0.65)),
        )
        self._running = False

    def start(self) -> None:
        self._running = True
        threading.Thread(target=self._loop, name="gestures", daemon=True).start()

    def stop(self) -> None:
        self._running = False

    def _loop(self) -> None:
        import cv2
        import mediapipe as mp
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision

        options = vision.GestureRecognizerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=self.model_path),
            running_mode=vision.RunningMode.VIDEO,
            num_hands=1,
        )
        recognizer = vision.GestureRecognizer.create_from_options(options)
        log.info("Gesture recognizer ready")
        start = time.monotonic()
        last_t = 0.0
        last_report = (None, -1.0)
        while self._running:
            got = self.camera.latest(newer_than=last_t, timeout=1.0)
            if got is None:
                continue
            frame, last_t = got
            if self.paused.is_set():
                self.trigger.update(None, 0.0, last_t)
                continue
            # Recognition doesn't need full resolution; ~640px wide is plenty and much faster on a Pi.
            h, w = frame.shape[:2]
            if w > 640:
                frame = cv2.resize(frame, (640, int(h * 640 / w)), interpolation=cv2.INTER_AREA)
            image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame)
            result = recognizer.recognize_for_video(image, int((last_t - start) * 1000))

            name, score = None, 0.0
            if result.gestures and result.gestures[0]:
                top = result.gestures[0][0]
                name, score = top.category_name, top.score
            fired = self.trigger.update(name, score, last_t)

            if self.on_progress:
                report = (self.trigger.current, round(self.trigger.progress(last_t), 1))
                if report != last_report:
                    self.on_progress(*report)
                    last_report = report
            if fired:
                log.info("Gesture: %s (%.2f)", fired, score)
                try:
                    self.on_fire(fired)
                except Exception:  # an action failing must not kill the watcher
                    log.exception("Action for %s failed", fired)
        recognizer.close()
