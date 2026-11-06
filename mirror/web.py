"""The screen: a full-screen web page fed by Server-Sent Events, plus small JSON APIs."""
from __future__ import annotations

import json
import queue
import threading
from pathlib import Path
from typing import Callable

from flask import Flask, Response, abort, jsonify, send_file, send_from_directory

from .backgrounds import Backgrounds
from .calendar_feed import CalendarFeed
from .config import resolve
from .news import News

STATIC = Path(__file__).resolve().parent / "static"


class EventBus:
    """Fan-out of events to every open page (usually just the kiosk browser)."""

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


def create_app(cfg: dict, bus: EventBus, photos_dir: Path, run_action: Callable[[str], bool],
               actions: list[str], camera=None, version: str = "dev") -> Flask:
    app = Flask(__name__, static_folder=str(STATIC), static_url_path="/static")
    display = cfg.get("display", {})
    slideshow = cfg.get("slideshow", {}) or {}
    booth_cfg = cfg.get("photobooth", {})

    folders = [resolve(f) for f in slideshow.get("folders") or []]
    if slideshow.get("include_booth_photos", True):
        folders.append(photos_dir)
    size = slideshow.get("size") or [1920, 1080]
    backgrounds = Backgrounds(folders, resolve(slideshow.get("cache_dir", ".cache/backgrounds")), (int(size[0]), int(size[1])))
    calendar = CalendarFeed(cfg.get("calendar"))
    news = News(cfg.get("news"))

    @app.get("/")
    def index():
        return send_from_directory(STATIC, "index.html")

    @app.get("/events")
    def events():
        return Response(bus.stream(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/settings")
    def settings():
        return jsonify({
            "version": version,
            "style": display.get("style", "photos"),
            "clock_24h": bool(display.get("clock_24h", False)),
            "show_seconds": bool(display.get("show_seconds", False)),
            "greeting": display.get("greeting", ""),
            "messages": display.get("messages") or [],
            "weather": display.get("weather"),
            "seconds_per_photo": int(slideshow.get("seconds_per_photo", 30)),
            "camera": camera is not None,
            "actions": actions,
            "bindings": cfg.get("gestures", {}).get("bindings") or {},
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
        return Response(camera.mjpeg(mirror=bool(booth_cfg.get("mirror_preview", True))),
                        mimetype="multipart/x-mixed-replace; boundary=frame")

    @app.post("/api/action/<name>")
    def action(name: str):
        if name not in actions:
            abort(404)
        return jsonify({"ok": run_action(name)})

    @app.get("/photos/<path:path>")
    def photos(path: str):
        return send_from_directory(photos_dir, path)

    return app
