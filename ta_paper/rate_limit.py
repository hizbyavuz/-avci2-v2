"""Durable, shared request pacing. A restart must never clear an IP cooldown."""
from email.utils import parsedate_to_datetime
import json
import math
from pathlib import Path
import re
import threading
import time


class CooldownError(RuntimeError):
    pass


def retry_deadline(code, headers, body, now_ms):
    """Read only timing fields; never log an exchange error body or IP address."""
    deadlines = []
    value = headers.get('Retry-After', '')
    try:
        seconds = float(value)
        if math.isfinite(seconds) and seconds >= 0:
            deadlines.append(now_ms + int(seconds * 1000))
    except (TypeError, ValueError, OverflowError):
        try:
            deadlines.append(int(parsedate_to_datetime(value).timestamp() * 1000))
        except (TypeError, ValueError, OverflowError):
            pass
    if code == 418:
        for match in re.finditer(r'banned\s+until\s+(\d{10,13})', body, re.I):
            deadline = int(match[1])
            deadlines.append(deadline * 1000 if deadline < 100_000_000_000 else deadline)
    future = [x for x in deadlines if x > now_ms]
    if future:
        return max(future) + 2000, 'EXCHANGE_RETRY_TIME'
    # Binance documents IP bans lasting up to three days. Unknown is not 60s.
    fallback = 72*3600 if code == 418 else 3600 if code in (403, 451) else 60
    return now_ms + fallback*1000, 'CONSERVATIVE_FALLBACK'


class RequestGate:
    def __init__(self, path=None, legacy=None):
        self.path = Path(path) if path else None
        self.lock = threading.Lock()
        self.next_request = 0.0
        self.data = {'until_ms': 0, 'reason': 'READY', 'source': 'NONE'}
        if self.path and self.path.exists():
            try:
                value = json.loads(self.path.read_text())
                until = int(value['until_ms'])
                if until < 0:
                    raise ValueError('negative deadline')
                self.data = dict(until_ms=until, reason=str(value['reason']), source=str(value['source']))
            except (OSError, ValueError, KeyError, TypeError):
                raise CooldownError('RATE_LIMIT_STATE_INVALID') from None
        elif legacy:
            self.data = legacy
            self._persist()

    def _persist(self):
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps(self.data, sort_keys=True))
            temporary.replace(self.path)

    def state(self):
        with self.lock:
            return dict(self.data, remaining_seconds=max(0, math.ceil((self.data['until_ms']-time.time()*1000)/1000)))

    def block(self, until_ms, reason, source):
        with self.lock:
            if until_ms >= self.data['until_ms']:
                self.data = dict(until_ms=int(until_ms), reason=reason, source=source)
                self._persist()

    def acquire(self, weight):
        while True:
            with self.lock:
                if time.time()*1000 < self.data['until_ms']:
                    raise CooldownError('EXCHANGE_COOLDOWN')
                wait = self.next_request-time.monotonic()
                if wait <= 0:
                    # At most 600 estimated weight/minute; one shared budget.
                    self.next_request = time.monotonic()+max(.12, weight*.1)
                    return
            # Recheck both the shared deadline and budget after sleeping.
            time.sleep(min(wait, .25))


def request_weight(endpoint, params):
    if endpoint.endswith('/klines'):
        limit = int(params.get('limit', 120))
        return 1 if limit < 100 else 2 if limit < 500 else 5 if limit <= 1000 else 10
    if endpoint.endswith('/ticker/24hr'):
        return 1 if params.get('symbol') else 40
    if endpoint.endswith('/depth'):
        limit = int(params.get('limit', 100))
        return 2 if limit <= 50 else 5 if limit <= 100 else 10 if limit <= 500 else 20
    return 1
