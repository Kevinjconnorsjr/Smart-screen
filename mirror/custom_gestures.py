"""Gestures you teach the screen yourself.

The gesture model already finds the 21 points of the hand (wrist, knuckles, finger tips). Teaching a
gesture records those points for a few seconds while you hold the pose; recognising it later is a
nearest-neighbour match against the recorded samples. No training run, no computer needed, and it
works the moment you finish teaching.

- Poses are compared after removing position and size, so it doesn't matter where your hand is in
  the picture or how far from the camera. Rotation is kept on purpose (thumb up and thumb down
  are different gestures); small tilts are fine because teaching records you for a few seconds.
- Left and right hands count as the same pose (the left hand is mirrored first).
- Only still poses can be taught (not movements like swipes).
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path

import numpy as np

SAMPLES_WANTED = 60          # about 4 seconds at 15 checks per second
MIN_SAMPLES = 15


def features(landmarks, left_hand: bool = False) -> np.ndarray:
    """21 hand points (objects with .x .y .z, or tuples) -> a pose vector independent of position and size."""
    pts = np.array([(p.x, p.y, p.z) if hasattr(p, "x") else p for p in landmarks], dtype=np.float64)
    if left_hand:
        pts[:, 0] = -pts[:, 0]
    pts -= pts[0]                              # wrist at the origin
    size = np.linalg.norm(pts[9]) or 1.0       # wrist -> middle-finger knuckle = 1
    return (pts[1:] / size).ravel()


def slug(name: str) -> str:
    return "c_" + (re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:30] or "gesture")


class CustomGestures:
    """Taught gestures, saved in data/custom_gestures.json."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self.gestures: dict[str, dict] = {}   # id -> {name, emoji, samples: [[...]]}
        self._matrix: np.ndarray | None = None
        self._labels: list[str] = []
        self._limits: dict[str, float] = {}
        if path.exists():
            try:
                self.gestures = json.loads(path.read_text())
            except Exception:
                self.gestures = {}
        self._rebuild()

    # ---------- storage ----------
    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.gestures))
        tmp.replace(self.path)

    def _rebuild(self) -> None:
        rows, labels, limits = [], [], {}
        for gid, g in self.gestures.items():
            samples = np.array(g["samples"], dtype=np.float64)
            if len(samples) < 2:
                continue
            # How spread out this gesture's own samples are sets how close a new pose must be.
            d = np.linalg.norm(samples[:, None, :] - samples[None, :, :], axis=2)
            typical = float(np.median(d[np.triu_indices(len(samples), 1)]))
            limits[gid] = max(typical * 2.5, 0.35)
            rows.append(samples)
            labels += [gid] * len(samples)
        self._matrix = np.vstack(rows) if rows else None
        self._labels, self._limits = labels, limits

    def add(self, name: str, emoji: str, samples: list[np.ndarray]) -> str:
        if len(samples) < MIN_SAMPLES:
            raise ValueError("Not enough of your hand was seen. Try again with your hand clearly in view.")
        gid = slug(name)
        with self._lock:
            self.gestures[gid] = {"name": name.strip()[:40], "emoji": (emoji or "").strip()[:8],
                                  "samples": [s.round(4).tolist() for s in samples]}
            self._save()
            self._rebuild()
        return gid

    def remove(self, gid: str) -> bool:
        with self._lock:
            if self.gestures.pop(gid, None) is None:
                return False
            self._save()
            self._rebuild()
        return True

    def labels(self) -> dict[str, str]:
        """{id: "🤘 Rock on"}"""
        return {gid: f"{g.get('emoji') or '✋'} {g['name']}".strip() for gid, g in self.gestures.items()}

    # ---------- recognising ----------
    def match(self, vec: np.ndarray) -> tuple[str | None, float]:
        """(gesture id, confidence 0-1) of the closest taught gesture, or (None, 0)."""
        with self._lock:
            matrix, labels, limits = self._matrix, self._labels, self._limits
        if matrix is None:
            return None, 0.0
        dists = np.linalg.norm(matrix - vec, axis=1)
        best = int(np.argmin(dists))
        gid, dist = labels[best], float(dists[best])
        limit = limits[gid]
        if dist > limit:
            return None, 0.0
        # Must also be clearly closer to this gesture than to any other taught one.
        others = [float(dists[i]) for i, g in enumerate(labels) if g != gid]
        if others and min(others) < dist * 1.15:
            return None, 0.0
        return gid, round(1.0 - 0.5 * dist / limit, 3)  # 1.0 at a perfect match, 0.5 at the limit


class Teacher:
    """One teaching session at a time: collects pose samples from the gesture watcher."""

    def __init__(self):
        self.active = False
        self.samples: list[np.ndarray] = []
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            self.active, self.samples = True, []

    def offer(self, vec: np.ndarray) -> None:
        with self._lock:
            if self.active and len(self.samples) < SAMPLES_WANTED:
                self.samples.append(vec)

    def stop(self) -> list[np.ndarray]:
        with self._lock:
            self.active = False
            out, self.samples = self.samples, []
        return out

    @property
    def count(self) -> int:
        return len(self.samples)
