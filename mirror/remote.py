"""Remote control for the phone app: pairing, settings, photos, photo booth gallery, system.

Security model
- Requests from the Pi itself (the kiosk browser) are always allowed.
- Anything else on the network must be *paired*: the phone asks to pair, a 6-digit code appears
  on the screen for 2 minutes, and typing that code on the phone gives it a long random token
  (kept as a cookie). Only a hash of each token is stored on the Pi.
- Paired phones can be removed from the app (Settings -> Paired phones) or by deleting
  .secrets/remote_devices.json on the Pi.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from flask import Blueprint, Response, abort, jsonify, request, send_file, send_from_directory
from PIL import Image, ImageOps
from werkzeug.utils import secure_filename

from . import settings_store
from .backgrounds import EXTENSIONS
from .config import DEFAULTS_FILE, ROOT, load_config, resolve

log = logging.getLogger(__name__)

COOKIE = "ss_token"
CODE_SECONDS = 120
MAX_TRIES = 5
LOCAL_ADDRS = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}
STATIC = Path(__file__).resolve().parent / "static"

# Paths anyone on the network may open (needed to pair, or harmless page assets).
PUBLIC_PATHS = ("/remote", "/static/", "/api/pair/", "/manifest.webmanifest", "/favicon")


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Devices:
    """Paired phones, stored as token hashes in .secrets/remote_devices.json."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._devices: dict[str, dict] = {}
        if path.exists():
            try:
                self._devices = json.loads(path.read_text())
            except Exception:
                log.warning("Could not read %s; starting with no paired phones", path)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._devices, indent=2))
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)

    def add(self, name: str) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._devices[_hash(token)] = {
                "id": secrets.token_hex(4),
                "name": (name or "Phone")[:60],
                "paired": dt.datetime.now().isoformat(timespec="seconds"),
                "last_seen": dt.datetime.now().isoformat(timespec="seconds"),
            }
            self._save()
        return token

    def check(self, token: str | None) -> dict | None:
        if not token:
            return None
        h = _hash(token)
        with self._lock:
            for known, device in self._devices.items():
                if hmac.compare_digest(known, h):
                    now = dt.datetime.now()
                    if device.get("last_seen", "")[:13] != now.isoformat()[:13]:  # write at most hourly
                        device["last_seen"] = now.isoformat(timespec="seconds")
                        self._save()
                    return device
        return None

    def list(self) -> list[dict]:
        with self._lock:
            return [dict(d) for d in self._devices.values()]

    def remove(self, device_id: str) -> bool:
        with self._lock:
            for h, d in list(self._devices.items()):
                if d["id"] == device_id:
                    del self._devices[h]
                    self._save()
                    return True
        return False


class Pairing:
    """One pairing code at a time, shown on the screen, valid for 2 minutes, 5 tries."""

    def __init__(self, emit: Callable[[str, dict], None], clock: Callable[[], float] = time.monotonic):
        self.emit = emit
        self.clock = clock
        self.code: str | None = None
        self.expires = 0.0
        self.tries = 0
        self.last_start = -1e9
        self._lock = threading.Lock()

    def start(self) -> bool:
        with self._lock:
            now = self.clock()
            if now - self.last_start < 5:  # don't let anyone spam the screen
                return False
            self.last_start = now
            self.code = f"{secrets.randbelow(1_000_000):06d}"
            self.expires = now + CODE_SECONDS
            self.tries = 0
        self.emit("pair", {"code": self.code, "seconds": CODE_SECONDS})
        return True

    def finish(self, code: str) -> bool:
        with self._lock:
            if self.code is None or self.clock() > self.expires:
                return False
            self.tries += 1
            ok = hmac.compare_digest(re.sub(r"\D", "", code or ""), self.code)
            if ok or self.tries >= MAX_TRIES:
                self.code = None
        if ok or self.tries >= MAX_TRIES:
            self.emit("pair", {"code": None, "paired": ok})
        return ok


def _system_info() -> dict:
    info: dict = {}
    try:
        info["cpu_temp_c"] = round(int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000, 1)
    except Exception:
        info["cpu_temp_c"] = None
    try:
        info["uptime_hours"] = round(float(Path("/proc/uptime").read_text().split()[0]) / 3600, 1)
    except Exception:
        info["uptime_hours"] = None
    usage = shutil.disk_usage(ROOT)
    info["disk_free_gb"] = round(usage.free / 1e9, 1)
    info["disk_total_gb"] = round(usage.total / 1e9, 1)
    try:
        info["commit"] = subprocess.run(["git", "log", "-1", "--format=%h %cd", "--date=format:%b %d %H:%M"],
                                        cwd=ROOT, capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        info["commit"] = ""
    return info


def create_remote(app_state) -> Blueprint:
    """`app_state` gives access to cfg, the event bus, backgrounds, the mirror and helpers (see web.py)."""
    bp = Blueprint("remote", __name__)
    st = app_state
    devices = Devices(st.secrets_dir / "remote_devices.json")
    pairing = Pairing(st.bus.emit)
    st.devices, st.pairing = devices, pairing

    def is_local() -> bool:
        return (request.remote_addr or "") in LOCAL_ADDRS

    def token() -> str | None:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth[7:].strip()
        return request.cookies.get(COOKIE)

    @bp.before_app_request
    def require_pairing():
        if is_local() or request.path.startswith(PUBLIC_PATHS):
            return None
        if devices.check(token()) is None:
            return jsonify({"error": "not_paired"}), 401
        return None

    # ---------- pairing ----------
    @bp.post("/api/pair/start")
    def pair_start():
        st.mirror.power.activity()  # wake the screen so the code can be seen
        if not pairing.start():
            return jsonify({"ok": False, "error": "Wait a few seconds and try again"}), 429
        return jsonify({"ok": True, "seconds": CODE_SECONDS})

    @bp.post("/api/pair/finish")
    def pair_finish():
        body = request.get_json(silent=True) or {}
        if not pairing.finish(str(body.get("code", ""))):
            return jsonify({"ok": False, "error": "That code didn't match. Ask for a new one."}), 403
        tok = devices.add(str(body.get("name") or request.user_agent.string[:40] or "Phone"))
        resp = jsonify({"ok": True, "token": tok})
        resp.set_cookie(COOKIE, tok, max_age=10 * 365 * 86400, httponly=True, samesite="Lax")
        return resp

    @bp.get("/api/pair/status")
    def pair_status():
        # Also tells the phone app "this is a Smart Screen" when it scans the Wi-Fi for one.
        # The name matches the mDNS announcement in deploy/smart-screen.avahi: "<name> (<hostname>)".
        name = (st.cfg.get("remote") or {}).get("name") or "Smart Screen"
        return jsonify({"paired": is_local() or devices.check(token()) is not None,
                        "app": "smart-screen", "name": f"{name} ({socket.gethostname()})"})

    # ---------- the phone page ----------
    @bp.get("/remote")
    def remote_page():
        return send_from_directory(STATIC / "remote", "index.html")

    @bp.get("/manifest.webmanifest")
    def manifest():
        return send_from_directory(STATIC / "remote", "manifest.webmanifest", mimetype="application/manifest+json")

    # ---------- status & system ----------
    @bp.get("/api/remote/status")
    def status():
        return jsonify({
            "asleep": st.mirror.power.asleep,
            "lights_on": st.mirror.lights.is_on,
            "booth_busy": bool(st.mirror.booth and st.mirror.booth.busy),
            "camera": st.camera is not None,
            "problems": st.problems,
            "version": st.version,
            "actions": list(st.mirror.actions),
            **_system_info(),
        })

    @bp.post("/api/remote/restart")
    def restart():
        st.restart_soon()
        return jsonify({"ok": True})

    @bp.post("/api/remote/update")
    def update():
        # Allowed without a password by /etc/sudoers.d/smart-screen (see deploy/install.sh).
        try:
            r = subprocess.run(["sudo", "-n", "systemctl", "start", "--no-block", "smart-screen-update.service"],
                               capture_output=True, text=True, timeout=15)
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500
        if r.returncode != 0:
            return jsonify({"ok": False, "error": r.stderr.strip() or "Update check failed"}), 500
        return jsonify({"ok": True})

    @bp.get("/api/remote/devices")
    def device_list():
        mine = devices.check(token())
        return jsonify([{**d, "this_phone": bool(mine and mine["id"] == d["id"])} for d in devices.list()])

    @bp.delete("/api/remote/devices/<device_id>")
    def device_remove(device_id: str):
        return jsonify({"ok": devices.remove(device_id)})

    # ---------- settings ----------
    @bp.get("/api/remote/config")
    def config_get():
        return jsonify({
            "config": settings_store.masked(st.cfg),
            "locked": [".".join(p) for p in settings_store.LOCKED_KEYS],
            "actions": list(st.mirror.actions),
            "gestures": sorted(settings_store.GESTURES),
        })

    @bp.put("/api/remote/config")
    def config_put():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"ok": False, "error": "Expected settings"}), 400
        defaults = load_config(DEFAULTS_FILE)  # the example file = the list of known settings
        try:
            new = settings_store.validate(body, st.cfg, defaults, list(st.mirror.actions))
            paths = settings_store.save(st.config_path, DEFAULTS_FILE, st.cfg, new)
        except settings_store.SettingsError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        restart_needed = settings_store.needs_restart(paths)
        settings_store.apply_live(st.cfg, new)
        st.on_settings_changed()
        if restart_needed:
            st.restart_soon()
        return jsonify({"ok": True, "changed": [".".join(p) for p in paths],
                        "applied": "restart" if restart_needed else ("live" if paths else "unchanged")})

    # ---------- background photos ----------
    def upload_dir() -> Path:
        folders = st.cfg.get("slideshow", {}).get("folders") or ["backgrounds"]
        return resolve(folders[0]) / "from-phone"

    @bp.get("/api/remote/backgrounds")
    def bg_list():
        items = []
        for pid in st.backgrounds.list(max_age=0, shuffle=False):
            path = st.backgrounds.source(pid)
            if path is None or st.photos_dir in path.parents:
                continue  # photo booth pictures are in their own tab
            items.append({"id": pid, "name": path.name, "folder": path.parent.name,
                          "modified": int(path.stat().st_mtime)})
        items.sort(key=lambda i: -i["modified"])
        return jsonify(items)

    @bp.get("/api/remote/thumb/<photo_id>.jpg")
    def bg_thumb(photo_id: str):
        path = st.backgrounds.image_path(photo_id, thumb=True)
        if path is None:
            abort(404)
        return send_file(path, mimetype="image/jpeg", max_age=86400)

    @bp.post("/api/remote/backgrounds")
    def bg_upload():
        files = request.files.getlist("photos")
        if not files:
            return jsonify({"ok": False, "error": "No photos received"}), 400
        folder = upload_dir()
        folder.mkdir(parents=True, exist_ok=True)
        saved = []
        for f in files:
            name = secure_filename(f.filename or "") or "photo.jpg"
            stem, ext = os.path.splitext(name)
            if ext.lower() not in EXTENSIONS:
                continue
            target = folder / name
            n = 1
            while target.exists():
                target = folder / f"{stem}-{n}{ext}"
                n += 1
            f.save(target)
            try:
                with Image.open(target) as im:
                    im.verify()
            except Exception:
                target.unlink(missing_ok=True)
                continue
            saved.append(target.name)
        st.backgrounds.list(max_age=0)
        return jsonify({"ok": bool(saved), "saved": saved,
                        **({} if saved else {"error": "None of those files were photos"})})

    @bp.delete("/api/remote/backgrounds/<photo_id>")
    def bg_delete(photo_id: str):
        path = st.backgrounds.source(photo_id)
        if path is None or st.photos_dir in path.parents:
            abort(404)
        path.unlink(missing_ok=True)
        st.backgrounds.list(max_age=0)
        return jsonify({"ok": True})

    # ---------- photo booth gallery ----------
    def session_dir(session: str) -> Path:
        if not re.fullmatch(r"\d{8}-\d{6}", session):
            abort(404)
        d = st.photos_dir / session
        if not d.is_dir():
            abort(404)
        return d

    @bp.get("/api/remote/booth")
    def booth_list():
        sessions = []
        if st.photos_dir.is_dir():
            for d in sorted(st.photos_dir.iterdir(), reverse=True):
                if d.is_dir() and re.fullmatch(r"\d{8}-\d{6}", d.name):
                    when = dt.datetime.strptime(d.name, "%Y%m%d-%H%M%S")
                    sessions.append({
                        "id": d.name,
                        "time": when.isoformat(),
                        "shots": sorted(p.name for p in d.glob("shot*.jpg")),
                        "has_print": (d / "print.jpg").exists(),
                    })
        return jsonify(sessions[:200])

    @bp.get("/api/remote/booth/<session>/<name>")
    def booth_file(session: str, name: str):
        d = session_dir(session)
        if not re.fullmatch(r"(shot\d+|print)\.jpg", name):
            abort(404)
        thumb = request.args.get("thumb")
        path = d / name
        if not path.exists():
            abort(404)
        if thumb:
            cache = st.backgrounds.cache_dir / f"booth-{session}-{name}"
            if not cache.exists():
                cache.parent.mkdir(parents=True, exist_ok=True)
                with Image.open(path) as im:
                    im = ImageOps.exif_transpose(im).convert("RGB")
                    im.thumbnail((480, 480))
                    im.save(cache, "JPEG", quality=80)
            path = cache
        as_download = bool(request.args.get("download"))
        return send_file(path, mimetype="image/jpeg", as_attachment=as_download,
                         download_name=f"photobooth-{session}-{name}")

    @bp.post("/api/remote/booth/<session>/print")
    def booth_print(session: str):
        from .photobooth import print_file

        d = session_dir(session)
        if not (d / "print.jpg").exists():
            abort(404)
        booth = st.cfg.get("photobooth", {})
        ok = print_file(d / "print.jpg", booth.get("printer", ""), booth.get("print_options"),
                        int((request.get_json(silent=True) or {}).get("copies", 1) or 1))
        return jsonify({"ok": ok, **({} if ok else {"error": "Printing failed - is the printer on?"})})

    @bp.delete("/api/remote/booth/<session>")
    def booth_delete(session: str):
        shutil.rmtree(session_dir(session))
        st.backgrounds.list(max_age=0)
        return jsonify({"ok": True})

    return bp
