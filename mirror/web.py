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
from .cameras import Cameras
from .cloud_sync import CloudSync
from .config import ROOT, resolve
from .screen import apply_rotation, is_portrait, transform
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
               config_path: Path | None = None, secrets_dir: Path | None = None, restart_soon=_restart_soon,
               problems: list[str] | None = None, start_background: bool = False, alexa=None,
               custom=None, teacher=None, blink=None) -> Flask:
    app = Flask(__name__, static_folder=str(STATIC), static_url_path="/static")
    app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 ** 3  # photo and video uploads from the phone

    def section(name: str) -> dict:
        return cfg.get(name) or {}

    def folders() -> list[Path]:
        out = [resolve(f) for f in section("slideshow").get("folders") or []]
        if section("slideshow").get("include_booth_photos", True):
            out.append(photos_dir)
        return out

    def screen_size() -> tuple[int, int]:
        size = section("slideshow").get("size") or [1920, 1080]
        long, short = max(int(size[0]), int(size[1])), min(int(size[0]), int(size[1]))
        return (short, long) if is_portrait(cfg) else (long, short)

    def first_folder() -> Path:
        return resolve((section("slideshow").get("folders") or ["backgrounds"])[0])

    backgrounds = Backgrounds(folders, resolve(section("slideshow").get("cache_dir", ".cache/backgrounds")), screen_size,
                              video_max_seconds=lambda: float(section("slideshow").get("video_max_seconds", 60)))
    cloud = CloudSync(lambda: section("cloud"), lambda: first_folder() / "cloud", lambda: backgrounds.list(max_age=0))
    if start_background:
        cloud.start()
        threading.Thread(target=lambda: backgrounds.list(max_age=0), daemon=True).start()  # start preparing videos
    cameras = Cameras(cfg)
    if custom is None:
        from .custom_gestures import CustomGestures, Teacher

        base = secrets_dir.parent if secrets_dir else ROOT  # tests keep everything in their temp folder
        custom = CustomGestures(base / "data" / "custom_gestures.json")
        teacher = teacher or Teacher()
    calendar = CalendarFeed(cfg.setdefault("calendar", {}))
    news = News(cfg.setdefault("news", {}))

    last = {"transform": transform(cfg), "cloud": dict(section("cloud"))}

    def on_settings_changed() -> None:
        calendar._fetched_at = 0  # fetch again with the new calendar list
        news._fetched_at = 0
        backgrounds.list(max_age=0)
        if transform(cfg) != last["transform"]:
            last["transform"] = transform(cfg)
            apply_rotation(cfg)
        if dict(section("cloud")) != last["cloud"]:
            last["cloud"] = dict(section("cloud"))
            if section("cloud").get("enabled"):
                cloud.sync_now()
        bus.emit("settings", {})

    state = SimpleNamespace(
        cfg=cfg, bus=bus, mirror=mirror, camera=camera, version=version, photos_dir=photos_dir,
        backgrounds=backgrounds, config_path=config_path or ROOT / "config.yaml",
        secrets_dir=secrets_dir or ROOT / ".secrets", restart_soon=restart_soon,
        on_settings_changed=on_settings_changed, problems=problems or [], cloud=cloud, alexa=alexa,
        cameras=cameras, custom=custom, teacher=teacher, blink=blink,
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
            "orientation": "portrait" if is_portrait(cfg) else "landscape",
            "transform": transform(cfg),
            "tiles": max(1, min(6, int(slideshow.get("tiles", 1) or 1))),
            "include_videos": bool(slideshow.get("include_videos", True)),
            "videos_at_once": max(0, min(4, int(slideshow.get("videos_at_once", 1) or 0))),
            "camera": camera is not None,
            "problems": problems or [],
            "actions": list(mirror.actions),
            "bindings": section("gestures").get("bindings") or {},
            "gesture_labels": custom.labels(),
            "action_labels": mirror.action_labels(),
            "cameras": [{"name": c.get("name", f"Camera {i + 1}")} for i, c in enumerate(cameras.list())],
            "blink_cameras": blink.camera_names() if blink else [],
            "blink_live": bool(section("blink").get("live_view")),
        })

    blink_relays: dict = {}

    def blink_name(index: int) -> str:
        names = blink.camera_names() if blink else []
        if not 0 <= index < len(names):
            abort(404)
        return names[index]

    @app.get("/api/blink/<int:index>.jpg")
    def blink_picture(index: int):
        jpg = blink.picture(blink_name(index))
        if not jpg:
            abort(404)
        return Response(jpg, mimetype="image/jpeg", headers={"Cache-Control": "no-cache"})

    @app.get("/api/blink/<int:index>.mjpg")
    def blink_live(index: int):
        name = blink_name(index)
        if not cameras.ffmpeg:
            abort(404)
        from .cameras import CameraRelay

        relay = blink_relays.get(name)
        if relay is None:
            relay = blink_relays[name] = CameraRelay(lambda: blink.live_url(name), lambda: cfg, cameras.ffmpeg)
        return Response(relay.stream(), mimetype="multipart/x-mixed-replace; boundary=frame",
                        headers={"Cache-Control": "no-cache"})

    @app.get("/api/cameras/<int:index>.mjpg")
    def camera_stream(index: int):
        relay = cameras.relay(index)
        if relay is None:
            abort(404)
        return Response(relay.stream(), mimetype="multipart/x-mixed-replace; boundary=frame",
                        headers={"Cache-Control": "no-cache"})

    @app.get("/api/calendar")
    def calendar_events():
        return jsonify(calendar.upcoming())

    @app.get("/api/news")
    def headlines():
        return jsonify(news.headlines())

    @app.get("/api/backgrounds")
    def background_list():
        videos = bool(section("slideshow").get("include_videos", True)) and int(section("slideshow").get("videos_at_once", 1) or 0) > 0
        return jsonify(backgrounds.items(videos=videos))

    @app.get("/bg/<photo_id>.mp4")
    def background_video(photo_id: str):
        path = backgrounds.video_path(photo_id)
        if path is None:
            abort(404)
        return send_file(path, mimetype="video/mp4", max_age=86400, conditional=True)

    @app.post("/api/noop")
    def noop():  # used by the Alexa voice devices that only do something when turned on
        return jsonify({"ok": True})

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
