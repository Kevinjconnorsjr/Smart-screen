"""Blink: sign-in with the emailed code (password never stored), pictures, motion pop-ups."""
import asyncio
import json
import time

import pytest

pytest.importorskip("blinkpy")
from blinkpy.auth import BlinkTwoFARequiredError  # noqa: E402

from mirror import blink as blink_mod  # noqa: E402
from mirror.config import load_config  # noqa: E402
from mirror.lights import Lights  # noqa: E402
from mirror.main import Mirror  # noqa: E402
from mirror.web import EventBus, create_app  # noqa: E402

JPG = b"\xff\xd8fake-jpeg\xff\xd9"
PHONE = {"REMOTE_ADDR": "192.168.1.23"}


class FakeResponse:
    status = 200

    async def read(self):
        return JPG


class FakeCamera:
    def __init__(self):
        self.motion_detected = False
        self.last_record = "2026-11-10T08:00:00+00:00"
        self.battery = "ok"
        self.arm = True
        self._cached_image = None
        self.snaps = 0

    @property
    def image_from_cache(self):
        return self._cached_image

    async def get_media(self):
        return FakeResponse()

    async def snap_picture(self):
        self.snaps += 1
        self._cached_image = JPG


class FakeAuth:
    def __init__(self, data):
        self.data = data

    @property
    def login_attributes(self):
        return {**self.data, "token": "TOKEN", "refresh_token": "REFRESH", "hardware_id": "HW"}


class FakeBlink:
    """Behaves like blinkpy.Blink for a new sign-in that needs the 2FA code."""

    def __init__(self, login):
        self.auth = FakeAuth(login)
        self.cameras = {}
        self.needs_code = "password" in login  # a saved login (no password) resumes without a code

    async def start(self):
        if self.needs_code:
            raise BlinkTwoFARequiredError()
        self.cameras = {"Front door": FakeCamera()}
        return True

    async def send_2fa_code(self, code):
        if code != "123456":
            return False
        self.cameras = {"Front door": FakeCamera()}
        return True

    async def refresh(self, force=False, force_cache=False):
        return True


@pytest.fixture
def service(tmp_path, monkeypatch):
    events = []
    cfg = load_config()
    svc = blink_mod.BlinkService(cfg, tmp_path / ".secrets" / "blink.json", lambda t, d: events.append((t, d)))

    async def new_blink(login):
        return FakeBlink(login)
    monkeypatch.setattr(svc, "_new_blink", new_blink)
    monkeypatch.setattr(blink_mod.BlinkService, "_poll_forever", lambda self: None)
    monkeypatch.setattr(blink_mod.asyncio, "sleep", _no_sleep)
    svc.start()
    return svc, events, cfg


async def _no_sleep(*_a, **_k):
    return None


def test_sign_in_with_code_and_password_is_not_kept(service, tmp_path):
    svc, events, _ = service
    assert svc.login("me@example.com", "hunter2") == "needs_code"
    assert svc.verify("000000") == "needs_code" and "code" in svc.error
    assert svc.verify("123456") == "signed_in"
    saved = json.loads((tmp_path / ".secrets" / "blink.json").read_text())
    assert saved["refresh_token"] == "REFRESH" and "password" not in saved and saved["username"] == "me@example.com"
    assert svc.camera_names() == ["Front door"]
    # after a restart the saved keys sign in again without a code
    again = blink_mod.BlinkService(svc.cfg, tmp_path / ".secrets" / "blink.json", lambda *a: None)
    again._new_blink = svc._new_blink
    again._poll_forever = lambda: None
    again.start()
    end = time.time() + 5
    while time.time() < end and again.state != "signed_in":
        time.sleep(0.05)
    assert again.state == "signed_in"
    svc.logout()
    assert svc.state == "signed_out" and not (tmp_path / ".secrets" / "blink.json").exists()


def test_pictures_and_motion_popups(service):
    svc, events, cfg = service
    svc.login("me@example.com", "pw")
    svc.verify("123456")
    assert svc.picture("Front door") == JPG
    svc.snap("Front door")
    assert svc.blink.cameras["Front door"].snaps == 1 and ("blink", {"camera": "Front door", "kind": "picture"}) in events

    cam = svc.blink.cameras["Front door"]
    check = lambda: svc._submit(svc._check_motion())  # noqa: E731
    assert check() == []                   # first look after starting: just remembers the last clip
    assert check() == []                   # nothing new
    cam.motion_detected, cam.last_record = True, "2026-11-10T09:15:00+00:00"
    for name in check():
        svc._motion(name)
    popups = [d for t, d in events if t == "blink_motion"]
    assert len(popups) == 1 and popups[0]["camera"] == "Front door" and popups[0]["seconds"] == 20
    assert check() == []                   # same clip doesn't pop up twice
    cfg["blink"]["motion_popups"] = False
    cam.last_record = "2026-11-10T09:30:00+00:00"
    for name in check():
        svc._motion(name)
    assert len([1 for t, _ in events if t == "blink_motion"]) == 1  # turned off: no pop-up
    assert svc.status()["cameras"][0]["last_motion"]


def test_blink_in_the_app(service, tmp_path):
    svc, events, cfg = service
    cfg["slideshow"].update(folders=[str(tmp_path / "bg")], cache_dir=str(tmp_path / "c"))
    bus = EventBus()
    client = create_app(cfg, bus, tmp_path / "photos", Mirror(cfg, bus, Lights()), secrets_dir=tmp_path / "s",
                        blink=svc).test_client()
    r = client.post("/api/remote/blink/login", json={"username": "me@example.com", "password": "pw"})
    assert r.json == {"ok": True, "state": "needs_code", "error": ""}
    assert client.post("/api/remote/blink/verify", json={"code": "123456"}).json["ok"]
    assert client.get("/api/settings").json["blink_cameras"] == ["Front door"]
    pic = client.get("/api/blink/0.jpg")
    assert pic.status_code == 200 and pic.data == JPG
    assert client.get("/api/blink/3.jpg").status_code == 404
    assert client.post("/api/remote/blink/0/snap").json["ok"]
    assert client.get("/api/remote/blink").json["cameras"][0]["name"] == "Front door"
    assert client.get("/api/blink/0.jpg", environ_base=PHONE).status_code == 401  # pairing needed
