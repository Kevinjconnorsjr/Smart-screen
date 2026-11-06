import datetime as dt
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from mirror.backgrounds import Backgrounds
from mirror.calendar_feed import CalendarFeed, parse_events
from mirror.power import ScreenPower, in_hours, parse_hours

ROOT = Path(__file__).resolve().parent.parent
TZ = dt.timezone(dt.timedelta(hours=-5))

ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:1
SUMMARY:Soccer practice
DTSTART:20261102T230000Z
DTEND:20261103T000000Z
RRULE:FREQ=WEEKLY;BYDAY=MO,WE
END:VEVENT
BEGIN:VEVENT
UID:2
SUMMARY:Mom's birthday
DTSTART;VALUE=DATE:20261106
DTEND;VALUE=DATE:20261107
END:VEVENT
BEGIN:VEVENT
UID:3
SUMMARY:Cancelled dinner
STATUS:CANCELLED
DTSTART:20261105T230000Z
DTEND:20261106T010000Z
END:VEVENT
BEGIN:VEVENT
UID:4
SUMMARY:Dentist
DTSTART:20261105T140000Z
DURATION:PT30M
END:VEVENT
END:VCALENDAR
"""


# ---------- calendar ----------

def test_parse_events_expands_repeats_and_skips_cancelled():
    start = dt.datetime(2026, 11, 2, tzinfo=TZ)
    events = parse_events(ICS, start, start + dt.timedelta(days=7), "Family", "#123456")
    titles = sorted(e["title"] for e in events)
    assert titles == ["Dentist", "Mom's birthday", "Soccer practice", "Soccer practice"]  # Mon 11/2 + Wed 11/4
    bday = next(e for e in events if e["title"] == "Mom's birthday")
    assert bday["all_day"] and bday["start"].startswith("2026-11-06T00:00")
    dentist = next(e for e in events if e["title"] == "Dentist")
    assert dentist["start"] == "2026-11-05T09:00:00-05:00" and dentist["end"] == "2026-11-05T09:30:00-05:00"
    assert all(e["calendar"] == "Family" and e["color"] == "#123456" for e in events)


def test_calendar_feed_hides_finished_events_and_sorts(tmp_path):
    ics = tmp_path / "family.ics"
    ics.write_text(ICS)
    feed = CalendarFeed({"days_ahead": 3, "feeds": [{"name": "Family", "url": str(ics)}]})
    now = dt.datetime(2026, 11, 5, 10, 0, tzinfo=TZ)  # after the 9:00 dentist appointment
    titles = [e["title"] for e in feed.upcoming(now)]
    assert titles == ["Mom's birthday"]  # Monday/Wednesday soccer is outside Thu..Sun
    early = dt.datetime(2026, 11, 4, 8, 0, tzinfo=TZ)
    feed2 = CalendarFeed({"days_ahead": 2, "feeds": [{"name": "F", "url": str(ics)}]})
    assert [e["title"] for e in feed2.upcoming(early)] == ["Soccer practice", "Dentist", "Mom's birthday"]


def test_calendar_bad_feed_does_not_crash(tmp_path):
    feed = CalendarFeed({"feeds": [{"name": "missing", "url": str(tmp_path / "nope.ics")},
                                   {"name": "placeholder", "url": "PASTE-SECRET-ICAL-LINK-HERE"}]})
    assert feed.upcoming() == []


# ---------- background photos ----------

def test_backgrounds_resize_rotate_and_skip(tmp_path):
    folder = tmp_path / "bg"
    (folder / "trip").mkdir(parents=True)
    (folder / ".hidden").mkdir()
    big = Image.new("RGB", (4000, 3000), "blue")
    exif = big.getexif()
    exif[0x0112] = 6  # "rotate 90°" like a phone held upright
    big.save(folder / "trip" / "upright.jpg", exif=exif)
    Image.new("RGB", (800, 600), "red").save(folder / "small.png")
    Image.new("RGB", (100, 100)).save(folder / ".hidden" / "secret.jpg")
    Image.new("RGB", (100, 100)).save(folder / "print.jpg")  # photo booth print sheets are skipped
    (folder / "notes.txt").write_text("not a photo")

    bg = Backgrounds([folder, tmp_path / "missing"], tmp_path / "cache", (1920, 1080))
    ids = bg.list()
    assert len(ids) == 2
    sizes = sorted(Image.open(bg.image_path(i)).size for i in ids)
    # upright 3000x4000 photo is scaled to cover 1920x1080 -> 1920 wide; small one is left alone
    assert sizes == [(800, 600), (1920, 2560)]
    assert bg.image_path("nope") is None


# ---------- screen sleep ----------

class Clock:
    def __init__(self):
        self.t = 0.0
        self.now = dt.datetime(2026, 11, 5, 23, 0)


def test_screen_sleeps_after_idle_and_wakes_on_activity():
    c, events, cmds = Clock(), [], []
    p = ScreenPower({"sleep_after_minutes": 10, "always_on_hours": "07:00-22:00",
                     "screen_off_command": "off", "screen_on_command": "on"},
                    lambda t, d: events.append(t), monotonic=lambda: c.t, clock=lambda: c.now)
    p._run = cmds.append
    c.t = 599
    p.tick()
    assert not p.asleep
    c.t = 600
    p.tick()
    assert p.asleep and events == ["sleep"] and cmds == ["off"]
    p.activity()
    assert not p.asleep and events[-1] == "wake" and cmds == ["off", "on"]


def test_screen_stays_on_during_always_on_hours():
    c = Clock()
    c.now = dt.datetime(2026, 11, 5, 12, 0)
    p = ScreenPower({"sleep_after_minutes": 1, "always_on_hours": "07:00-22:00"}, lambda t, d: None,
                    monotonic=lambda: c.t, clock=lambda: c.now)
    c.t = 10_000
    p.tick()
    assert not p.asleep


def test_hours_wrap_past_midnight():
    h = parse_hours("22:00-06:30")
    assert in_hours(h, dt.time(23, 0)) and in_hours(h, dt.time(3, 0))
    assert not in_hours(h, dt.time(12, 0))
    assert parse_hours("") is None and not in_hours(None, dt.time(12, 0))


# ---------- auto-update from GitHub ----------

def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repos(tmp_path):
    """A fake 'GitHub' repo, a Pi clone of it, and a developer clone that pushes updates."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    dev = tmp_path / "dev"
    subprocess.run(["git", "clone", "-q", str(origin), str(dev)], check=True)
    git(dev, "config", "user.email", "t@t"), git(dev, "config", "user.name", "t")
    (dev / "deploy").mkdir()
    shutil.copy(ROOT / "deploy" / "update.sh", dev / "deploy" / "update.sh")
    (dev / "app.txt").write_text("v1")
    git(dev, "add", "."), git(dev, "commit", "-qm", "v1"), git(dev, "push", "-q", "origin", "main")
    pi = tmp_path / "pi"
    subprocess.run(["git", "clone", "-q", str(origin), str(pi)], check=True)
    # Stand-in for `sudo systemctl restart`, so the test can see the restart happen.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("sudo", "systemctl"):
        (bin_dir / name).write_text(f'#!/bin/sh\necho "{name} $*" >> {tmp_path}/restarts\n')
        (bin_dir / name).chmod(0o755)
    return dev, pi, bin_dir, tmp_path / "restarts"


def run_update(pi, bin_dir):
    import os
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}")
    return subprocess.run(["bash", str(pi / "deploy" / "update.sh")], capture_output=True, text=True, env=env)


def test_update_pulls_new_commits_and_restarts(repos):
    dev, pi, bin_dir, restarts = repos
    r = run_update(pi, bin_dir)
    assert r.returncode == 0 and "Up to date" in r.stdout and not restarts.exists()

    (dev / "app.txt").write_text("v2")
    git(dev, "commit", "-qam", "v2"), git(dev, "push", "-q", "origin", "main")
    r = run_update(pi, bin_dir)
    assert r.returncode == 0, r.stderr
    assert (pi / "app.txt").read_text() == "v2"
    assert "restart smart-screen" in restarts.read_text()


def test_update_never_overwrites_changes_made_on_the_pi(repos):
    dev, pi, bin_dir, restarts = repos
    (pi / "app.txt").write_text("edited on the Pi")
    (dev / "app.txt").write_text("v2")
    git(dev, "commit", "-qam", "v2"), git(dev, "push", "-q", "origin", "main")
    r = run_update(pi, bin_dir)
    assert r.returncode == 1 and "edited on this Pi" in r.stderr
    assert (pi / "app.txt").read_text() == "edited on the Pi"
    assert not restarts.exists()
