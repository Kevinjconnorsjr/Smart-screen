"""One camera, shared by the gesture recognizer and the photo booth.

A background thread keeps the newest frame. Frames are kept in the camera's own BGR order and are
never modified after capture, so readers can share them without copying (copying and colour-
converting every full-HD frame was a big part of why the preview felt slow).

Speed: USB webcams send uncompressed video by default, which at 1920x1080 is only ~5 frames per
second over USB. Asking for MJPEG gives the full 30 fps.
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
            # Picamera2's "RGB888" is BGR byte order in memory, which is what we keep.
            self._grab = cam.capture_array
            self._close = cam.stop
        else:
            import cv2

            index = int(self.cfg.get("index", 0))
            cap = cv2.VideoCapture(index, cv2.CAP_V4L2) if hasattr(cv2, "CAP_V4L2") else cv2.VideoCapture(index)
            if not cap.isOpened():
                cap = cv2.VideoCapture(index)
            if not cap.isOpened():
                raise RuntimeError("Could not open the camera. Is it plugged in? Try another camera.index.")
            # Order matters: pick the compressed format before the size, so the size is allowed at 30 fps.
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
            cap.set(cv2.CAP_PROP_FPS, int(self.cfg.get("fps", 30)))
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # always the newest frame, no lag behind a queue
            fourcc = int(cap.get(cv2.CAP_PROP_FOURCC))
            log.info("Camera: %dx%d @ %.0f fps (%s)", cap.get(cv2.CAP_PROP_FRAME_WIDTH), cap.get(cv2.CAP_PROP_FRAME_HEIGHT),
                     cap.get(cv2.CAP_PROP_FPS), "".join(chr((fourcc >> 8 * i) & 0xFF) for i in range(4)))

            def grab():
                ok, bgr = cap.read()
                return bgr if ok else None

            self._grab = grab
            self._close = cap.release

        self._running = True
        self._thread = threading.Thread(target=self._loop, name="camera", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        import cv2

        # A webcam clipped to a monitor that's been turned on its side sees the room sideways.
        turn = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180,
                270: cv2.ROTATE_90_COUNTERCLOCKWISE}.get(int(self.cfg.get("rotate", 0) or 0))
        while self._running:
            frame = self._grab()
            if frame is None:
                time.sleep(0.05)
                continue
            if turn is not None:
                frame = cv2.rotate(frame, turn)
            with self._lock:
                self._frame = frame  # a new array every time; never modified afterwards
                self._frame_time = time.monotonic()

    def latest_bgr(self, newer_than: float = 0.0, timeout: float = 2.0) -> tuple[np.ndarray, float] | None:
        """Newest frame (shared, read-only, BGR) and its timestamp, waiting for one newer than `newer_than`."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if self._frame is not None and self._frame_time > newer_than:
                    return self._frame, self._frame_time
            time.sleep(0.005)
        return None

    def latest(self, newer_than: float = 0.0, timeout: float = 2.0) -> tuple[np.ndarray, float] | None:
        """Newest full-size frame as RGB (used for photo booth shots)."""
        got = self.latest_bgr(newer_than, timeout)
        return None if got is None else (np.ascontiguousarray(got[0][:, :, ::-1]), got[1])

    def mjpeg(self, width: int = 800, mirror: bool = True, fps: float = 24):
        """Live preview for the screen as a multipart MJPEG stream (small and fast)."""
        import cv2

        last = 0.0
        min_gap = 1 / fps
        while self._running:
            got = self.latest_bgr(newer_than=last, timeout=2.0)
            if got is None:
                continue
            frame, last = got
            started = time.monotonic()
            h, w = frame.shape[:2]
            if w > width:
                frame = cv2.resize(frame, (width, int(h * width / w)), interpolation=cv2.INTER_LINEAR)
            if mirror:
                frame = cv2.flip(frame, 1)
            ok, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
            if ok:
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpg.tobytes() + b"\r\n"
            spare = min_gap - (time.monotonic() - started)
            if spare > 0:
                time.sleep(spare)

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        if self._close:
            self._close()
