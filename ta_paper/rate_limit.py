"""Durable, shared request pacing. A restart must never clear an IP cooldown."""
from email.utils import parsedate_to_datetime
import json
import math
from pathlib import Path
import re
import threading
import time

ASSUMED_SOURCES = frozenset(('LEGACY_UNKNOWN_UPPER_BOUND', 'CONSERVATIVE_FALLBACK'))

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
        self.recheck_permit = False
        self.data = {'until_ms': 0, 'reason': 'READY', 'source': 'NONE'}
        if self.path and self.path.exists():
            try:
                value = json.loads(self.path.read_text())
                until = int(value['until_ms'])
                if until < 0:
                    raise ValueError('negative deadline')
                checks = value.get('recheck_ids', [])
                if not isinstance(checks, list) or not all(isinstance(x, str) for x in checks):
                    raise ValueError('invalid recheck history')
                self.data = dict(until_ms=until, reason=str(value['reason']), source=str(value['source']),
                                 recheck_ids=checks)
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
            current_known = self.data['source'] == 'EXCHANGE_RETRY_TIME'
            if current_known and self.data['until_ms'] > time.time()*1000 and source != 'EXCHANGE_RETRY_TIME':
                return
            server_replaces_assumption = source == 'EXCHANGE_RETRY_TIME' and self.data['source'] in ASSUMED_SOURCES
            if server_replaces_assumption or until_ms >= self.data['until_ms']:
                self.data = dict(until_ms=int(until_ms), reason=reason, source=source,
                                 recheck_ids=self.data.get('recheck_ids', []))
                self._persist()

    def reserve_unknown_recheck(self, request_id):
        """Consume one explicit request durably, only for our assumed 418 timer."""
        with self.lock:
            checks = self.data.get('recheck_ids', [])
            if (not request_id or request_id in checks or self.data['source'] not in ASSUMED_SOURCES
                    or self.data['reason'] != 'BINANCE_HTTP_418'
                    or self.data['until_ms'] <= time.time()*1000):
                return False
            self.data['recheck_ids'] = checks+[request_id]
            self._persist()
            self.recheck_permit = True
            return True

    def confirm_unknown_recheck(self):
        with self.lock:
            if self.data['source'] not in ASSUMED_SOURCES or self.data['reason'] != 'BINANCE_HTTP_418':
                raise CooldownError('RECHECK_STATE_CHANGED')
            self.data.update(until_ms=0, reason='READY', source='VERIFIED_TIME_RESPONSE')
            self._persist()

    def acquire(self, weight, unknown_recheck=False):
        while True:
            with self.lock:
                permitted = (unknown_recheck and self.recheck_permit
                             and self.data['source'] in ASSUMED_SOURCES
                             and self.data['reason'] == 'BINANCE_HTTP_418')
                if unknown_recheck and not permitted:
                    raise CooldownError('RECHECK_NOT_AUTHORIZED')
                if time.time()*1000 < self.data['until_ms'] and not permitted:
                    raise CooldownError('EXCHANGE_COOLDOWN')
                wait = self.next_request-time.monotonic()
                if wait <= 0:
                    # At most 600 estimated weight/minute; one shared budget.
                    self.next_request = time.monotonic()+max(.12, weight*.1)
                    if permitted:
                        self.recheck_permit = False
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
