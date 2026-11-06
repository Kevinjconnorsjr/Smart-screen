"""Upcoming events from iCal (.ics) feeds: Google Calendar, iCloud, Outlook, or a local file.

Every major calendar can publish a private iCal link, so no sign-in or API keys are needed.
"""
from __future__ import annotations

import datetime as dt
import logging
import threading
import time
from pathlib import Path

import icalendar
import recurring_ical_events
import requests

log = logging.getLogger(__name__)


def _local_tz():
    return dt.datetime.now().astimezone().tzinfo


def _as_datetime(value, tz) -> tuple[dt.datetime, bool]:
    """(local datetime, is_all_day) for an iCal DTSTART/DTEND value."""
    if isinstance(value, dt.datetime):
        return (value.replace(tzinfo=tz) if value.tzinfo is None else value.astimezone(tz)), False
    return dt.datetime.combine(value, dt.time(), tz), True


def parse_events(ics_text: str | bytes, start: dt.datetime, end: dt.datetime,
                 calendar_name: str = "", color: str = "") -> list[dict]:
    tz = start.tzinfo or _local_tz()
    cal = icalendar.Calendar.from_ical(ics_text)
    events = []
    for ev in recurring_ical_events.of(cal).between(start, end):
        if str(ev.get("STATUS", "")).upper() == "CANCELLED":
            continue
        begins, all_day = _as_datetime(ev.decoded("DTSTART"), tz)
        if "DTEND" in ev:
            ends, _ = _as_datetime(ev.decoded("DTEND"), tz)
        elif "DURATION" in ev:
            ends = begins + ev.decoded("DURATION")
        else:
            ends = begins + (dt.timedelta(days=1) if all_day else dt.timedelta(0))
        events.append({
            "title": str(ev.get("SUMMARY", "(no title)")),
            "start": begins.isoformat(),
            "end": ends.isoformat(),
            "all_day": all_day,
            "location": str(ev.get("LOCATION", "")),
            "calendar": calendar_name,
            "color": color,
        })
    return events


def _fetch(url: str) -> bytes:
    if url.startswith("webcal://"):
        url = "https://" + url[len("webcal://"):]
    if url.startswith(("http://", "https://")):
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        return r.content
    return Path(url).expanduser().read_bytes()


class CalendarFeed:
    """Fetches all feeds at most every `refresh_minutes`; keeps the last good copy on errors."""

    def __init__(self, cfg: dict):
        self.cfg = cfg or {}
        self._cache: dict[str, bytes] = {}
        self._fetched_at = 0.0
        self._lock = threading.Lock()

    def _refresh(self) -> None:
        if time.time() - self._fetched_at < float(self.cfg.get("refresh_minutes", 10)) * 60:
            return
        for feed in self.cfg.get("feeds") or []:
            url = feed.get("url", "")
            if not url or "PASTE" in url:
                continue
            try:
                self._cache[url] = _fetch(url)
            except Exception as e:
                log.warning("Calendar %r could not be loaded: %s", feed.get("name", url), e)
        self._fetched_at = time.time()

    def upcoming(self, now: dt.datetime | None = None) -> list[dict]:
        with self._lock:
            self._refresh()
            cache = dict(self._cache)
        now = now or dt.datetime.now().astimezone()
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = day_start + dt.timedelta(days=int(self.cfg.get("days_ahead", 7)) + 1)
        events = []
        for feed in self.cfg.get("feeds") or []:
            data = cache.get(feed.get("url", ""))
            if data is None:
                continue
            try:
                events += parse_events(data, day_start, end, feed.get("name", ""), feed.get("color", ""))
            except Exception as e:
                log.warning("Calendar %r could not be read: %s", feed.get("name", ""), e)
        # Hide events that already finished today; all-day events stay until the day ends.
        events = [e for e in events if dt.datetime.fromisoformat(e["end"]) > now or
                  (e["all_day"] and dt.datetime.fromisoformat(e["start"]).date() == now.date())]
        events.sort(key=lambda e: (e["start"], not e["all_day"], e["title"]))
        return events[: int(self.cfg.get("max_events", 10))]
