"""Security cameras on the screen (and in the phone app).

Each camera in `cameras.list` has a name and a source:
- rtsp://user:password@192.168.1.50:554/stream   most security cameras (Reolink, Amcrest, Tapo, Eufy,
                                                  Hikvision, Dahua, Wyze with RTSP…): use the camera's
                                                  smaller "sub stream" if it has one
- http(s)://… an MJPEG stream or a snapshot (.jpg) address
- ha:camera.front_door                            any camera in Home Assistant (Ring, Nest, Blink… if
                                                  your Home Assistant can show it)

The Pi turns each source into a small, light MJPEG stream with ffmpeg. One ffmpeg runs per camera
and only while someone is watching; the screen and phones share it.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import threading
import time
from typing import Callable

from .devices import home_assistant

log = logging.getLogger(__name__)
ALLOWED = ("rtsp://", "rtsps://", "http://", "https://", "ha:")
SOI, EOI = b"\xff\xd8", b"\xff\xd9"


def valid_source(url: str) -> bool:
    return isinstance(url, str) and url.strip().lower().startswith(ALLOWED)


def ffmpeg_command(ffmpeg: str, url: str, cfg: dict, width: int = 960, fps: int = 8) -> list[str]:
    """ffmpeg that reads a camera and writes JPEG frames to stdout."""
    url = url.strip()
    cmd = [ffmpeg, "-nostdin", "-loglevel", "error"]
    if url.lower().startswith(("rtsp://", "rtsps://")):
        cmd += ["-rtsp_transport", "tcp", "-timeout", "8000000"]
    elif url.startswith("ha:"):
        base, token = home_assistant(cfg)
        cmd += ["-headers", f"Authorization: Bearer {token}\r\n"]
        url = f"{base}/api/camera_proxy_stream/{url[3:]}"
    else:
        cmd += ["-rw_timeout", "8000000"]
    return cmd + ["-i", url, "-an", "-vf", f"scale='min({width},iw)':-2,fps={fps}",
                  "-q:v", "7", "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"]


def split_jpegs(buffer: bytearray) -> list[bytes]:
    """Take complete JPEG images out of the front of the buffer."""
    frames = []
    while True:
        start = buffer.find(SOI)
        if start < 0:
            buffer.clear()
            return frames
        end = buffer.find(EOI, start + 2)
        if end < 0:
            if start:
                del buffer[:start]
            return frames
        frames.append(bytes(buffer[start:end + 2]))
        del buffer[:end + 2]


class CameraRelay:
    """Runs ffmpeg for one camera while it has viewers, and hands the newest frame to each of them."""

    def __init__(self, source: Callable[[], str], cfg: Callable[[], dict], ffmpeg: str):
        self._source = source
        self._cfg = cfg
        self.ffmpeg = ffmpeg
        self.frame: bytes | None = None
        self.frame_no = 0
        self.error = ""
        self.viewers = 0
        self._cond = threading.Condition()
        self._thread: threading.Thread | None = None

    def _run(self) -> None:
        while True:
            with self._cond:
                if self.viewers == 0:
                    self._thread = None
                    return
            proc = subprocess.Popen(ffmpeg_command(self.ffmpeg, self._source(), self._cfg()),
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            buffer = bytearray()
            try:
                while True:
                    chunk = proc.stdout.read1(65536) if hasattr(proc.stdout, "read1") else proc.stdout.read(65536)
                    if not chunk:
                        break
                    buffer += chunk
                    for jpg in split_jpegs(buffer):
                        with self._cond:
                            self.frame, self.frame_no, self.error = jpg, self.frame_no + 1, ""
                            self._cond.notify_all()
                    with self._cond:
                        if self.viewers == 0:
                            break
            finally:
                proc.kill()
                err = proc.stderr.read().decode(errors="replace").strip() if proc.stderr else ""
                proc.wait()
            if err:
                self.error = err.splitlines()[-1][-200:]
                log.warning("Camera stream stopped: %s", self.error)
            time.sleep(1)  # snapshot cameras end after one picture: this makes them refresh every second

    def stream(self):
        """MJPEG for one viewer (a browser <img>)."""
        with self._cond:
            self.viewers += 1
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="camera-relay", daemon=True)
                self._thread.start()
        seen = 0
        try:
            while True:
                with self._cond:
                    self._cond.wait_for(lambda: self.frame_no != seen, timeout=10)
                    frame, seen = self.frame, self.frame_no
                if frame is None:
                    continue
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
        finally:
            with self._cond:
                self.viewers -= 1


class Cameras:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.ffmpeg = shutil.which("ffmpeg")
        self._relays: dict[str, CameraRelay] = {}
        self._lock = threading.Lock()

    def list(self) -> list[dict]:
        return [c for c in ((self.cfg.get("cameras") or {}).get("list") or [])
                if isinstance(c, dict) and valid_source(c.get("url", ""))]

    def relay(self, index: int) -> CameraRelay | None:
        cams = self.list()
        if not self.ffmpeg or not 0 <= index < len(cams):
            return None
        url = cams[index]["url"]
        with self._lock:
            relay = self._relays.get(url)
            if relay is None:
                relay = CameraRelay(lambda u=url: u, lambda: self.cfg, self.ffmpeg)
                self._relays[url] = relay
            return relay
