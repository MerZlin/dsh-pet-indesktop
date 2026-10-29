"""One bounded speech channel; synthesis completion is not playback completion."""
from __future__ import annotations

import itertools
import logging
import threading
from collections import deque
from dataclasses import dataclass, field

from ..contracts.speech import SpeechError

log = logging.getLogger(__name__)


@dataclass
class _Job:
    token: str
    request: object
    owner: str
    slot: int | None
    guard: object
    on_done: object
    on_error: object
    on_finished: object
    epoch: tuple = ()
    done: threading.Event = field(default_factory=threading.Event)


class CoreSpeechPort:
    def __init__(self, service, audio, *, scheduler=None, quiet=None, queue_limit=16):
        self.service, self.audio = service, audio
        self._scheduler, self._quiet = scheduler, quiet
        self._counter = itertools.count(1)
        self._lock = threading.RLock()
        self._jobs = {}
        self._queue = deque()
        self._worker = None
        self._active = None
        self._limit = max(1, queue_limit)
        self._rules = {}
        self._closed = False
        self._epoch = 0
        self._owner_epochs = {}
        self._slot_epochs = {}
        self._unsubscribe = quiet.subscribe(self._quiet_changed) if quiet else None

    def providers(self):
        return self.service.providers()

    def preflight(self, provider=''):
        return self.service.preflight(provider)

    def synthesize(self, request):
        return self.service.synthesize(request)

    def cache_dir(self):
        return self.service.cache_dir()

    def set_secret(self, provider, field, value):
        self.service.set_secret(provider, field, value)

    def secret_status(self, provider):
        return self.service.secret_status(provider)

    def register_yield(self, owner, callback):
        self._rules[owner] = callback
        def revoke():
            if self._rules.get(owner) is callback:
                self._rules.pop(owner, None)
        return revoke

    def should_yield(self, moment, *, requester=''):
        for owner, rule in tuple(self._rules.items()):
            if owner != requester:
                try:
                    if rule(moment):
                        return True
                except Exception:
                    log.exception('speech yield rule failed: %s', owner)
        return False

    def _quiet_changed(self, active):
        if active:
            self.stop()

    def _identity_epoch(self, job):
        return (self._epoch, self._owner_epochs.get(job.owner, 0),
                self._slot_epochs.get(job.slot, 0))

    def _live(self, job):
        try:
            return (not self._closed and job.epoch == self._identity_epoch(job)
                    and (job.guard is None or job.guard()))
        except Exception:
            log.exception('speech lifecycle guard failed')
            return False

    def _allowed(self, job):
        try:
            return (self._live(job) and not job.done.is_set()
                    and not (self._quiet and self._quiet.quiet_active()))
        except Exception:
            log.exception('speech lifecycle guard failed')
            return False

    def speak(self, request, *, on_done=None, on_error=None, on_finished=None,
              owner='', slot=None, guard=None):
        job = _Job(f'speech-{next(self._counter)}', request, owner, slot, guard,
                   on_done, on_error, on_finished)
        with self._lock:
            job.epoch = self._identity_epoch(job)
            error = None
            if not self._allowed(job):
                error = SpeechError('unavailable', 'speech is inactive or quiet')
            elif len(self._jobs) >= self._limit:
                error = SpeechError('rate_limit', 'speech queue full')
            if error:
                self._dispatch(lambda: self._callback(on_error, error) if self._live(job) else None)
                return job.token
            self._jobs[job.token] = job
            self._queue.append(job)
            if self._worker is None:
                self._worker = threading.Thread(target=self._run, name='petcore-speech', daemon=True)
                self._worker.start()
        return job.token

    def _dispatch(self, callback):
        if self._scheduler is None:
            callback()  # compatibility for non-Qt audio adapters
            return True
        try:
            self._scheduler.call_later(0, callback)
            return True
        except Exception:
            log.debug('speech scheduler unavailable', exc_info=True)
            return False

    def _run(self):
        while True:
            with self._lock:
                if not self._queue:
                    self._worker = None
                    return
                job = self._queue.popleft()
            if job.done.is_set():
                continue
            try:
                result = self.service.synthesize(job.request)
            except Exception as exc:
                error = exc if isinstance(exc, SpeechError) else SpeechError('unavailable', type(exc).__name__)
                ok = self._dispatch(lambda j=job, e=error: self._finish(j, j.on_error, e))
            else:
                ok = self._dispatch(lambda j=job, r=result: self._play(j, r))
            if not ok:
                self._forget(job)
            job.done.wait()  # canceled synthesis must return before another worker can start

    def _play(self, job, result):
        if not self._allowed(job):
            self._forget(job)
            return
        self._active = job.token
        tracked = getattr(self.audio, 'play_tracked', None)
        # 播放音量必须取**合成结果里的有效值**：``SpeechRequest.volume`` 留空是
        # 「沿用语音页配置」，把它（None）交给音频层会让 float() 抛 TypeError，
        # 整句语音在 on_error 为 None 时静默消失。
        volume = float(getattr(result, 'volume', 1.0) or 0.0)
        try:
            if tracked:
                played = tracked(result.path, volume=volume,
                    on_finished=lambda: self._finish(job, job.on_finished, result),
                    on_error=lambda error: self._finish(job, job.on_error, error))
            else:
                played = self.audio.play(result.path, volume=volume)
            if played is False:
                raise SpeechError('unavailable', 'audio channel unavailable')
        except Exception as exc:
            error = exc if isinstance(exc, SpeechError) else SpeechError('unavailable', type(exc).__name__)
            self._finish(job, job.on_error, error)
            return
        if tracked:
            if not job.done.is_set():
                self._callback(job.on_done, result)
        else:
            # Legacy adapters have no completion signal. Never fabricate on_finished.
            self._finish(job, job.on_done, result)

    @staticmethod
    def _callback(callback, value):
        if callback:
            try:
                callback(value)
            except Exception:
                log.exception('speech callback failed')

    def _forget(self, job):
        with self._lock:
            self._jobs.pop(job.token, None)
            if self._active == job.token:
                self._active = None
            job.done.set()

    def _finish(self, job, callback, value):
        with self._lock:
            if job.done.is_set():
                return
            allowed = self._allowed(job)
            self._forget(job)
        if allowed:
            self._callback(callback, value)

    def stop(self, token=''):
        with self._lock:
            if not token:
                self._epoch += 1
            jobs = [j for t, j in self._jobs.items() if not token or t == token]
            active = self._active
            if active and any(j.token == active for j in jobs):
                stop = getattr(self.audio, 'stop', None)
                if stop:
                    # Enqueue before waking the worker, so a replacement can
                    # never start ahead of cancellation of the prior channel.
                    self._dispatch(stop)
            for job in jobs:
                self._forget(job)
            self._queue = deque(j for j in self._queue if not j.done.is_set())

    def cancel_owner(self, owner):
        self._owner_epochs[owner] = self._owner_epochs.get(owner, 0) + 1
        self._rules.pop(owner, None)
        with self._lock:
            tokens = [j.token for j in self._jobs.values() if j.owner == owner]
        for token in tokens:
            self.stop(token)

    def cancel_slot(self, slot):
        self._slot_epochs[slot] = self._slot_epochs.get(slot, 0) + 1
        with self._lock:
            tokens = [j.token for j in self._jobs.values() if j.slot == slot]
        for token in tokens:
            self.stop(token)

    def playing(self):
        with self._lock:
            return bool(self._jobs)

    def close(self):
        self._closed = True
        self.stop()
        self._rules.clear()
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None
