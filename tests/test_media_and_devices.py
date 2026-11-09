"""Videos in the slideshow, Google Drive sync, lights (brightness/colour, Alexa), Alexa voice, portrait, power."""
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from PIL import Image

from mirror import lights as lights_mod
from mirror.alexa import FIRST_PORT, fauxmo_config
from mirror.backgrounds import Backgrounds
from mirror.cloud_sync import CloudSync
from mirror.config import DEFAULTS_FILE, load_config
from mirror.lights import ActionURLLights, HomeAssistantLights, HueLights, alexa_urls, make_lights
from mirror.main import Mirror
from mirror.screen import transform
from mirror.web import EventBus, create_app

FFMPEG = shutil.which("ffmpeg")
PHONE = {"REMOTE_ADDR": "192.168.1.23"}


def make_video(path: Path, size="1920x1080", seconds=4, audio=True):
    cmd = ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={size}:rate=30:duration={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=duration={seconds}", "-shortest"]
    subprocess.run(cmd + ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)


def probe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def wait_for(cond, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.2)
    return False


# ---------- videos ----------

@pytest.mark.skipif(not FFMPEG, reason="ffmpeg not installed")
def test_videos_are_converted_for_the_screen(tmp_path):
    folder = tmp_path / "bg"
    folder.mkdir()
    make_video(folder / "beach.mov", size="1920x1080", seconds=4)
    make_video(folder / "phone.mp4", size="1080x1920", seconds=4)  # portrait phone video
    Image.new("RGB", (800, 600), "red").save(folder / "photo.jpg")
    bg = Backgrounds([folder], tmp_path / "cache", (1920, 1080), video_max_seconds=2)

    first = bg.items(max_age=0)
    assert [i["type"] for i in first] == ["image"]  # videos appear once their screen copy is ready
    vids = [pid for pid in bg.list() if bg.source(pid).suffix != ".jpg"]
    assert all(bg.video_state(v) == "processing" for v in vids)
    assert wait_for(lambda: all(bg.video_state(v) == "ready" for v in vids))
    assert sorted(i["type"] for i in bg.items()) == ["image", "video", "video"]
    assert bg.items(videos=False) == [i for i in bg.items(shuffle=False) if i["type"] == "image"]

    for v in vids:
        info = probe(bg.video_path(v))
        streams = info["streams"]
        assert [s["codec_type"] for s in streams] == ["video"]  # silent
        w, h = streams[0]["width"], streams[0]["height"]
        assert max(w, h) == 1280 and streams[0]["codec_name"] == "h264"
        assert float(info["format"]["duration"]) <= 2.2  # cut to video_max_seconds
        assert Image.open(bg.image_path(v, thumb=True)).size[0] == 400
        assert bg.image_path(v) is None  # no still image for the slideshow


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg not installed")
def test_video_upload_and_serving(tmp_path):
    cfg = load_config()
    cfg["slideshow"].update(folders=[str(tmp_path / "bg")], cache_dir=str(tmp_path / "cache"),
                            include_booth_photos=False, video_max_seconds=2)
    cfg["calendar"]["feeds"] = []
    client = create_app(cfg, EventBus(), tmp_path / "photos", Mirror(cfg, EventBus(), lights_mod.Lights()),
                        secrets_dir=tmp_path / "s").test_client()
    make_video(tmp_path / "clip.mp4", seconds=3)
    with open(tmp_path / "clip.mp4", "rb") as f:
        r = client.post("/api/remote/backgrounds", data={"photos": [(f, "clip.mp4")]}, content_type="multipart/form-data")
    assert r.json["saved"] == ["clip.mp4"]
    listed = client.get("/api/remote/backgrounds").json
    assert listed[0]["type"] == "video" and listed[0]["state"] in ("processing", "ready")
    assert wait_for(lambda: client.get("/api/remote/backgrounds").json[0]["state"] == "ready")
    shown = client.get("/api/backgrounds").json
    assert shown == [{"id": listed[0]["id"], "type": "video"}]
    v = client.get(f"/bg/{shown[0]['id']}.mp4")
    assert v.status_code == 200 and v.mimetype == "video/mp4"
    # turning videos off in the settings hides them from the screen
    cfg["slideshow"]["include_videos"] = False
    assert client.get("/api/backgrounds").json == []


# ---------- Google Drive (rclone) ----------

@pytest.fixture
def fake_rclone(tmp_path, monkeypatch):
    """A stand-in for rclone: 'gdrive:' is the folder tmp/drive."""
    drive = tmp_path / "drive"
    (drive / "Smart Screen" / "Trip").mkdir(parents=True)
    Image.new("RGB", (64, 64)).save(drive / "Smart Screen" / "Trip" / "a.jpg")
    Image.new("RGB", (64, 64)).save(drive / "Smart Screen" / "b.png")
    (drive / "Smart Screen" / "notes.txt").write_text("not media")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "rclone"
    script.write_text(f"""#!/usr/bin/env python3
import sys, shutil, pathlib
args = sys.argv[1:]
if args[0] == "listremotes":
    print("gdrive:")
elif args[0] == "sync":
    src = pathlib.Path("{drive}") / args[1].split(":", 1)[1]
    if not src.exists():
        print("directory not found", file=sys.stderr); sys.exit(3)
    dst = pathlib.Path(args[2]); shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("*.txt"))
""")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    return drive


def test_cloud_sync_copies_the_drive_folder(tmp_path, fake_rclone):
    cfg = {"enabled": True, "remote": "gdrive", "folder": "Smart Screen"}
    changed = []
    sync = CloudSync(lambda: cfg, lambda: tmp_path / "bg" / "cloud", lambda: changed.append(1))
    assert sync.status()["connected"] and sync.status()["files"] == 0
    assert sync.run_once()
    st = sync.status()
    assert st["last_ok"] and st["files"] == 2 and changed == [1]
    assert (tmp_path / "bg" / "cloud" / "Trip" / "a.jpg").exists()

    cfg["folder"] = "Nope"
    assert not sync.run_once()
    assert "not found" in sync.status()["last_error"]

    cfg["remote"] = "dropbox"
    assert not sync.run_once()
    assert "connect-google-drive" in sync.status()["last_error"]


def test_cloud_photos_cant_be_deleted_from_the_app(tmp_path, fake_rclone):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(DEFAULTS_FILE.read_text())
    cfg = load_config(config_path)
    cfg["slideshow"].update(folders=[str(tmp_path / "bg")], cache_dir=str(tmp_path / "cache"))
    cfg["cloud"].update(enabled=True, folder="Smart Screen")
    app = create_app(cfg, EventBus(), tmp_path / "photos", Mirror(cfg, EventBus(), lights_mod.Lights()),
                     config_path=config_path, secrets_dir=tmp_path / "s")
    client = app.test_client()
    state = app.extensions["smart_screen"]
    assert client.get("/api/remote/cloud").json["connected"] is True
    assert state.cloud.run_once()
    items = client.get("/api/remote/backgrounds").json
    assert len(items) == 2 and all(i["cloud"] for i in items)
    r = client.delete(f"/api/remote/backgrounds/{items[0]['id']}")
    assert r.status_code == 400 and "Google Drive" in r.json["error"]


# ---------- lights ----------

class Recorder:
    def __init__(self):
        self.calls = []

    def ok(self, *a, **kw):
        self.calls.append((a, kw))

        class R:
            def raise_for_status(self):
                pass
        return R()


def test_home_assistant_brightness_and_colours(monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(lights_mod.requests, "post", rec.ok)
    ha = HomeAssistantLights("http://ha:8123", "t", ["light.a"])
    ha.set_brightness(30)
    ha.set_color("warm")
    ha.set_color("blue")
    urls = [a[0] for a, _ in rec.calls]
    bodies = [kw["json"] for _, kw in rec.calls]
    assert urls == ["http://ha:8123/api/services/light/turn_on"] * 3
    assert bodies == [{"entity_id": ["light.a"], "brightness_pct": 30},
                      {"entity_id": ["light.a"], "color_temp_kelvin": 2700},
                      {"entity_id": ["light.a"], "rgb_color": [40, 90, 255]}]
    assert ha.state()["brightness"] == 30 and ha.state()["color"] == "blue" and ha.is_on
    with pytest.raises(ValueError):
        ha.set_color("plaid")


def test_hue_brightness_and_colours(monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(lights_mod.requests, "put", rec.ok)
    hue = HueLights("10.0.0.2", "user", [1])
    hue.set_brightness(50)
    hue.set_color("warm")
    hue.set_color("red")
    bodies = [kw["json"] for _, kw in rec.calls]
    assert bodies[0] == {"on": True, "bri": 127}
    assert bodies[1] == {"on": True, "ct": 370}
    assert bodies[2]["on"] and 0 <= bodies[2]["hue"] < 2000 and bodies[2]["sat"] > 200


def test_alexa_lights_use_voice_monkey_routines(monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(lights_mod.requests, "request", rec.ok)
    cfg = {"backend": "alexa", "alexa": {"token": "tok 1", "routines": {"on": "lights-on-1", "off": "lights-off-2",
                                                                       "party": "party-3", "dim": ""}}}
    lights = make_lights(cfg)
    assert isinstance(lights, ActionURLLights) and not lights.supports_brightness
    lights.turn_on()
    lights.next_party_color()
    lights.set_brightness(20)  # no "dim" routine: nothing is sent
    sent = [a[1] for a, _ in rec.calls]
    assert sent == ["https://api-v3.voicemonkey.io/trigger?token=tok+1&device=lights-on-1",
                    "https://api-v3.voicemonkey.io/trigger?token=tok+1&device=party-3"]
    assert alexa_urls("PASTE-VOICE-MONKEY-TOKEN", {"on": "x"}) == {}


def test_lights_from_the_phone(tmp_path, monkeypatch):
    cfg = load_config()
    cfg["slideshow"].update(folders=[str(tmp_path / "bg")], cache_dir=str(tmp_path / "c"))
    bus = EventBus()
    mirror = Mirror(cfg, bus, lights_mod.Lights())
    client = create_app(cfg, bus, tmp_path / "photos", mirror, secrets_dir=tmp_path / "s").test_client()
    r = client.post("/api/remote/lights", json={"power": "on", "brightness": 40, "color": "purple"})
    assert r.json["ok"] and r.json["lights"]["brightness"] == 40 and r.json["lights"]["color"] == "purple"
    assert client.get("/api/remote/status").json["lights"]["on"] is True
    assert client.post("/api/remote/lights", json={"color": "plaid"}).status_code == 502
    assert {"lights_dim", "lights_bright", "lights_warm", "lights_cool", "lights_party"} <= set(mirror.actions)


# ---------- Alexa voice commands ----------

def test_alexa_voice_devices_config():
    devices = [{"name": "Photo booth", "on": "photo_booth", "off": ""},
               {"name": "Smart screen", "on": "wake", "off": "sleep"},
               {"name": "", "on": "wake"}]
    cfg = fauxmo_config(devices, 8080)
    d = cfg["PLUGINS"]["SimpleHTTPPlugin"]["DEVICES"]
    assert [x["name"] for x in d] == ["Photo booth", "Smart screen"]
    assert d[0]["on_cmd"] == "http://127.0.0.1:8080/api/action/photo_booth"
    assert d[0]["off_cmd"] == "http://127.0.0.1:8080/api/noop"
    assert d[1]["off_cmd"].endswith("/api/action/sleep") and d[1]["port"] == FIRST_PORT + 1
    assert d[0]["method"] == "POST" and d[0]["use_fake_state"] is True


def test_alexa_device_settings_are_checked(tmp_path):
    from mirror import settings_store

    cfg = load_config()
    new = load_config()
    new["alexa"]["devices"][0]["on"] = "rm_rf"
    with pytest.raises(settings_store.SettingsError):
        settings_store.validate(new, cfg, load_config(DEFAULTS_FILE), list(Mirror(cfg, EventBus(), lights_mod.Lights()).actions))


# ---------- portrait ----------

def test_portrait_rotation_and_screen_size(tmp_path):
    cfg = load_config()
    assert transform(cfg) == "normal"
    cfg["display"]["orientation"] = "portrait"
    assert transform(cfg) == "90"
    cfg["display"]["portrait_turned"] = "left"
    assert transform(cfg) == "270"
    cfg["slideshow"].update(folders=[str(tmp_path / "bg")], cache_dir=str(tmp_path / "c"), tiles=4)
    app = create_app(cfg, EventBus(), tmp_path / "photos", Mirror(cfg, EventBus(), lights_mod.Lights()),
                     secrets_dir=tmp_path / "s")
    s = app.test_client().get("/api/settings").json
    assert s["orientation"] == "portrait" and s["transform"] == "270" and s["tiles"] == 4
    assert app.extensions["smart_screen"].backgrounds.size == (1080, 1920)


# ---------- power ----------

def test_shutdown_and_restart_buttons(tmp_path, monkeypatch):
    ran = []

    class Done:
        returncode = 0
        stderr = ""

    monkeypatch.setattr("mirror.remote.subprocess.run", lambda cmd, **kw: ran.append(cmd) or Done())
    cfg = load_config()
    cfg["slideshow"].update(folders=[str(tmp_path / "bg")], cache_dir=str(tmp_path / "c"))
    client = create_app(cfg, EventBus(), tmp_path / "photos", Mirror(cfg, EventBus(), lights_mod.Lights()),
                        secrets_dir=tmp_path / "s").test_client()
    assert client.post("/api/remote/power/shutdown").json["ok"]
    assert client.post("/api/remote/power/reboot").json["ok"]
    assert client.post("/api/remote/power/format").status_code == 404
    assert ran == [["sudo", "-n", "systemctl", "poweroff"], ["sudo", "-n", "systemctl", "reboot"]]
    assert client.post("/api/remote/power/shutdown", environ_base=PHONE).status_code == 401  # pairing needed
