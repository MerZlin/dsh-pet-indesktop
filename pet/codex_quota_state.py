"""Durable, offline quota threshold tracking. No inference or network access."""

from __future__ import annotations

import copy
import json
import logging
import math
import os
from pathlib import Path
import time

THRESHOLDS = (75, 50, 25)


def finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def window_key(row):
    return json.dumps([row.get("limitId") or "codex", row.get("window") or "unknown"], ensure_ascii=True)


class QuotaThresholdState:
    def __init__(self, path):
        self.path = Path(path)
        self.windows = {}
        self.pending = []
        self.last_update = 0
        self.dirty = False
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("version") == 1 and isinstance(data.get("windows"), dict):
                self.windows = {key: value for key, value in data["windows"].items() if isinstance(value, dict) and isinstance(value.get("notified"), list)}
                self.pending = [
                    value
                    for value in data.get("pending", [])
                    if isinstance(value, dict)
                    and value.get("key") in self.windows
                    and isinstance(value.get("row"), dict)
                    and isinstance(value.get("thresholds"), list)
                    and value["thresholds"]
                    and all(item in THRESHOLDS for item in value["thresholds"])
                    and finite_number(value["row"].get("remainingPercent"))
                ]
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def observe(self, snapshot):
        """Queue crossed thresholds, merging jumps into one notice per window."""
        if not snapshot.get("ok"):
            return
        updated = snapshot.get("updatedAt")
        if finite_number(updated):
            if updated < self.last_update:
                return
            self.last_update = updated
        before = copy.deepcopy((self.windows, self.pending))
        for row in snapshot.get("windows", []):
            remaining = row.get("remainingPercent")
            if not finite_number(remaining):
                continue
            remaining = max(0, min(100, remaining))
            key = window_key(row)
            old = self.windows.get(key, {})
            reset = row.get("resetsAt")
            reset = reset if finite_number(reset) and reset > 0 else old.get("reset")
            previous = old.get("remaining")
            old_reset = old.get("reset")
            now = updated if finite_number(updated) else time.time()
            # App-server reset epochs can drift by a few seconds on each read.
            # Rearm only after the previous period ended and a later reset was
            # reported, or when the allowance really refilled to 100%.
            new_cycle = bool(old and finite_number(reset) and finite_number(old_reset) and reset > old_reset + 60 and now >= old_reset - 10)
            if finite_number(previous) and previous < 100 and remaining == 100:
                new_cycle = True
            if new_cycle:
                old = {}
                self.pending = [event for event in self.pending if event["key"] != key]
            elif finite_number(old_reset):
                # Keep one stable period identity, rather than accumulating jitter.
                reset = old_reset
            notified = set(old.get("notified", [])) & set(THRESHOLDS)
            crossed = [threshold for threshold in THRESHOLDS if remaining <= threshold and threshold not in notified]
            notified.update(crossed)
            self.windows[key] = {"reset": reset, "remaining": remaining, "notified": sorted(notified, reverse=True)}
            queued = next((event for event in self.pending if event["key"] == key), None)
            if queued is not None:
                queued["row"] = dict(row, remainingPercent=remaining)
                queued["thresholds"] = sorted(set(queued["thresholds"]) | set(crossed), reverse=True)
                # Do not display a delayed warning after the allowance recovered.
                if remaining > max(queued["thresholds"]):
                    self.pending.remove(queued)
            elif crossed:
                self.pending.append({"key": key, "row": dict(row, remainingPercent=remaining), "thresholds": crossed})
        if before != (self.windows, self.pending):
            self.dirty = True
        self.save()

    def acknowledge(self, count):
        del self.pending[:count]
        self.dirty = True
        self.save()

    def save(self):
        if not self.dirty:
            return True
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(self.path.name + ".tmp")
            temporary.write_text(json.dumps({"version": 1, "windows": self.windows, "pending": self.pending}, ensure_ascii=False, indent=2), encoding="utf-8")
            for attempt in range(4):
                try:
                    os.replace(temporary, self.path)
                    self.dirty = False
                    return True
                except OSError as error:
                    if getattr(error, "winerror", None) not in (5, 32, 33) or attempt == 3:
                        raise
                    time.sleep(0.05)
        except OSError:
            logging.warning("Codex quota reminder state could not be saved")
        return False
