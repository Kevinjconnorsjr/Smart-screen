"""The phone remote: pairing, who may do what, saving settings, photos and the booth gallery."""
import io
import time
from pathlib import Path

import pytest
from PIL import Image

from mirror import settings_store
from mirror.config import DEFAULTS_FILE, load_config
from mirror.lights import Lights
from mirror.main import Mirror
from mirror.remote import Pairing
from mirror.web import EventBus, create_app

PHONE = {"REMOTE_ADDR": "192.168.1.23"}
PI = {"REMOTE_ADDR": "127.0.0.1"}


class Bus(EventBus):
    def __init__(self):
        super().__init__()
        self.events = []

    def emit(self, t, d=None):
        self.events.append((t, d))


@pytest.fixture
def env(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(DEFAULTS_FILE.read_text())  # a full config.yaml with comments, like after install
    cfg = load_config(config_path)
    cfg["slideshow"].update(folders=[str(tmp_path / "bg")], cache_dir=str(tmp_path / "cache"))
    cfg["calendar"]["feeds"] = []
    cfg["news"]["feeds"] = []
    bus = Bus()
    mirror = Mirror(cfg, bus, Lights())
    restarts = []
    app = create_app(cfg, bus, tmp_path / "photos", mirror, config_path=config_path,
                     secrets_dir=tmp_path / "secrets", restart_soon=lambda: restarts.append(1))
    client = app.test_client()
    return client, bus, cfg, config_path, restarts, tmp_path


def pair(client, bus):
    assert client.post("/api/pair/start", environ_base=PHONE).json["ok"]
    code = [d for t, d in bus.events if t == "pair"][-1]["code"]
    r = client.post("/api/pair/finish", json={"code": code[:3] + " " + code[3:], "name": "Kevin's Pixel"}, environ_base=PHONE)
    assert r.status_code == 200 and r.json["ok"]
    return r.json["token"]


# ---------- who may do what ----------

def test_phone_must_pair_before_anything(env):
    client, bus, *_ = env
    for method, url in [("get", "/"), ("get", "/api/settings"), ("get", "/api/calendar"), ("post", "/api/action/lights_on"),
                        ("get", "/api/remote/config"), ("get", "/api/remote/backgrounds"), ("get", "/events")]:
        assert getattr(client, method)(url, environ_base=PHONE).status_code == 401, url
    # the pairing page itself is open
    assert client.get("/remote", environ_base=PHONE).status_code == 200
    assert client.get("/api/pair/status", environ_base=PHONE).json == {"paired": False}
    # the Pi's own browser never needs pairing
    assert client.get("/api/settings", environ_base=PI).status_code == 200


def test_pairing_with_code_from_screen(env):
    client, bus, *_ = env
    token = pair(client, bus)
    assert client.get("/api/pair/status", environ_base=PHONE).json == {"paired": True}  # cookie set
    assert client.get("/api/remote/status", environ_base=PHONE).status_code == 200
    assert ("pair", {"code": None, "paired": True}) in bus.events  # code disappears from the screen
    # the token also works as a header (scripts, curl) from another client without the cookie
    other = client.application.test_client()
    assert other.get("/api/remote/status", environ_base=PHONE).status_code == 401
    assert other.get("/api/remote/status", environ_base=PHONE, headers={"Authorization": f"Bearer {token}"}).status_code == 200
    # only a hash is stored on the Pi
    stored = (env[5] / "secrets" / "remote_devices.json").read_text()
    assert token not in stored and "Kevin's Pixel" in stored


def test_wrong_codes_lock_out_after_five_tries():
    events = []
    now = [0.0]
    p = Pairing(lambda t, d: events.append(d), clock=lambda: now[0])
    assert p.start()
    real = p.code
    wrong = "000000" if real != "000000" else "111111"
    for _ in range(5):
        assert not p.finish(wrong)
    assert not p.finish(real)  # code is burned after 5 wrong tries
    assert not p.start()  # can't immediately spam a new code
    now[0] = 10
    assert p.start()
    now[0] = 10 + 121
    assert not p.finish(p.code or "")  # expired after 2 minutes


def test_removing_a_phone_revokes_it(env):
    client, bus, *_ = env
    pair(client, bus)
    devices = client.get("/api/remote/devices", environ_base=PHONE).json
    assert len(devices) == 1 and devices[0]["this_phone"] and devices[0]["name"] == "Kevin's Pixel"
    client.delete(f"/api/remote/devices/{devices[0]['id']}", environ_base=PHONE)
    assert client.get("/api/remote/status", environ_base=PHONE).status_code == 401


# ---------- settings ----------

def test_settings_hide_secrets_and_save_live_keeping_comments(env):
    client, bus, cfg, config_path, restarts, _ = env
    cfg["lights"]["home_assistant"]["token"] = "secret-token"
    pair(client, bus)
    data = client.get("/api/remote/config", environ_base=PHONE).json
    new = data["config"]
    assert new["lights"]["home_assistant"]["token"] == settings_store.MASK
    assert "display.screen_off_command" in data["locked"]

    new["display"]["greeting"] = "Hi Connors!"
    new["display"]["theme"]["accent_color"] = "#ff8a65"
    new["display"]["show"]["news"] = False
    new["display"]["messages"] = ["Trash night 🗑️"]
    new["calendar"]["feeds"] = [{"name": "Family", "url": "https://example.com/basic.ics", "color": "#123456"}]
    new["display"]["screen_off_command"] = "rm -rf /"  # locked: must be ignored
    r = client.put("/api/remote/config", json=new, environ_base=PHONE)
    assert r.status_code == 200 and r.json["applied"] == "live", r.json
    assert not restarts
    assert cfg["display"]["greeting"] == "Hi Connors!" and cfg["display"]["show"]["news"] is False
    assert cfg["display"]["screen_off_command"] == ""
    assert cfg["lights"]["home_assistant"]["token"] == "secret-token"  # mask didn't overwrite it
    assert ("settings", {}) in bus.events  # the screen reloads its look

    text = config_path.read_text()
    assert "Hi Connors!" in text and "#ff8a65" in text and "Trash night" in text
    assert "# Copy this file to config.yaml" in text  # comments survive
    assert "rm -rf" not in text
    reloaded = load_config(config_path)
    assert reloaded["calendar"]["feeds"][0]["url"] == "https://example.com/basic.ics"
    assert reloaded["display"]["show"]["news"] is False
    # the screen's settings endpoint reflects it immediately
    s = client.get("/api/settings", environ_base=PI).json
    assert s["greeting"] == "Hi Connors!" and s["theme"]["accent_color"] == "#ff8a65"


def test_settings_that_need_a_restart(env):
    client, bus, cfg, config_path, restarts, _ = env
    pair(client, bus)
    new = client.get("/api/remote/config", environ_base=PHONE).json["config"]
    new["lights"]["backend"] = "hue"
    r = client.put("/api/remote/config", json=new, environ_base=PHONE)
    assert r.json["applied"] == "restart" and restarts == [1]


def test_bad_settings_are_rejected(env):
    client, bus, cfg, config_path, *_ = env
    pair(client, bus)
    before = config_path.read_text()
    new = client.get("/api/remote/config", environ_base=PHONE).json["config"]
    new["photobooth"]["shots"] = "lots"
    r = client.put("/api/remote/config", json=new, environ_base=PHONE)
    assert r.status_code == 400 and "number" in r.json["error"]
    new["photobooth"]["shots"] = 4
    new["gestures"]["bindings"]["Victory"] = "format_disk"
    assert client.put("/api/remote/config", json=new, environ_base=PHONE).status_code == 400
    assert config_path.read_text() == before


# ---------- photos ----------

def jpeg(color="red", size=(1200, 900)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    buf.seek(0)
    return buf


def test_upload_list_thumb_delete_background_photos(env):
    client, bus, cfg, _, _, tmp = env
    pair(client, bus)
    r = client.post("/api/remote/backgrounds", environ_base=PHONE, content_type="multipart/form-data",
                    data={"photos": [(jpeg(), "../../beach day.jpg"), (jpeg("blue"), "beach day.jpg"),
                                     (io.BytesIO(b"not an image"), "evil.jpg"), (io.BytesIO(b"x"), "run.sh")]})
    assert r.json["ok"] and sorted(r.json["saved"]) == ["beach_day-1.jpg", "beach_day.jpg"]
    assert sorted(p.name for p in (tmp / "bg" / "from-phone").iterdir()) == ["beach_day-1.jpg", "beach_day.jpg"]
    items = client.get("/api/remote/backgrounds", environ_base=PHONE).json
    assert len(items) == 2
    t = client.get(f"/api/remote/thumb/{items[0]['id']}.jpg", environ_base=PHONE)
    assert t.status_code == 200 and max(Image.open(io.BytesIO(t.data)).size) <= 400
    assert client.delete(f"/api/remote/backgrounds/{items[0]['id']}", environ_base=PHONE).json["ok"]
    assert len(client.get("/api/remote/backgrounds", environ_base=PHONE).json) == 1


def test_booth_gallery_reprint_and_delete(env, monkeypatch):
    client, bus, cfg, _, _, tmp = env
    session = tmp / "photos" / "20261109-183000"
    session.mkdir(parents=True)
    Image.new("RGB", (1200, 1800)).save(session / "print.jpg")
    Image.new("RGB", (640, 480)).save(session / "shot1.jpg")
    printed = []
    monkeypatch.setattr("mirror.photobooth.print_file", lambda path, *a: printed.append(path) or True)
    pair(client, bus)
    sessions = client.get("/api/remote/booth", environ_base=PHONE).json
    assert sessions[0]["id"] == "20261109-183000" and sessions[0]["has_print"] and sessions[0]["shots"] == ["shot1.jpg"]
    assert client.get("/api/remote/booth/20261109-183000/print.jpg?thumb=1", environ_base=PHONE).status_code == 200
    assert client.get("/api/remote/booth/20261109-183000/..%2Fsecret.jpg", environ_base=PHONE).status_code == 404
    assert client.get("/api/remote/booth/../x/print.jpg", environ_base=PHONE).status_code == 404
    assert client.post("/api/remote/booth/20261109-183000/print", json={}, environ_base=PHONE).json["ok"]
    assert printed == [session / "print.jpg"]
    # booth photos are not listed (or deletable) as slideshow backgrounds
    assert client.get("/api/remote/backgrounds", environ_base=PHONE).json == []
    assert client.delete("/api/remote/booth/20261109-183000", environ_base=PHONE).json["ok"]
    assert not session.exists()


# ---------- saving keeps a short config.yaml short ----------

def test_save_only_writes_changed_keys(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("# my settings\ndisplay:\n  greeting: Hello  # keep me\n")
    old = load_config(path)
    new = load_config(path)
    new["display"]["clock_24h"] = True
    changed = settings_store.save(path, DEFAULTS_FILE, old, new)
    assert changed == [("display", "clock_24h")]
    text = path.read_text()
    assert "# my settings" in text and "# keep me" in text and "clock_24h: true" in text
    assert "photobooth" not in text  # defaults weren't copied in
