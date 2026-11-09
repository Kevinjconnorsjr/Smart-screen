"""Background slideshow: finds photos and videos in folders and serves screen-ready copies.

- Photos: big camera photos (12-48 MP) are slow for the Pi's browser, so each is resized once to
  the screen size, turned the right way up, and cached.
- Videos: the Pi 5 has no hardware decoder for phone video (H.264/HEVC), and the browser can't
  play iPhone HEVC at all. Each video is converted once, in the background, to a small, silent
  H.264 clip (720p, at most `video_max_seconds` long) that plays smoothly. A video shows up in the
  slideshow once its copy is ready.
- Small thumbnails of both are made for the phone app.
"""
from __future__ import annotations

import hashlib
import logging
import queue
import random
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from PIL import Image, ImageOps

log = logging.getLogger(__name__)

try:  # iPhone photos (.heic)
    from pillow_heif import register_heif_opener

    register_heif_opener()
    HEIC = True
except ImportError:
    HEIC = False

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"} | ({".heic", ".heif"} if HEIC else set())
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".3gp"}
EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS
THUMB = (400, 400)
VIDEO_LONG_SIDE = 1280


def is_video(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXTENSIONS


class Backgrounds:
    def __init__(self, folders: list[Path] | Callable[[], list[Path]], cache_dir: Path,
                 size: tuple[int, int] | Callable[[], tuple[int, int]] = (1920, 1080),
                 skip_names: tuple[str, ...] = ("print.jpg",),
                 video_max_seconds: float | Callable[[], float] = 60):
        self._folders = folders
        self.cache_dir = cache_dir
        self._size = size
        self.skip_names = skip_names
        self._video_max = video_max_seconds
        self._photos: dict[str, Path] = {}
        self._scanned_at = 0.0
        self._lock = threading.Lock()
        self._jobs: queue.Queue = queue.Queue()
        self._queued: set[str] = set()
        self._failed: set[str] = set()
        self._worker: threading.Thread | None = None
        self.ffmpeg = shutil.which("ffmpeg")

    @property
    def folders(self) -> list[Path]:
        return self._folders() if callable(self._folders) else self._folders

    @property
    def size(self) -> tuple[int, int]:
        return self._size() if callable(self._size) else self._size

    def _scan(self) -> None:
        photos = {}
        for folder in self.folders:
            if not folder.is_dir():
                continue
            for path in folder.rglob("*"):
                if (path.suffix.lower() in EXTENSIONS and path.name not in self.skip_names
                        and not any(part.startswith(".") for part in path.relative_to(folder).parts)):
                    photos[hashlib.sha1(str(path).encode()).hexdigest()[:16]] = path
        self._photos = photos
        self._scanned_at = time.time()
        for pid, path in photos.items():  # prepare any new videos in the background
            if is_video(path) and not self._video_out(pid, path).exists():
                self._queue_video(pid)

    def list(self, max_age: float = 120, shuffle: bool = True) -> list[str]:
        """Photo ids (random order by default). Rescans the folders every couple of minutes."""
        with self._lock:
            if time.time() - self._scanned_at > max_age:
                self._scan()
            ids = list(self._photos)
        if shuffle:
            random.shuffle(ids)
        return ids

    def items(self, max_age: float = 120, shuffle: bool = True, videos: bool = True) -> list[dict]:
        """[{id, type}] ready to show: photos, plus videos whose screen copy is ready."""
        out = []
        for pid in self.list(max_age=max_age, shuffle=shuffle):
            path = self.source(pid)
            if path is None:
                continue
            if is_video(path):
                if videos and self._video_out(pid, path).exists():
                    out.append({"id": pid, "type": "video"})
            else:
                out.append({"id": pid, "type": "image"})
        return out

    def video_state(self, photo_id: str) -> str:
        """ready | processing | failed | unavailable (no ffmpeg)"""
        path = self.source(photo_id)
        if path is None:
            return "failed"
        if self._video_out(photo_id, path).exists():
            return "ready"
        if photo_id in self._failed:
            return "failed"
        return "processing" if self.ffmpeg else "unavailable"

    def source(self, photo_id: str) -> Path | None:
        with self._lock:
            path = self._photos.get(photo_id)
        return path if path is not None and path.exists() else None

    def image_path(self, photo_id: str, thumb: bool = False) -> Path | None:
        """Screen-sized (or thumbnail) JPEG for a photo id, made on first use. Videos: thumbnail only."""
        src = self.source(photo_id)
        if src is None:
            return None
        if is_video(src):
            return self._video_thumb(photo_id, src) if thumb else None
        size = THUMB if thumb else self.size
        stamp = int(src.stat().st_mtime)
        out = self.cache_dir / f"{photo_id}-{stamp}-{'t' if thumb else ''}{size[0]}x{size[1]}.jpg"
        if not out.exists():
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            with Image.open(src) as im:
                im = ImageOps.exif_transpose(im).convert("RGB")
                im.thumbnail(size if thumb else self._fit_box(im.size, size), Image.LANCZOS)
                tmp = out.with_suffix(".tmp")
                im.save(tmp, "JPEG", quality=80 if thumb else 85)
                tmp.replace(out)
        return out

    # ---------- videos ----------

    def video_path(self, photo_id: str) -> Path | None:
        """The screen copy of a video, if it's ready."""
        src = self.source(photo_id)
        if src is None or not is_video(src):
            return None
        out = self._video_out(photo_id, src)
        return out if out.exists() else None

    def _video_out(self, photo_id: str, src: Path) -> Path:
        limit = int(self._video_max() if callable(self._video_max) else self._video_max)
        return self.cache_dir / f"{photo_id}-{int(src.stat().st_mtime)}-v{limit}.mp4"

    def _queue_video(self, photo_id: str) -> None:
        if not self.ffmpeg or photo_id in self._queued or photo_id in self._failed:
            return
        self._queued.add(photo_id)
        self._jobs.put(photo_id)
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._work, name="video-prep", daemon=True)
            self._worker.start()

    def _work(self) -> None:
        while True:  # one video at a time, at low priority, for as long as the program runs
            pid = self._jobs.get()
            try:
                self._convert(pid)
            except Exception as e:
                log.warning("Couldn't prepare video %s: %s", pid, e)
                self._failed.add(pid)
            finally:
                self._queued.discard(pid)

    def _convert(self, photo_id: str) -> None:
        src = self.source(photo_id)
        if src is None:
            return
        out = self._video_out(photo_id, src)
        if out.exists():
            return
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".part.mp4")
        limit = str(int(self._video_max() if callable(self._video_max) else self._video_max))
        long_side = VIDEO_LONG_SIDE
        scale = (f"scale='if(gte(iw,ih),min({long_side},iw),-2)':'if(gte(iw,ih),-2,min({long_side},ih))',"
                 "fps=30,format=yuv420p")
        cmd = ["nice", "-n", "15", self.ffmpeg, "-nostdin", "-loglevel", "error", "-y", "-i", str(src),
               "-t", limit, "-an", "-vf", scale, "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
               "-movflags", "+faststart", str(tmp)]
        if not shutil.which("nice"):
            cmd = cmd[3:]
        log.info("Preparing video %s for the screen", src.name)
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if r.returncode != 0 or not tmp.exists():
            tmp.unlink(missing_ok=True)
            raise RuntimeError(r.stderr.strip()[-300:] or "ffmpeg failed")
        tmp.replace(out)

    def _video_thumb(self, photo_id: str, src: Path) -> Path | None:
        out = self.cache_dir / f"{photo_id}-{int(src.stat().st_mtime)}-thumb.jpg"
        if out.exists():
            return out
        if not self.ffmpeg:
            return None
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        for seek in ("1", "0"):
            r = subprocess.run([self.ffmpeg, "-nostdin", "-loglevel", "error", "-y", "-ss", seek, "-i", str(src),
                                "-frames:v", "1", "-vf", f"scale={THUMB[0]}:-2", str(out)],
                               capture_output=True, timeout=60)
            if r.returncode == 0 and out.exists():
                return out
        return None

    @staticmethod
    def _fit_box(size: tuple[int, int], screen: tuple[int, int]) -> tuple[int, int]:
        """Big enough to fill the screen when cropped (the page uses background-size: cover)."""
        w, h = size
        sw, sh = screen
        scale = max(sw / w, sh / h)
        return (max(1, round(w * scale)), max(1, round(h * scale))) if scale < 1 else (w, h)
