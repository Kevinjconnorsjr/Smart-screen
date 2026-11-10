"""Blink cameras: pictures, motion pop-ups, and (experimental) live view.

Uses blinkpy (https://github.com/fronzbot/blinkpy), the library Home Assistant uses for Blink.
- Sign in once from the phone app with your Blink email and password, then the code Blink emails or
  texts you. Only Blink's access keys are kept on the Pi (.secrets/blink.json), never your password.
  If Blink ever expires them, the app asks you to sign in again.
- Every `check_every_seconds` (60 by default, as the library recommends) the Pi asks Blink for news.
  When a camera has recorded motion, its new picture pops up on the screen. Blink only records
  motion while the system is armed (in the Blink app).
- "New picture" asks a camera to take a fresh photo. Battery cameras use some battery each time.
- Live view uses Blink's own (unofficial) live stream protocol through blinkpy. It's experimental:
  it may stop working if Blink changes something, and it drains battery cameras quickly.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)


class BlinkService:
    def __init__(self, cfg: dict, secrets_file: Path, emit: Callable[[str, dict], None],
                 on_motion: Callable[[], None] | None = None):
        self.cfg = cfg                       # the live settings (blink: section read on use)
        self.secrets_file = secrets_file
        self.emit = emit
        self.on_motion = on_motion
        self.state = "signed_out"            # signed_out | needs_code | signed_in | error | unavailable
        self.error = ""
        self.blink = None
        self.images: dict[str, bytes] = {}   # camera name -> newest picture
        self.last_records: dict[str, str] = {}
        self.motion_at: dict[str, str] = {}
        self._loop = asyncio.new_event_loop()
        self._lives: dict[str, object] = {}
        try:
            import blinkpy  # noqa: F401
        except ImportError:
            self.state, self.error = "unavailable", "blinkpy isn't installed (run ./deploy/install.sh)"

    # ---------- running coroutines on the Blink thread ----------
    def start(self) -> None:
        threading.Thread(target=self._loop.run_forever, name="blink", daemon=True).start()
        if self.state != "unavailable" and self.secrets_file.exists():
            self._submit(self._resume())
        threading.Thread(target=self._poll_forever, name="blink-poll", daemon=True).start()

    def _submit(self, coro, timeout: float = 60):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    @property
    def settings(self) -> dict:
        return self.cfg.get("blink") or {}

    # ---------- sign in ----------
    async def _new_blink(self, login: dict):
        from blinkpy.auth import Auth
        from blinkpy.blinkpy import Blink

        blink = Blink(refresh_rate=30, motion_interval=2)
        blink.auth = Auth(login, no_prompt=True)
        return blink

    async def _resume(self) -> None:
        from blinkpy.auth import BlinkTwoFARequiredError

        try:
            self.blink = await self._new_blink(json.loads(self.secrets_file.read_text()))
            ok = await self.blink.start()
        except BlinkTwoFARequiredError:
            self.state, self.error = "signed_out", "Blink wants you to sign in again"
            return
        except Exception as e:
            ok, self.error = False, str(e)
        if ok:
            self.state, self.error = "signed_in", ""
            await self._save()
            log.info("Blink: signed in, cameras: %s", ", ".join(self.blink.cameras))
        else:
            self.state = "signed_out"
            self.error = self.error or "Blink sign-in expired. Sign in again in the app."

    def login(self, username: str, password: str) -> str:
        return self._submit(self._login(username, password))

    async def _login(self, username: str, password: str) -> str:
        from blinkpy.auth import BlinkTwoFARequiredError

        self.blink = await self._new_blink({"username": username.strip(), "password": password})
        try:
            ok = await self.blink.start()
        except BlinkTwoFARequiredError:
            self.state, self.error = "needs_code", ""
            return self.state
        except Exception as e:
            self.state, self.error = "error", f"Blink sign-in failed: {e}"
            return self.state
        if not ok:
            self.state, self.error = "error", "Blink didn't accept that email and password"
            return self.state
        self.state, self.error = "signed_in", ""
        await self._save()
        return self.state

    def verify(self, code: str) -> str:
        return self._submit(self._verify(code))

    async def _verify(self, code: str) -> str:
        if self.blink is None or self.state != "needs_code":
            self.error = "Sign in with your email and password first"
            return self.state
        try:
            ok = await self.blink.send_2fa_code(code.strip())
        except Exception as e:
            ok, self.error = False, str(e)
        if not ok:
            self.error = self.error or "That code didn't work. Check the newest email or text from Blink."
            return self.state
        self.state, self.error = "signed_in", ""
        await self._save()
        return self.state

    async def _save(self) -> None:
        data = dict(self.blink.auth.login_attributes)
        data.pop("password", None)  # the access keys are enough; never keep the password
        self.secrets_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.secrets_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        os.chmod(tmp, 0o600)
        tmp.replace(self.secrets_file)

    def logout(self) -> None:
        self.secrets_file.unlink(missing_ok=True)
        self.blink, self.state, self.error = None, "signed_out", ""
        self.images.clear()

    # ---------- cameras ----------
    def camera_names(self) -> list[str]:
        if self.state != "signed_in" or self.blink is None:
            return []
        return sorted(self.blink.cameras.keys())

    def status(self) -> dict:
        cams = []
        for name in self.camera_names():
            c = self.blink.cameras[name]
            cams.append({"name": name, "battery": getattr(c, "battery", None), "armed": getattr(c, "arm", None),
                         "last_motion": self.motion_at.get(name), "has_picture": name in self.images})
        return {"state": self.state, "error": self.error, "cameras": cams}

    def picture(self, name: str) -> bytes | None:
        if name not in self.images and self.state == "signed_in" and name in self.camera_names():
            try:
                self._submit(self._fetch_picture(name), timeout=30)
            except Exception as e:
                log.warning("Blink picture for %s failed: %s", name, e)
        return self.images.get(name)

    async def _fetch_picture(self, name: str) -> None:
        cam = self.blink.cameras[name]
        if not cam.image_from_cache:
            response = await cam.get_media()
            if response and response.status == 200:
                cam._cached_image = await response.read()
        if cam.image_from_cache:
            self.images[name] = cam.image_from_cache

    def snap(self, name: str) -> None:
        """Ask a camera for a new picture (takes a few seconds)."""
        self._submit(self._snap(name), timeout=60)

    async def _snap(self, name: str) -> None:
        cam = self.blink.cameras[name]
        await cam.snap_picture()
        await asyncio.sleep(4)  # give the camera time to upload it
        await self.blink.refresh(force_cache=True)
        await self._fetch_picture(name)
        self.emit("blink", {"camera": name, "kind": "picture"})

    def live_url(self, name: str) -> str:
        """Start a live view; returns a local tcp:// address that ffmpeg can read."""
        return self._submit(self._live(name), timeout=40)

    async def _live(self, name: str) -> str:
        cam = self.blink.cameras[name]
        stream = await cam.init_livestream()
        await stream.start(host="127.0.0.1", port=0)
        self._loop.create_task(stream.feed())
        self._lives[name] = stream
        return stream.url

    # ---------- motion ----------
    def _poll_forever(self) -> None:
        time.sleep(15)
        while True:
            every = max(30, int(self.settings.get("check_every_seconds", 60) or 60))
            if self.state == "signed_in":
                try:
                    for name in self._submit(self._check_motion(), timeout=120):
                        self._motion(name)
                except Exception as e:
                    log.warning("Blink check failed: %s", e)
            time.sleep(every)

    async def _check_motion(self) -> list[str]:
        await self.blink.refresh()
        new = []
        for name, cam in self.blink.cameras.items():
            record = cam.last_record
            if cam.motion_detected and record and record != self.last_records.get(name):
                if name in self.last_records:  # not the first look after starting
                    new.append(name)
                    await self._fetch_picture(name)
            if record:
                self.last_records[name] = record
        return new

    def _motion(self, name: str) -> None:
        now = dt.datetime.now()
        self.motion_at[name] = now.isoformat(timespec="seconds")
        log.info("Blink motion: %s", name)
        if not self.settings.get("motion_popups", True):
            return
        if self.on_motion:
            self.on_motion()  # wakes the screen
        self.emit("blink_motion", {"camera": name, "time": now.strftime("%-I:%M %p"),
                                   "seconds": int(self.settings.get("popup_seconds", 20) or 20)})
