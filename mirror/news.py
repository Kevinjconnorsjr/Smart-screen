"""Headlines from RSS/Atom feeds for the ticker at the bottom of the screen."""
from __future__ import annotations

import logging
import threading
import time

import feedparser
import requests

log = logging.getLogger(__name__)


class News:
    def __init__(self, cfg: dict | None):
        self.cfg = cfg or {}
        self._items: list[dict] = []
        self._fetched_at = 0.0
        self._lock = threading.Lock()

    def headlines(self) -> list[dict]:
        with self._lock:
            if time.time() - self._fetched_at > float(self.cfg.get("refresh_minutes", 30)) * 60:
                self._fetch()
            return list(self._items)

    def _fetch(self) -> None:
        items = []
        per_feed = int(self.cfg.get("per_feed", 5))
        for feed in self.cfg.get("feeds") or []:
            try:
                r = requests.get(feed["url"], timeout=15, headers={"User-Agent": "smart-display/1.0"})
                r.raise_for_status()
                parsed = feedparser.parse(r.content)
                for entry in parsed.entries[:per_feed]:
                    items.append({"source": feed.get("name", ""), "title": entry.get("title", "").strip()})
            except Exception as e:
                log.warning("News feed %r failed: %s", feed.get("name", feed.get("url")), e)
        if items or not self._items:
            self._items = items
        self._fetched_at = time.time()
