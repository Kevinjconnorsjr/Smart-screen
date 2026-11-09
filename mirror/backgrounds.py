"""Background photo slideshow: finds photos in folders and serves screen-sized copies.

Big camera photos (12-48 MP) are slow for the Pi's browser, so each photo is resized once
to the screen size, turned the right way up, and cached. Small thumbnails are made the same
way for the phone app.
"""
from __future__ import annotations

import hashlib
import logging
import random
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

EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"} | ({".heic", ".heif"} if HEIC else set())
THUMB = (400, 400)


class Backgrounds:
    def __init__(self, folders: list[Path] | Callable[[], list[Path]], cache_dir: Path,
                 size: tuple[int, int] | Callable[[], tuple[int, int]] = (1920, 1080),
                 skip_names: tuple[str, ...] = ("print.jpg",)):
        self._folders = folders
        self.cache_dir = cache_dir
        self._size = size
        self.skip_names = skip_names
        self._photos: dict[str, Path] = {}
        self._scanned_at = 0.0
        self._lock = threading.Lock()

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

    def list(self, max_age: float = 120, shuffle: bool = True) -> list[str]:
        """Photo ids (random order by default). Rescans the folders every couple of minutes."""
        with self._lock:
            if time.time() - self._scanned_at > max_age:
                self._scan()
            ids = list(self._photos)
        if shuffle:
            random.shuffle(ids)
        return ids

    def source(self, photo_id: str) -> Path | None:
        with self._lock:
            path = self._photos.get(photo_id)
        return path if path is not None and path.exists() else None

    def image_path(self, photo_id: str, thumb: bool = False) -> Path | None:
        """Screen-sized (or thumbnail) JPEG for a photo id, made on first use."""
        src = self.source(photo_id)
        if src is None:
            return None
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

    @staticmethod
    def _fit_box(size: tuple[int, int], screen: tuple[int, int]) -> tuple[int, int]:
        """Big enough to fill the screen when cropped (the page uses background-size: cover)."""
        w, h = size
        sw, sh = screen
        scale = max(sw / w, sh / h)
        return (max(1, round(w * scale)), max(1, round(h * scale))) if scale < 1 else (w, h)
