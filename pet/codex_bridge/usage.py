"""Read-only Codex quota access, without exposing account identifiers or tokens."""

import math
import threading
import time

from .rpc import Rpc


def window_label(minutes, fallback):
    if minutes == 10080:
        return "每週"
    if isinstance(minutes, (int, float)) and not isinstance(minutes, bool) and minutes > 0:
        if minutes % 1440 == 0:
            return "%g 天" % (minutes / 1440)
        if minutes % 60 == 0:
            return "%g 小時" % (minutes / 60)
        return "%g 分鐘" % minutes
    return fallback


def normalize_usage(response, now=None):
    """Missing windows stay unknown; percentages always describe remaining quota."""
    buckets = response.get("rateLimitsByLimitId")
    if not isinstance(buckets, dict) or not buckets:
        legacy = response.get("rateLimits")
        buckets = {(legacy or {}).get("limitId") or "codex": legacy} if isinstance(legacy, dict) else {}
    rows = []
    blocked = response.get("ordinaryUsageAllowed") is False
    for key in sorted(buckets, key=lambda key: (key != "codex", key)):
        bucket = buckets[key]
        if not isinstance(bucket, dict):
            continue
        blocked |= bool(bucket.get("rateLimitReachedType"))
        count = 0
        for field, fallback in [("primary", "短期"), ("secondary", "長期")]:
            window = bucket.get(field)
            if not isinstance(window, dict):
                continue
            used = window.get("usedPercent")
            remaining = None
            if isinstance(used, (int, float)) and not isinstance(used, bool) and math.isfinite(used):
                remaining = max(0, min(100, 100 - used))
            minutes = window.get("windowDurationMins")
            label = window_label(minutes, fallback)
            if key != "codex":
                label = str(bucket.get("limitName") or key) + " · " + label
            reset = window.get("resetsAt")
            rows.append(
                {
                    "limitId": key,
                    "window": field,
                    "label": label,
                    "remainingPercent": remaining,
                    "windowDurationMins": minutes,
                    "resetsAt": reset if isinstance(reset, (int, float)) else None,
                }
            )
            count += 1
        if not count:
            rows.append(
                {
                    "limitId": key,
                    "window": "unknown",
                    "label": "Codex" if key == "codex" else str(bucket.get("limitName") or key),
                    "remainingPercent": None,
                    "windowDurationMins": None,
                    "resetsAt": None,
                }
            )
    return {
        "ok": True,
        "updatedAt": time.time() if now is None else now,
        "windows": rows,
        "usageBlocked": blocked,
        "ordinaryUsageAllowed": response.get("ordinaryUsageAllowed"),
    }


class UsageReader:
    def __init__(self, config, ttl=30):
        self.config, self.ttl = config, ttl
        self.lock = threading.Lock()
        self.snapshot = None
        self.valid_until = 0

    def read(self):
        with self.lock:
            now = time.time()
            reset_due = self.snapshot and any(
                row.get("resetsAt") is not None and self.snapshot["updatedAt"] < row["resetsAt"] <= now for row in self.snapshot["windows"]
            )
            if self.snapshot and time.monotonic() < self.valid_until and not reset_due:
                return self.snapshot
            rpc = Rpc(self.config["codexExecutable"], startup_timeout=8)
            try:
                raw = rpc.call("account/rateLimits/read", {"excludeResetCreditDetails": True, "supportsLunaReserve": False}, timeout=8)
                self.snapshot = normalize_usage(raw)
                self.valid_until = time.monotonic() + self.ttl
                return self.snapshot
            finally:
                rpc.close()
