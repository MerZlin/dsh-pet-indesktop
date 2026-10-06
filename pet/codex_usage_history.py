"""Local sampled quota history. Stores no account IDs, prompts or credentials."""

from __future__ import annotations
import datetime as dt
import json
import logging
import math
import os
from pathlib import Path
import time

MAX_GAP = 180
RETENTION = 9 * 86400


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def key(row):
    return (str(row.get("limitId") or "codex"), str(row.get("window") or "unknown"))


def reset_between(previous, current):
    old, new = previous.get("reset"), current.get("reset")
    return number(old) and number(new) and new > old + 60 and current["t"] >= old - 10 and previous["t"] <= old


def day(timestamp):
    return dt.datetime.fromtimestamp(timestamp).date()


class UsageHistory:
    def __init__(self, path):
        self.path = Path(path)
        self.samples = []
        self.saved = True
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("version") != 1 or not isinstance(data.get("samples"), list):
                raise ValueError("Invalid quota history")
            for sample in data["samples"][-20000:]:
                clean = self._clean(sample)
                if clean and time.time() - RETENTION <= clean["t"] <= time.time() + 120 and (not self.samples or clean["t"] > self.samples[-1]["t"]):
                    self.samples.append(clean)
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, AttributeError, OverflowError):
            logging.warning("Codex usage history could not be loaded")

    @staticmethod
    def _clean(sample):
        if not isinstance(sample, dict) or not number(sample.get("t")) or not 0 < sample["t"] < 253402214400:
            return None
        rows = []
        for row in sample.get("rows", [])[:40]:
            if not isinstance(row, dict):
                continue
            used, reset = row.get("used"), row.get("reset")
            rows.append(
                {
                    "limitId": str(row.get("limitId") or "codex")[:120],
                    "window": str(row.get("window") or "unknown")[:40],
                    "used": max(0, min(100, used)) if number(used) else None,
                    "reset": reset if number(reset) else None,
                    "minutes": row.get("minutes") if number(row.get("minutes")) else None,
                }
            )
        return {"t": float(sample["t"]), "rows": rows}

    def observe(self, snapshot, now=None):
        stamp = snapshot.get("updatedAt")
        now = time.time() if now is None else now
        if not snapshot.get("ok") or not number(stamp) or stamp > now + 120 or stamp < now - RETENTION or (self.samples and stamp <= self.samples[-1]["t"]):
            if not self.saved:
                self.save()
            return False
        rows = []
        for row in snapshot.get("windows", []):
            remaining = row.get("remainingPercent")
            rows.append(
                {
                    "limitId": row.get("limitId"),
                    "window": row.get("window"),
                    "used": 100 - remaining if number(remaining) else None,
                    "reset": row.get("resetsAt"),
                    "minutes": row.get("windowDurationMins"),
                }
            )
        clean = self._clean({"t": stamp, "rows": rows})
        if not clean:
            return False
        self.samples.append(clean)
        cutoff = now - RETENTION
        self.samples = [item for item in self.samples if item["t"] >= cutoff][-20000:]
        self.save()
        return True

    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(self.path.name + ".tmp")
            temporary.write_text(json.dumps({"version": 1, "samples": self.samples}, ensure_ascii=True, separators=(",", ":")), encoding="utf-8")
            os.replace(temporary, self.path)
            self.saved = True
        except OSError:
            if self.saved:
                logging.warning("Codex usage history could not be saved; will retry")
            self.saved = False
        return self.saved

    def series(self, row):
        target = key(row)
        result = []
        for sample in self.samples:
            item = next((value for value in sample["rows"] if key(value) == target), None)
            result.append(dict(item or {"used": None, "reset": None}, t=sample["t"]))
        return result

    def five_hour(self, row, now=None):
        """Show a fixed five-hour span beginning with observed consumption.

        A zero/refill or a confirmed reset starts a new cycle. Idle zero
        readings and jitter in server reset estimates cannot move the origin.
        """
        now = time.time() if now is None else now
        cycle, previous_known = [], None
        for point in self.series(row):
            if point["t"] > now:
                break
            if point["used"] is not None:
                if previous_known and (reset_between(previous_known, point) or point["used"] < previous_known["used"] - 0.5):
                    cycle = []
                previous_known = point
            cycle.append(point)
        first = next((point for point in cycle if point["used"] is not None and point["used"] > 0), None)
        if first is None:
            return {"start": None, "end": None, "segments": [], "resets": [], "count": 0, "waiting": True}
        start, end = first["t"], first["t"] + 18000
        segments, segment, previous = [], [], None
        for point in cycle:
            if not start <= point["t"] <= end:
                continue
            if point["used"] is None:
                if segment:
                    segments.append(segment)
                segment, previous = [], None
                continue
            if previous and point["t"] - previous["t"] > MAX_GAP:
                if segment:
                    segments.append(segment)
                segment = []
            segment.append(point)
            previous = point
        if segment:
            segments.append(segment)
        return {"start": start, "end": end, "segments": segments, "resets": [], "count": sum(len(segment) for segment in segments), "waiting": False}

    def daily(self, row, now=None):
        now = time.time() if now is None else now
        today = day(now)
        dates = [today - dt.timedelta(days=index) for index in range(6, -1, -1)]
        result = {date: {"date": date.isoformat(), "value": None, "partial": True, "coverage": 0, "samples": 0, "reset": False} for date in dates}
        previous = None
        for point in self.series(row):
            if point["t"] > now:
                break
            date = day(point["t"])
            current = result.get(date)
            if point["used"] is None:
                previous = None
                continue
            if current is not None:
                current["samples"] += 1
            if previous is not None and current is not None:
                gap = point["t"] - previous["t"]
                same_day = day(previous["t"]) == date
                reset = reset_between(previous, point)
                delta = point["used"] - previous["used"]
                # Across an unobserved midnight the consumed amount cannot be
                # assigned to a particular day. Never invent such a split.
                if (same_day or gap <= MAX_GAP) and delta >= 0 and not reset:
                    current["value"] = (current["value"] or 0) + delta
                elif reset:
                    current["reset"] = True
                    if gap <= MAX_GAP and previous["t"] <= previous["reset"] <= point["t"]:
                        current["value"] = (current["value"] or 0) + point["used"]
                if gap <= MAX_GAP and delta >= 0 and not reset:
                    start = previous["t"]
                    while start < point["t"]:
                        start_day = day(start)
                        midnight = dt.datetime.combine(start_day + dt.timedelta(days=1), dt.time()).timestamp()
                        end = min(midnight, point["t"])
                        if start_day in result:
                            result[start_day]["coverage"] += end - start
                        start = end
            previous = point
        for date, value in result.items():
            midnight = dt.datetime.combine(date, dt.time()).timestamp()
            next_midnight = dt.datetime.combine(date + dt.timedelta(days=1), dt.time()).timestamp()
            expected = min(now, next_midnight) - midnight
            value["partial"] = value["coverage"] < max(0, expected - MAX_GAP) or value["reset"]
            if value["value"] is not None:
                value["value"] = round(value["value"], 3)
        return list(result.values())
