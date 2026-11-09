"""The screen: a full-screen web page fed by Server-Sent Events, plus JSON APIs and the phone remote."""
from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from flask import Flask, Response, abort, jsonify, request, send_file, send_from_directory

from .backgrounds import Backgrounds
from .calendar_feed import CalendarFeed
from .config import ROOT, resolve
from .news import News
from .remote import create_remote

log = logging.getLogger(__name__)
STATIC = Path(__file__).resolve().parent / "static"


class EventBus:
    """Fan-out of events to every open page (the kiosk browser, and phone previews)."""

    def __init__(self):
        self._subscribers: list[queue.Queue] = []
        self._lock = threading.Lock()

    def emit(self, event_type: str, data: dict | None = None) -> None:
        msg = f"event: {event_type}\ndata: {json.dumps(data or {})}\n\n"
        with self._lock:
            for q in self._subscribers:
                q.put(msg)

    def stream(self):
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._subscribers.append(q)
        try:
            yield "retry: 2000\n\n"
            while True:
                try:
                    yield q.get(timeout=15)
                except queue.Empty:
                    yield ": keep-alive\n\n"
        finally:
            with self._lock:
                self._subscribers.remove(q)


def _restart_soon() -> None:
    """Exit shortly; systemd (Restart=on-failure) starts us again with the new settings."""
    def go():
        time.sleep(1.0)
        log.info("Restarting to apply new settings")
        os._exit(3)

    threading.Thread(target=go, daemon=True).start()


def create_app(cfg: dict, bus: EventBus, photos_dir: Path, mirror, camera=None, version: str = "dev",
               config_path: Path | None = None, secrets_dir: Path | None = None, restart_soon=_restart_soon) -> Flask:
    app = Flask(__name__, static_folder=str(STATIC), static_url_path="/static")
    app.config["MAX_CONTENT_LENGTH"] = 300 * 1024 * 1024  # photo uploads from the phone

    def section(name: str) -> dict:
        return cfg.get(name) or {}

    def folders() -> list[Path]:
        out = [resolve(f) for f in section("slideshow").get("folders") or []]
        if section("slideshow").get("include_booth_photos", True):
            out.append(photos_dir)
        return out

    def screen_size() -> tuple[int, int]:
        size = section("slideshow").get("size") or [1920, 1080]
        return int(size[0]), int(size[1])

    backgrounds = Backgrounds(folders, resolve(section("slideshow").get("cache_dir", ".cache/backgrounds")), screen_size)
    calendar = CalendarFeed(cfg.setdefault("calendar", {}))
    news = News(cfg.setdefault("news", {}))

    def on_settings_changed() -> None:
        calendar._fetched_at = 0  # fetch again with the new calendar list
        news._fetched_at = 0
        backgrounds.list(max_age=0)
        bus.emit("settings", {})

    state = SimpleNamespace(
        cfg=cfg, bus=bus, mirror=mirror, camera=camera, version=version, photos_dir=photos_dir,
        backgrounds=backgrounds, config_path=config_path or ROOT / "config.yaml",
        secrets_dir=secrets_dir or ROOT / ".secrets", restart_soon=restart_soon,
        on_settings_changed=on_settings_changed,
    )
    app.register_blueprint(create_remote(state))
    app.extensions["smart_screen"] = state

    @app.get("/")
    def index():
        return send_from_directory(STATIC, "index.html")

    @app.get("/events")
    def events():
        return Response(bus.stream(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/settings")
    def settings():
        display = section("display")
        slideshow = section("slideshow")
        return jsonify({
            "version": version,
            "style": display.get("style", "photos"),
            "layout": display.get("layout", "left"),
            "theme": display.get("theme") or {},
            "show": display.get("show") or {},
            "clock_24h": bool(display.get("clock_24h", False)),
            "show_seconds": bool(display.get("show_seconds", False)),
            "greeting": display.get("greeting", ""),
            "messages": display.get("messages") or [],
            "weather": display.get("weather"),
            "seconds_per_photo": int(slideshow.get("seconds_per_photo", 30)),
            "camera": camera is not None,
            "actions": list(mirror.actions),
            "bindings": section("gestures").get("bindings") or {},
        })

    @app.get("/api/calendar")
    def calendar_events():
        return jsonify(calendar.upcoming())

    @app.get("/api/news")
    def headlines():
        return jsonify(news.headlines())

    @app.get("/api/backgrounds")
    def background_list():
        return jsonify(backgrounds.list())

    @app.get("/bg/<photo_id>.jpg")
    def background(photo_id: str):
        path = backgrounds.image_path(photo_id)
        if path is None:
            abort(404)
        return send_file(path, mimetype="image/jpeg", max_age=86400)

    @app.get("/stream.mjpg")
    def stream():
        if camera is None:
            abort(404)
        return Response(camera.mjpeg(mirror=bool(section("photobooth").get("mirror_preview", True))),
                        mimetype="multipart/x-mixed-replace; boundary=frame")

    @app.post("/api/action/<name>")
    def action(name: str):
        if name not in mirror.actions:
            abort(404)
        return jsonify({"ok": mirror.run(name)})

    @app.get("/photos/<path:path>")
    def photos(path: str):
        return send_from_directory(photos_dir, path)

    @app.errorhandler(413)
    def too_big(_e):
        return jsonify({"ok": False, "error": "Too many photos at once - try fewer"}), 413

    return app
