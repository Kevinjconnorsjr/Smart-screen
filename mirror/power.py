"""Screen sleep/wake: goes dark after a while with no gestures, optionally powers the monitor off."""
from __future__ import annotations

import datetime as dt
import logging
import shlex
import subprocess
import threading
import time
from typing import Callable

log = logging.getLogger(__name__)


def parse_hours(text: str | None) -> tuple[dt.time, dt.time] | None:
    """'07:00-22:30' -> (07:00, 22:30). Empty -> None."""
    if not text:
        return None
    start, end = (dt.time.fromisoformat(part.strip()) for part in str(text).split("-"))
    return start, end


def in_hours(hours: tuple[dt.time, dt.time] | None, now: dt.time) -> bool:
    if hours is None:
        return False
    start, end = hours
    return start <= now < end if start <= end else (now >= start or now < end)  # wraps past midnight


class ScreenPower:
    def __init__(self, cfg: dict, emit: Callable[[str, dict | None], None],
                 monotonic: Callable[[], float] = time.monotonic,
                 clock: Callable[[], dt.datetime] = dt.datetime.now):
        self.sleep_after = float(cfg.get("sleep_after_minutes", 0) or 0) * 60
        self.always_on = parse_hours(cfg.get("always_on_hours"))
        self.on_cmd = cfg.get("screen_on_command") or ""
        self.off_cmd = cfg.get("screen_off_command") or ""
        self.emit = emit
        self.monotonic = monotonic
        self.clock = clock
        self.asleep = False
        self.last_activity = monotonic()
        self._lock = threading.Lock()

    def activity(self) -> None:
        self.last_activity = self.monotonic()
        if self.asleep:
            self.wake()

    def wake(self) -> None:
        with self._lock:
            was_asleep, self.asleep = self.asleep, False
            self.last_activity = self.monotonic()
        if was_asleep:
            self._run(self.on_cmd)
        self.emit("wake", None)

    def sleep(self) -> None:
        with self._lock:
            if self.asleep:
                return
            self.asleep = True
        self.emit("sleep", None)
        self._run(self.off_cmd)

    def tick(self) -> None:
        if (not self.asleep and self.sleep_after > 0
                and self.monotonic() - self.last_activity >= self.sleep_after
                and not in_hours(self.always_on, self.clock().time())):
            self.sleep()

    def start(self) -> None:
        def loop():
            while True:
                time.sleep(5)
                try:
                    self.tick()
                except Exception:
                    log.exception("Screen power check failed")

        threading.Thread(target=loop, name="screen-power", daemon=True).start()

    @staticmethod
    def _run(cmd: str) -> None:
        if not cmd:
            return
        try:
            subprocess.run(shlex.split(cmd), timeout=10, check=False)
        except Exception as e:
            log.warning("Screen command %r failed: %s", cmd, e)
