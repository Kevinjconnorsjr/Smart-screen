"""Taught gestures, smart devices, security cameras."""
import shutil
import subprocess
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from mirror import devices as devices_mod
from mirror.cameras import CameraRelay, ffmpeg_command, split_jpegs, valid_source
from mirror.config import DEFAULTS_FILE, load_config
from mirror.custom_gestures import SAMPLES_WANTED, CustomGestures, Teacher, features
from mirror.devices import Devices, device_actions
from mirror.lights import Lights
from mirror.main import Mirror
from mirror.settings_store import MASK
from mirror.web import EventBus, create_app

RNG = np.random.default_rng(7)


class Bus(EventBus):
    def __init__(self):
        super().__init__()
        self.events = []

    def emit(self, t, d=None):
        self.events.append((t, d))


# ---------- taught gestures ----------

def hand(fingers_up: tuple[bool, ...]) -> np.ndarray:
    """A simple 21-point hand: wrist, then 4 points for the thumb and each finger, straight up or curled."""
    pts = [(0.0, 0.0, 0.0)]
    bases = [-0.45, -0.2, 0.0, 0.2, 0.38]
    for f, (x, up) in enumerate(zip(bases, fingers_up)):
        for j in range(1, 5):
            if up:
                pts.append((x * (1 + 0.1 * j), -0.25 * j - 0.6, 0.0))
            else:
                pts.append((x, -0.7 + 0.12 * j, 0.15 * j))  # curled back towards the palm
    return np.array(pts)


def shown(points: np.ndarray, shift=(0.3, 0.5), scale=0.25, noise=0.01, mirror=False) -> np.ndarray:
    p = points * scale + np.array([*shift, 0])
    if mirror:
        p[:, 0] = -p[:, 0]
    return p + RNG.normal(0, noise * scale, p.shape)


ROCK = hand((False, True, False, False, True))     # 🤘
THREE = hand((False, True, True, True, False))     # 3 fingers
FIST = hand((False,) * 5)


def samples(pose, n=40):
    return [features(shown(pose, shift=RNG.uniform(0.2, 0.8, 2), scale=RNG.uniform(0.15, 0.35))) for _ in range(n)]


def test_taught_gestures_are_recognised_anywhere_any_size(tmp_path):
    cg = CustomGestures(tmp_path / "g.json")
    rock = cg.add("Rock on", "🤘", samples(ROCK))
    three = cg.add("Three", "", samples(THREE))
    assert (rock, three) == ("c_rock_on", "c_three")
    # different place, different size, a bit of noise
    gid, conf = cg.match(features(shown(ROCK, shift=(0.7, 0.2), scale=0.4)))
    assert gid == rock and conf > 0.6
    assert cg.match(features(shown(THREE, shift=(0.1, 0.9), scale=0.12)))[0] == three
    # the left hand: MediaPipe labels it "Left" and the pose is mirrored back
    assert cg.match(features(shown(ROCK, mirror=True), left_hand=True))[0] == rock
    # something it wasn't taught
    assert cg.match(features(shown(FIST)))[0] is None
    assert cg.labels() == {"c_rock_on": "🤘 Rock on", "c_three": "✋ Three"}
    # saved, and loaded again after a restart
    again = CustomGestures(tmp_path / "g.json")
    assert again.match(features(shown(ROCK)))[0] == rock
    assert again.remove(rock) and again.match(features(shown(ROCK)))[0] is None


def test_teaching_needs_enough_of_the_hand(tmp_path):
    cg = CustomGestures(tmp_path / "g.json")
    with pytest.raises(ValueError):
        cg.add("Blip", "", samples(ROCK, n=5))
    t = Teacher()
    t.offer(features(ROCK))  # not active: ignored
    t.start()
    for v in samples(ROCK, n=SAMPLES_WANTED + 10):
        t.offer(v)
    assert t.count == SAMPLES_WANTED
    assert len(t.stop()) == SAMPLES_WANTED and not t.active


# ---------- the app: teaching, devices, cameras ----------

@pytest.fixture
def env(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(DEFAULTS_FILE.read_text())
    cfg = load_config(config_path)
    cfg["slideshow"].update(folders=[str(tmp_path / "bg")], cache_dir=str(tmp_path / "c"))
    bus = Bus()
    mirror = Mirror(cfg, bus, Lights())
    app = create_app(cfg, bus, tmp_path / "photos", mirror, config_path=config_path,
                     secrets_dir=tmp_path / "secrets", restart_soon=lambda: None)
    return app.test_client(), app.extensions["smart_screen"], cfg, bus, config_path


def test_teach_a_gesture_from_the_app_and_link_it(env):
    client, st, cfg, bus, config_path = env
    assert client.post("/api/remote/gestures/teach", json={"name": "Rock on"}).status_code == 409  # no camera

    class FakeWatcher:
        seeing = {"name": None, "score": 0, "hand": False}
        paused = threading.Event()
    st.mirror.watcher = FakeWatcher()

    def camera():  # stands in for the gesture watcher: feeds poses while teaching is on
        end = time.time() + 15
        while time.time() < end and not (st.teacher.active and st.teacher.count >= SAMPLES_WANTED):
            if st.teacher.active:
                st.teacher.offer(features(shown(ROCK)))
            time.sleep(0.01)
    threading.Thread(target=camera, daemon=True).start()
    assert client.post("/api/remote/gestures/teach", json={"name": "Rock on", "emoji": "🤘"}).json["ok"]
    end = time.time() + 15
    while time.time() < end and client.get("/api/remote/gestures").json["teaching"].get("phase") != "done":
        time.sleep(0.2)
    g = client.get("/api/remote/gestures").json
    assert g["teaching"]["ok"] and g["custom"] == {"c_rock_on": "🤘 Rock on"}
    phases = [d["phase"] for t, d in bus.events if t == "teach"]
    assert phases[:3] == ["get_ready"] * 3 and "recording" in phases and phases[-1] == "done"

    # link it to an action, like the Settings page does
    new = client.get("/api/remote/config").json["config"]
    new["gestures"]["bindings"]["c_rock_on"] = "lights_party"
    assert client.put("/api/remote/config", json=new).json["ok"]
    assert "c_rock_on: lights_party" in config_path.read_text()
    assert client.get("/api/settings").json["gesture_labels"]["c_rock_on"] == "🤘 Rock on"
    # forgetting it also removes the link
    assert client.delete("/api/remote/gestures/c_rock_on").json["ok"]
    assert "c_rock_on" not in config_path.read_text() and "c_rock_on" not in cfg["gestures"]["bindings"]


class Calls:
    def __init__(self):
        self.calls = []

    def __call__(self, *a, **kw):
        self.calls.append((a, kw))

        class R:
            def raise_for_status(self):
                pass
        return R()


def test_devices_home_assistant_alexa_webhook(monkeypatch):
    post, get, req = Calls(), Calls(), Calls()
    monkeypatch.setattr(devices_mod.requests, "post", post)
    monkeypatch.setattr(devices_mod.requests, "get", get)
    monkeypatch.setattr(devices_mod.requests, "request", req)
    cfg = {"connections": {"home_assistant": {"url": "http://ha:8123/", "token": "T"}, "voice_monkey": {"token": "VM"}},
           "devices": [{"name": "Living room fan", "type": "home_assistant", "entity_id": "switch.fan"},
                       {"name": "Movie night", "type": "home_assistant", "entity_id": "scene.movie"},
                       {"name": "Bedroom lamp", "type": "alexa", "on_routine": "lamp-on", "off_routine": "lamp-off"},
                       {"name": "Garage", "type": "webhook", "on_url": "http://10.0.0.9/on", "off_url": ""}]}
    assert set(device_actions(cfg)) >= {"device_on:living_room_fan", "device_toggle:bedroom_lamp", "device_off:garage"}
    d = Devices(cfg)
    assert d.run("device_toggle:living_room_fan") is True
    assert d.run("device_toggle:living_room_fan") is False
    assert [(a[0], kw["json"]) for a, kw in post.calls] == [
        ("http://ha:8123/api/services/homeassistant/turn_on", {"entity_id": "switch.fan"}),
        ("http://ha:8123/api/services/homeassistant/turn_off", {"entity_id": "switch.fan"})]
    assert post.calls[0][1]["headers"] == {"Authorization": "Bearer T"}
    d.run("device_off:movie_night")  # scenes can't be "turned off": nothing sent
    assert len(post.calls) == 2
    d.run("device_on:bedroom_lamp")
    assert get.calls[0][0][0] == "https://api-v3.voicemonkey.io/trigger?token=VM&device=lamp-on"
    d.run("device_on:garage")
    d.run("device_off:garage")  # no off URL: nothing sent
    assert req.calls == [(("POST", "http://10.0.0.9/on"), {"timeout": 6})]
    with pytest.raises(ValueError):
        d.run("device_on:toaster")


def test_device_actions_in_gestures_and_alexa(env, monkeypatch):
    client, st, cfg, bus, _ = env
    monkeypatch.setattr(devices_mod.requests, "post", Calls())
    new = client.get("/api/remote/config").json["config"]
    new["connections"]["home_assistant"].update(url="http://ha:8123", token="T")
    new["devices"] = [{"name": "Fan", "type": "home_assistant", "entity_id": "switch.fan"}]
    new["gestures"]["bindings"]["ILoveYou"] = "device_toggle:fan"   # device added in the same save
    new["alexa"]["devices"].append({"name": "Fan", "on": "device_on:fan", "off": "device_off:fan"})
    r = client.put("/api/remote/config", json=new)
    assert r.json["ok"], r.json
    assert "device_toggle:fan" in st.mirror.actions
    assert client.post("/api/remote/smart/fan/on").json == {"ok": True, "on": True}
    st.mirror.on_gesture("ILoveYou")
    end = time.time() + 3
    while time.time() < end and ("status", {"text": "Fan off"}) not in bus.events:
        time.sleep(0.05)
    assert ("status", {"text": "Fan off"}) in bus.events
    # secrets never go to the phone
    assert client.get("/api/remote/config").json["config"]["connections"]["home_assistant"]["token"] == MASK
    new = client.get("/api/remote/config").json["config"]
    new["devices"].append({"name": "fan", "type": "webhook"})
    assert "Two devices" in client.put("/api/remote/config", json=new).json["error"]


def test_camera_addresses_and_passwords(env):
    client, st, cfg, bus, config_path = env
    assert valid_source("rtsp://a:b@1.2.3.4/x") and valid_source("ha:camera.door") and not valid_source("file:///etc/passwd")
    new = client.get("/api/remote/config").json["config"]
    new["cameras"]["list"] = [{"name": "Front door", "url": "rtsp://admin:s3cret@192.168.1.50:554/stream2"}]
    assert client.put("/api/remote/config", json=new).json["ok"]
    shown_url = client.get("/api/remote/config").json["config"]["cameras"]["list"][0]["url"]
    assert shown_url == f"rtsp://admin:{MASK}@192.168.1.50:554/stream2"
    # saving other changes keeps the real password
    again = client.get("/api/remote/config").json["config"]
    again["display"]["greeting"] = "Hi"
    assert client.put("/api/remote/config", json=again).json["ok"]
    assert cfg["cameras"]["list"][0]["url"] == "rtsp://admin:s3cret@192.168.1.50:554/stream2"
    assert "s3cret" in config_path.read_text()
    bad = client.get("/api/remote/config").json["config"]
    bad["cameras"]["list"].append({"name": "Sneaky", "url": "file:///etc/shadow"})
    assert client.put("/api/remote/config", json=bad).status_code == 400
    assert client.get("/api/settings").json["cameras"] == [{"name": "Front door"}]
    assert client.get("/api/cameras/5.mjpg").status_code == 404


def test_ffmpeg_commands(env):
    _, _, cfg, _, _ = env
    cfg["connections"]["home_assistant"].update(url="http://ha:8123", token="T")
    rtsp = ffmpeg_command("ffmpeg", "rtsp://u:p@10.0.0.5/s", cfg)
    assert rtsp[rtsp.index("-rtsp_transport") + 1] == "tcp" and "rtsp://u:p@10.0.0.5/s" in rtsp
    ha = ffmpeg_command("ffmpeg", "ha:camera.door", cfg)
    assert ha[ha.index("-headers") + 1] == "Authorization: Bearer T\r\n"
    assert "http://ha:8123/api/camera_proxy_stream/camera.door" in ha


def test_split_jpegs():
    buf = bytearray(b"junk\xff\xd8AAA\xff\xd9\xff\xd8BB")
    assert split_jpegs(buf) == [b"\xff\xd8AAA\xff\xd9"]
    assert bytes(buf) == b"\xff\xd8BB"
    buf += b"B\xff\xd9"
    assert split_jpegs(buf) == [b"\xff\xd8BBB\xff\xd9"] and not buf


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_camera_relay_streams_jpegs(tmp_path):
    video = tmp_path / "cam.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=15:duration=3",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)], check=True)
    relay = CameraRelay(lambda: str(video), lambda: {}, shutil.which("ffmpeg"))
    stream = relay.stream()
    parts = [next(stream) for _ in range(3)]
    stream.close()
    for p in parts:
        assert p.startswith(b"--frame\r\nContent-Type: image/jpeg\r\n\r\n\xff\xd8") and p.endswith(b"\xff\xd9\r\n")
    from io import BytesIO

    from PIL import Image
    jpg = parts[0].split(b"\r\n\r\n", 1)[1][:-2]
    assert Image.open(BytesIO(jpg)).size == (960, 540)  # made smaller for the screen
    time.sleep(0.5)
    assert relay.viewers == 0


def test_show_cameras_hides_itself(env):
    client, st, cfg, bus, _ = env
    cfg["cameras"]["show_seconds"] = 1
    st.mirror.show_cameras(None)
    assert ("cameras", {"show": True, "seconds": 1}) in bus.events
    time.sleep(1.4)
    assert bus.events[-1] == ("cameras", {"show": False, "seconds": 1}) and not st.mirror.cameras_shown
