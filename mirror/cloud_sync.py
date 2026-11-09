"""Copies photos and videos from a cloud folder (Google Drive, Dropbox, OneDrive…) to the Pi.

Uses rclone (https://rclone.org). Connect the account once on the Pi with
deploy/connect-google-drive.sh (read-only access: the Pi can never change your Drive).
Then every `every_minutes` the chosen folder is mirrored into backgrounds/cloud/: new files
are downloaded, and files removed from the cloud folder are removed from the Pi too.
"""
from __future__ import annotations

import datetime as dt
import logging
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

MEDIA = "{jpg,jpeg,png,webp,gif,heic,heif,mp4,mov,m4v,webm,mkv,3gp}"


class CloudSync:
    def __init__(self, cfg: Callable[[], dict], dest: Callable[[], Path], on_change: Callable[[], None]):
        self._cfg = cfg
        self._dest = dest
        self.on_change = on_change
        self.rclone = shutil.which("rclone")
        self.running = False
        self.last_run: str | None = None
        self.last_ok: bool | None = None
        self.last_error = ""
        self._wake = threading.Event()
        self._lock = threading.Lock()

    @property
    def cfg(self) -> dict:
        return self._cfg() or {}

    def remotes(self) -> list[str]:
        if not self.rclone:
            return []
        try:
            out = subprocess.run([self.rclone, "listremotes"], capture_output=True, text=True, timeout=20).stdout
        except Exception:
            return []
        return [line.strip().rstrip(":") for line in out.splitlines() if line.strip()]

    def status(self) -> dict:
        cfg = self.cfg
        remotes = self.remotes()
        dest = self._dest()
        files = sum(1 for p in dest.rglob("*") if p.is_file()) if dest.is_dir() else 0
        return {
            "installed": bool(self.rclone),
            "connected": cfg.get("remote", "gdrive") in remotes,
            "remotes": remotes,
            "enabled": bool(cfg.get("enabled")),
            "folder": cfg.get("folder", ""),
            "running": self.running,
            "last_run": self.last_run,
            "last_ok": self.last_ok,
            "last_error": self.last_error,
            "files": files,
        }

    def start(self) -> None:
        threading.Thread(target=self._loop, name="cloud-sync", daemon=True).start()

    def sync_now(self) -> None:
        self._wake.set()

    def _loop(self) -> None:
        self._wake.set()  # once at startup
        while True:
            minutes = max(5, int(self.cfg.get("every_minutes", 30) or 30))
            self._wake.wait(timeout=minutes * 60)
            self._wake.clear()
            if self.cfg.get("enabled"):
                self.run_once()

    def run_once(self) -> bool:
        cfg = self.cfg
        if not self.rclone:
            self._finish(False, "rclone isn't installed (run ./deploy/install.sh)")
            return False
        remote, folder = cfg.get("remote", "gdrive"), (cfg.get("folder") or "").strip("/")
        if remote not in self.remotes():
            self._finish(False, "Google Drive isn't connected yet: run ./deploy/connect-google-drive.sh on the Pi")
            return False
        with self._lock:
            self.running = True
            dest = self._dest()
            dest.mkdir(parents=True, exist_ok=True)
            cmd = [self.rclone, "sync", f"{remote}:{folder}", str(dest),
                   "--include", f"*.{MEDIA}", "--ignore-case",
                   "--max-size", f"{int(cfg.get('max_file_mb', 500))}M",
                   "--transfers", "2", "--checkers", "4", "--low-level-retries", "3"]
            if cfg.get("shared_with_me"):
                cmd.append("--drive-shared-with-me")
            log.info("Syncing %s:%s", remote, folder)
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=6 * 3600)
                ok, err = r.returncode == 0, (r.stderr.strip().splitlines() or [""])[-1][-300:]
            except Exception as e:
                ok, err = False, str(e)
            self.running = False
        self._finish(ok, "" if ok else (err or "Sync failed"))
        if ok:
            self.on_change()
        return ok

    def _finish(self, ok: bool, error: str) -> None:
        self.last_run = dt.datetime.now().isoformat(timespec="seconds")
        self.last_ok = ok
        self.last_error = error
        if not ok:
            log.warning("Cloud sync: %s", error)
