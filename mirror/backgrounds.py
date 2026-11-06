"""Background photo slideshow: finds photos in folders and serves screen-sized copies.

Big camera photos (12-48 MP) are slow for the Pi's browser, so each photo is resized once
to the screen size, turned the right way up, and cached.
"""
from __future__ import annotations

import hashlib
import logging
import random
import threading
import time
from pathlib import Path

from PIL import Image, ImageOps

log = logging.getLogger(__name__)

try:  # iPhone photos (.heic)
    from pillow_heif import register_heif_opener

    register_heif_opener()
    HEIC = True
except ImportError:
    HEIC = False

EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"} | ({".heic", ".heif"} if HEIC else set())


class Backgrounds:
    def __init__(self, folders: list[Path], cache_dir: Path, size: tuple[int, int] = (1920, 1080),
                 skip_names: tuple[str, ...] = ("print.jpg",)):
        self.folders = folders
        self.cache_dir = cache_dir
        self.size = size
        self.skip_names = skip_names
        self._photos: dict[str, Path] = {}
        self._scanned_at = 0.0
        self._lock = threading.Lock()

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

    def list(self, max_age: float = 120) -> list[str]:
        """Photo ids in random order (rescans the folders every couple of minutes)."""
        with self._lock:
            if time.time() - self._scanned_at > max_age:
                self._scan()
            ids = list(self._photos)
        random.shuffle(ids)
        return ids

    def image_path(self, photo_id: str) -> Path | None:
        """Screen-sized JPEG for a photo id, made on first use."""
        with self._lock:
            src = self._photos.get(photo_id)
        if src is None or not src.exists():
            return None
        stamp = int(src.stat().st_mtime)
        out = self.cache_dir / f"{photo_id}-{stamp}-{self.size[0]}x{self.size[1]}.jpg"
        if not out.exists():
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            with Image.open(src) as im:
                im = ImageOps.exif_transpose(im).convert("RGB")
                im.thumbnail(self._fit_box(im.size), Image.LANCZOS)
                tmp = out.with_suffix(".tmp")
                im.save(tmp, "JPEG", quality=85)
                tmp.replace(out)
        return out

    def _fit_box(self, size: tuple[int, int]) -> tuple[int, int]:
        """Big enough to fill the screen when cropped (the page uses object-fit: cover)."""
        w, h = size
        sw, sh = self.size
        scale = max(sw / w, sh / h)
        return (max(1, round(w * scale)), max(1, round(h * scale))) if scale < 1 else (w, h)
