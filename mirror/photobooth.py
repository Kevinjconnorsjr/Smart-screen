"""Photo booth: countdown, take N shots, lay them out on 4x6 paper, print with CUPS."""
from __future__ import annotations

import datetime as dt
import logging
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw, ImageFont, ImageOps

log = logging.getLogger(__name__)

DPI = 300
PAPER_LONG, PAPER_SHORT = 6 * DPI, 4 * DPI  # 4x6 inch photo paper
BG = (255, 255, 255)
INK = (30, 30, 30)


def _font(size: int) -> ImageFont.ImageFont:
    for name in ("DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default(size=size)


def _caption(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], text: str, size: int) -> None:
    x0, y0, x1, y1 = box
    font = _font(size)
    for line, y in ((text, y0 + (y1 - y0) * 0.38), (dt.date.today().strftime("%B %d, %Y"), y0 + (y1 - y0) * 0.75)):
        if line:
            draw.text(((x0 + x1) / 2, y), line, fill=INK, font=font, anchor="mm")
        font = _font(int(size * 0.7))


def layout_strip(photos: list[Image.Image], caption: str = "") -> Image.Image:
    """Two identical 2x6 strips side by side on a 4x6 sheet (cut down the middle)."""
    sheet = Image.new("RGB", (PAPER_SHORT, PAPER_LONG), BG)
    strip_w, margin, gap, caption_h = PAPER_SHORT // 2, 36, 22, 170
    n = max(1, len(photos))
    cell_w = strip_w - 2 * margin
    cell_h = (PAPER_LONG - 2 * margin - caption_h - gap * (n - 1)) // n
    strip = Image.new("RGB", (strip_w, PAPER_LONG), BG)
    for i, photo in enumerate(photos):
        strip.paste(ImageOps.fit(photo, (cell_w, cell_h)), (margin, margin + i * (cell_h + gap)))
    _caption(ImageDraw.Draw(strip), (0, PAPER_LONG - caption_h - margin, strip_w, PAPER_LONG), caption, 34)
    sheet.paste(strip, (0, 0))
    sheet.paste(strip, (strip_w, 0))
    return sheet


def layout_grid(photos: list[Image.Image], caption: str = "") -> Image.Image:
    """Up to four photos in a 2x2 grid on a landscape 4x6 sheet."""
    sheet = Image.new("RGB", (PAPER_LONG, PAPER_SHORT), BG)
    margin, gap, caption_h = 40, 24, 130
    cell_w = (PAPER_LONG - 2 * margin - gap) // 2
    cell_h = (PAPER_SHORT - 2 * margin - gap - caption_h) // 2
    for i, photo in enumerate(photos[:4]):
        r, c = divmod(i, 2)
        sheet.paste(ImageOps.fit(photo, (cell_w, cell_h)), (margin + c * (cell_w + gap), margin + r * (cell_h + gap)))
    _caption(ImageDraw.Draw(sheet), (0, PAPER_SHORT - caption_h - margin // 2, PAPER_LONG, PAPER_SHORT), caption, 44)
    return sheet


def layout_single(photos: list[Image.Image], caption: str = "") -> Image.Image:
    sheet = Image.new("RGB", (PAPER_LONG, PAPER_SHORT), BG)
    margin, caption_h = 40, 120
    sheet.paste(ImageOps.fit(photos[-1], (PAPER_LONG - 2 * margin, PAPER_SHORT - 2 * margin - caption_h)), (margin, margin))
    _caption(ImageDraw.Draw(sheet), (0, PAPER_SHORT - caption_h - margin // 2, PAPER_LONG, PAPER_SHORT), caption, 44)
    return sheet


LAYOUTS = {"strip": layout_strip, "grid": layout_grid, "single": layout_single}


def print_file(path: Path, printer: str = "", options: list[str] | None = None, copies: int = 1) -> bool:
    lp = shutil.which("lp")
    if not lp:
        log.warning("`lp` not found; install CUPS to print (sudo apt install cups)")
        return False
    cmd = [lp, "-n", str(copies), *(options or [])]
    if printer:
        cmd += ["-d", printer]
    cmd.append(str(path))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log.error("Printing failed: %s", result.stderr.strip())
        return False
    log.info("Sent to printer: %s", result.stdout.strip())
    return True


class PhotoBooth:
    """Runs one session at a time. `emit(event_type, data)` drives the mirror screen."""

    def __init__(self, camera, cfg: dict, output_dir: Path, emit: Callable[[str, dict], None]):
        self.camera = camera
        self.cfg = cfg
        self.output_dir = output_dir
        self.emit = emit
        self._busy = threading.Lock()

    @property
    def busy(self) -> bool:
        return self._busy.locked()

    def start(self, on_done: Callable[[], None] | None = None) -> bool:
        if not self._busy.acquire(blocking=False):
            return False

        def run():
            try:
                self._session()
            except Exception:
                log.exception("Photo booth session failed")
                self.emit("booth", {"active": False})
                self.emit("status", {"text": "Photo booth error - check the logs"})
            finally:
                self._busy.release()
                if on_done:
                    on_done()

        threading.Thread(target=run, name="photobooth", daemon=True).start()
        return True

    def _session(self) -> None:
        cfg = self.cfg
        shots = int(cfg.get("shots", 4)) if cfg.get("layout", "strip") != "single" else 1
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        folder = self.output_dir / stamp
        folder.mkdir(parents=True, exist_ok=True)

        photos: list[Image.Image] = []
        self.emit("booth", {"active": True})
        for i in range(shots):
            self.emit("status", {"text": f"Photo {i + 1} of {shots}"})
            for n in range(int(cfg.get("countdown_seconds", 3)), 0, -1):
                self.emit("countdown", {"n": n})
                time.sleep(1)
            photos.append(self._capture())
            photos[-1].save(folder / f"shot{i + 1}.jpg", quality=95)
            if i < shots - 1:
                time.sleep(float(cfg.get("seconds_between_shots", 2)))

        self.emit("booth", {"active": False})
        self.emit("status", {"text": "Developing..."})
        sheet = LAYOUTS.get(cfg.get("layout", "strip"), layout_strip)(photos, cfg.get("caption", ""))
        sheet_path = folder / "print.jpg"
        sheet.save(sheet_path, quality=95, dpi=(DPI, DPI))
        self.emit("photo", {"url": f"/photos/{stamp}/print.jpg"})

        if cfg.get("print", True):
            self.emit("status", {"text": "Printing..."})
            ok = print_file(sheet_path, cfg.get("printer", ""), cfg.get("print_options"), int(cfg.get("copies", 1)))
            self.emit("status", {"text": "Printing! Grab it from the printer" if ok else "Saved (printing failed)"})
        else:
            self.emit("status", {"text": "Saved!"})

    def _capture(self) -> Image.Image:
        flash = bool(self.cfg.get("screen_flash", True))
        if flash:
            self.emit("flash", {"on": True})
            time.sleep(0.35)  # let the screen light up the room and the camera's exposure adjust
        got = self.camera.latest(newer_than=time.monotonic(), timeout=2.0)
        if flash:
            self.emit("flash", {"on": False})
        if got is None:
            raise RuntimeError("No frame from the camera")
        image = Image.fromarray(got[0])
        return ImageOps.mirror(image) if self.cfg.get("mirror_photos", False) else image
