import threading
import time
from pathlib import Path

import pytest
from PIL import Image

from mirror.config import load_config
from mirror.gestures import GestureTrigger
from mirror.lights import HomeAssistantLights, Lights, make_lights
from mirror.main import Mirror
from mirror.photobooth import PAPER_LONG, PAPER_SHORT, layout_grid, layout_single, layout_strip
from mirror.web import EventBus, create_app


# ---------- gesture hold / cooldown ----------

def feed(trigger, name, start, end, step=0.1, score=0.9):
    fired = []
    for i in range(round((end - start) / step) + 1):
        t = round(start + i * step, 6)  # no float drift: 0.1 * 8 must be 0.8
        f = trigger.update(name, score, t)
        if f:
            fired.append((f, t))
    return fired


def test_fires_once_after_hold():
    t = GestureTrigger(hold_seconds=0.8, cooldown_seconds=0)
    assert feed(t, "Thumb_Up", 0, 3) == [("Thumb_Up", 0.8)]


def test_brief_gesture_does_not_fire():
    t = GestureTrigger(hold_seconds=0.8, cooldown_seconds=0)
    assert feed(t, "Victory", 0, 0.5) == []
    assert feed(t, None, 0.6, 1.0) == []
    assert feed(t, "Victory", 1.1, 1.5) == []


def test_low_score_ignored():
    t = GestureTrigger(hold_seconds=0.5, cooldown_seconds=0, min_score=0.7)
    assert feed(t, "Thumb_Up", 0, 2, score=0.5) == []


def test_cooldown_blocks_next_gesture_then_allows():
    t = GestureTrigger(hold_seconds=0.5, cooldown_seconds=2.0)
    assert feed(t, "Thumb_Up", 0, 1) == [("Thumb_Up", 0.5)]
    # Switches to thumbs down right away; must wait for the cooldown (until t=2.5).
    fired = feed(t, "Thumb_Down", 1.1, 4)
    assert len(fired) == 1 and fired[0][0] == "Thumb_Down" and fired[0][1] >= 2.5


def test_release_and_repeat_fires_again():
    t = GestureTrigger(hold_seconds=0.5, cooldown_seconds=0)
    assert len(feed(t, "Thumb_Up", 0, 1)) == 1
    feed(t, None, 1.1, 1.3)
    assert len(feed(t, "Thumb_Up", 1.4, 2.5)) == 1


def test_progress():
    t = GestureTrigger(hold_seconds=1.0, cooldown_seconds=0)
    t.update("Victory", 0.9, 0)
    t.update("Victory", 0.9, 0.5)
    assert t.progress(0.5) == pytest.approx(0.5)


# ---------- print layouts ----------

@pytest.fixture
def shots():
    colors = ["red", "green", "blue", "yellow"]
    return [Image.new("RGB", (1920, 1080), c) for c in colors]


def test_strip_is_portrait_4x6_with_two_identical_strips(shots):
    sheet = layout_strip(shots, "Party!")
    assert sheet.size == (PAPER_SHORT, PAPER_LONG)
    w = sheet.width // 2
    assert sheet.crop((0, 0, w, sheet.height)).tobytes() == sheet.crop((w, 0, 2 * w, sheet.height)).tobytes()
    # photos land top to bottom in order
    assert sheet.getpixel((w // 2, 150)) == (255, 0, 0)
    assert sheet.getpixel((w // 2, 1250)) == (255, 255, 0)


def test_grid_and_single_are_landscape_4x6(shots):
    assert layout_grid(shots).size == (PAPER_LONG, PAPER_SHORT)
    assert layout_single(shots[:1], "hi").size == (PAPER_LONG, PAPER_SHORT)


# ---------- lights ----------

def test_lights_factory_and_toggle():
    lights = make_lights({"backend": "none"})
    assert type(lights) is Lights
    lights.toggle()
    assert lights.is_on
    lights.toggle()
    assert not lights.is_on
    ha = make_lights({"backend": "home_assistant",
                      "home_assistant": {"url": "http://ha:8123/", "token": "t", "entity_ids": ["light.x"]}})
    assert isinstance(ha, HomeAssistantLights) and ha.url == "http://ha:8123"


def test_home_assistant_request(monkeypatch):
    calls = []

    class Resp:
        def raise_for_status(self):
            pass

    monkeypatch.setattr("mirror.lights.requests.post", lambda url, **kw: calls.append((url, kw)) or Resp())
    HomeAssistantLights("http://ha:8123", "tok", ["light.a"]).turn_on()
    url, kw = calls[0]
    assert url == "http://ha:8123/api/services/light/turn_on"
    assert kw["json"] == {"entity_id": ["light.a"]}
    assert kw["headers"]["Authorization"] == "Bearer tok"


# ---------- gesture -> action wiring ----------

class RecordingBus(EventBus):
    def __init__(self):
        super().__init__()
        self.events = []

    def emit(self, event_type, data=None):
        self.events.append((event_type, data))


def wait_for(cond, timeout=2):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_gesture_runs_bound_action():
    cfg = load_config()
    bus = RecordingBus()
    mirror = Mirror(cfg, bus, Lights())
    mirror.on_gesture("Thumb_Up")
    assert wait_for(lambda: ("lights", {"on": True}) in bus.events)
    mirror.on_gesture("Thumb_Down")
    assert wait_for(lambda: ("lights", {"on": False}) in bus.events)


def test_photo_booth_pauses_gestures_until_done():
    done = threading.Event()

    class FakeBooth:
        def start(self, on_done=None):
            self.on_done = on_done
            done.set()
            return True

    class FakeWatcher:
        paused = threading.Event()

    booth, watcher = FakeBooth(), FakeWatcher()
    mirror = Mirror(load_config(), RecordingBus(), Lights(), booth, watcher)
    mirror.on_gesture("Victory")
    assert done.wait(2)
    assert watcher.paused.is_set()
    booth.on_done()
    assert not watcher.paused.is_set()


# ---------- web ----------

def test_web_settings_and_action(tmp_path: Path):
    cfg = load_config()
    cfg["slideshow"]["folders"] = [str(tmp_path / "bg")]
    cfg["calendar"]["feeds"] = []
    cfg["news"]["feeds"] = []
    mirror = Mirror(cfg, RecordingBus(), Lights())
    app = create_app(cfg, EventBus(), tmp_path, mirror, version="abc-1", secrets_dir=tmp_path / "s")
    client = app.test_client()
    s = client.get("/api/settings").json
    assert s["bindings"]["Thumb_Up"] == "lights_on" and s["version"] == "abc-1" and s["camera"] is False
    assert s["theme"]["accent_color"] and s["show"]["calendar"] is True and s["layout"] == "left"
    assert client.post("/api/action/lights_on").json == {"ok": True}
    assert wait_for(lambda: mirror.lights.is_on)
    assert client.post("/api/action/nope").status_code == 404
    assert client.get("/api/calendar").json == []
    assert client.get("/api/news").json == []
    assert client.get("/stream.mjpg").status_code == 404
    assert b"Smart Display" in client.get("/").data


def test_web_serves_background_photos(tmp_path: Path):
    (tmp_path / "bg").mkdir()
    Image.new("RGB", (4000, 3000), "purple").save(tmp_path / "bg" / "beach.jpg")
    cfg = load_config()
    cfg["slideshow"].update(folders=[str(tmp_path / "bg")], cache_dir=str(tmp_path / "cache"), include_booth_photos=False)
    client = create_app(cfg, EventBus(), tmp_path / "photos", Mirror(cfg, RecordingBus(), Lights()),
                        secrets_dir=tmp_path / "s").test_client()
    ids = client.get("/api/backgrounds").json
    assert len(ids) == 1
    r = client.get(f"/bg/{ids[0]}.jpg")
    assert r.status_code == 200 and r.mimetype == "image/jpeg"
    assert client.get("/bg/doesnotexist.jpg").status_code == 404


# ---------- the real MediaPipe model (skipped if not downloaded) ----------

MODEL = Path(__file__).resolve().parent.parent / "models" / "gesture_recognizer.task"


@pytest.mark.skipif(not MODEL.exists(), reason="gesture model not downloaded")
def test_model_loads_and_sees_no_hand_in_blank_frame():
    import mediapipe as mp
    import numpy as np
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision

    rec = vision.GestureRecognizer.create_from_options(vision.GestureRecognizerOptions(
        base_options=mp_python.BaseOptions(model_asset_path=str(MODEL)), running_mode=vision.RunningMode.VIDEO))
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    result = rec.recognize_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=frame), 0)
    assert result.gestures == []
    rec.close()
