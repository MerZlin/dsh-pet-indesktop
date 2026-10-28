"""Bounded, lifecycle-guarded reminder delivery. No business schedules live here."""
from __future__ import annotations

import logging
from collections import OrderedDict

log = logging.getLogger(__name__)


class ReminderDelivery:
    def __init__(self, quiet, scheduler, limit=128):
        self.quiet, self.scheduler, self.limit = quiet, scheduler, limit
        self._pending = OrderedDict()
        self._timer = None
        self._closed = False
        self._owner_epochs = {}
        self._slot_epochs = {}
        self._unsubscribe = quiet.subscribe(lambda active: None if active else self.flush()) if quiet else None

    def deliver(self, owner, slot, key, target, title, message, *, guard=None, on_click=None):
        if self._closed or (guard and not guard()):
            return 'inactive'
        if self.quiet and self.quiet.quiet_active():
            identity = (owner, slot, key)
            if identity not in self._pending and len(self._pending) >= self.limit:
                log.warning('reminder deferred queue full: owner=%s slot=%s', owner, slot)
                return 'unavailable'
            self._pending[identity] = (target, title, message, guard, on_click)
            if self._timer is None:
                self._timer = self.scheduler.call_every(1000, self.flush)
            return 'queued'
        # 提醒是用户自己设的，必须压过歌词等展示会话（force=True）；
        # 气泡拿不到才退到系统通知。
        if target.say(message, duration_ms=8000, force=True):
            return 'bubble'
        epoch = (self._owner_epochs.get(owner, 0), self._slot_epochs.get(slot, 0))
        def clicked():
            current = (self._owner_epochs.get(owner, 0), self._slot_epochs.get(slot, 0))
            if (not self._closed and current == epoch and (not guard or guard())
                    and on_click):
                on_click()
        if target.notify(title, message, on_click=clicked):
            return 'notification'
        log.warning('reminder delivery unavailable: owner=%s slot=%s', owner, slot)
        return 'unavailable'

    def flush(self):
        if self._closed or (self.quiet and self.quiet.quiet_active()):
            return
        pending, self._pending = self._pending, OrderedDict()
        if self._timer:
            self.scheduler.cancel(self._timer)
            self._timer = None
        groups = {}
        for (owner, slot, _key), record in pending.items():
            if record[3] is None or record[3]():
                groups.setdefault(slot, []).append((owner, record))
        for slot, records in groups.items():
            # One summary per pet, including reminders from different extensions.
            owner, selected = next((r for r in reversed(records) if r[1][4]), records[-1])
            target, _title, _text, guard, click = selected
            text = f'免打扰期间有 {len(records)} 条提醒：' + '；'.join(r[1][2] for r in records[-5:])
            self.deliver(owner, slot, 'summary', target, '提醒汇总', text, guard=guard, on_click=click)

    def _cancel(self, predicate):
        self._pending = OrderedDict((k, v) for k, v in self._pending.items() if not predicate(k))
        if not self._pending and self._timer:
            self.scheduler.cancel(self._timer)
            self._timer = None

    def cancel_owner(self, owner):
        self._owner_epochs[owner] = self._owner_epochs.get(owner, 0) + 1
        self._cancel(lambda key: key[0] == owner)

    def cancel_slot(self, slot):
        self._slot_epochs[slot] = self._slot_epochs.get(slot, 0) + 1
        self._cancel(lambda key: key[1] == slot)

    def close(self):
        self._closed = True
        self._cancel(lambda key: True)
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None
