"""One camera, shared by the gesture recognizer and the photo booth.

A background thread keeps the newest frame; readers get a copy of it.
Frames are RGB numpy arrays, exactly as the camera sees them (not mirrored).
"""
from __future__ import annotations

import logging
import threading
import time

import numpy as np

log = logging.getLogger(__name__)


class Camera:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._frame: np.ndarray | None = None
        self._frame_time = 0.0
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self._grab = None
        self._close = None

    def start(self) -> None:
        backend = self.cfg.get("backend", "opencv")
        w, h = int(self.cfg.get("width", 1280)), int(self.cfg.get("height", 720))
        if backend == "picamera2":
            from picamera2 import Picamera2  # Raspberry Pi OS: sudo apt install python3-picamera2

            cam = Picamera2()
            cam.configure(cam.create_video_configuration(main={"size": (w, h), "format": "RGB888"}))
            cam.start()
            # Picamera2's "RGB888" is BGR byte order in memory.
            self._grab = lambda: cam.capture_array()[:, :, ::-1]
            self._close = cam.stop
        else:
            import cv2

            cap = cv2.VideoCapture(int(self.cfg.get("index", 0)))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
            if not cap.isOpened():
                raise RuntimeError("Could not open the camera. Is it plugged in? Try another camera.index.")

            def grab():
                ok, bgr = cap.read()
                return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB) if ok else None

            self._grab = grab
            self._close = cap.release

        self._running = True
        self._thread = threading.Thread(target=self._loop, name="camera", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while self._running:
            frame = self._grab()
            if frame is None:
                time.sleep(0.05)
                continue
            with self._lock:
                self._frame = np.ascontiguousarray(frame)
                self._frame_time = time.monotonic()

    def latest(self, newer_than: float = 0.0, timeout: float = 2.0) -> tuple[np.ndarray, float] | None:
        """Newest frame (copy) and its timestamp, waiting for one newer than `newer_than`."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if self._frame is not None and self._frame_time > newer_than:
                    return self._frame.copy(), self._frame_time
            time.sleep(0.01)
        return None

    def mjpeg(self, width: int = 960, mirror: bool = True, fps: float = 20):
        """Live preview for the screen as a multipart MJPEG stream."""
        import cv2

        last = 0.0
        while self._running:
            got = self.latest(newer_than=last, timeout=2.0)
            if got is None:
                continue
            frame, last = got
            h, w = frame.shape[:2]
            if w > width:
                frame = cv2.resize(frame, (width, int(h * width / w)), interpolation=cv2.INTER_AREA)
            if mirror:
                frame = frame[:, ::-1]
            ok, jpg = cv2.imencode(".jpg", np.ascontiguousarray(frame[:, :, ::-1]), [cv2.IMWRITE_JPEG_QUALITY, 75])
            if ok:
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpg.tobytes() + b"\r\n"
            time.sleep(1 / fps)

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        if self._close:
            self._close()
