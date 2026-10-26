"""Authenticated public-data relay. It cannot trade, choose hosts or clear bans."""
import fcntl
import hmac
from http import HTTPStatus
import json
import os
from pathlib import Path
import re
import threading
import urllib.parse

from .io import Binance, DataUnavailable

PARAMETERS = {
    '/fapi/v1/time': set(),
    '/fapi/v1/exchangeInfo': set(),
    '/fapi/v1/ticker/24hr': {'symbol'},
    '/fapi/v1/klines': {'symbol', 'interval', 'limit', 'startTime', 'endTime'},
    '/fapi/v1/depth': {'symbol', 'limit'},
    '/fapi/v1/fundingRate': {'symbol', 'limit', 'startTime', 'endTime'},
}
INTERVALS = {'1m', '3m', '5m', '15m', '30m', '1h', '2h', '4h', '6h',
             '8h', '12h', '1d', '3d', '1w', '1M'}


def parameters(path, query):
    if path not in PARAMETERS or len(query) > 1024:
        raise ValueError('ENDPOINT_REFUSED')
    pairs = urllib.parse.parse_qsl(query, keep_blank_values=True, strict_parsing=True,
                                  max_num_fields=8)
    values = dict(pairs)
    if len(values) != len(pairs) or not values.keys() <= PARAMETERS[path]:
        raise ValueError('PARAMETERS_REFUSED')
    if 'symbol' in values:
        symbol = values['symbol']
        if not 1 <= len(symbol) <= 40 or not all(c.isalnum() or c == '_' for c in symbol):
            raise ValueError('SYMBOL_INVALID')
    if path.endswith(('/klines', '/depth')) and not values.get('symbol'):
        raise ValueError('SYMBOL_REQUIRED')
    if path.endswith('/klines') and values.get('interval') not in INTERVALS:
        raise ValueError('INTERVAL_INVALID')
    for key in ('startTime', 'endTime', 'limit'):
        if key in values:
            if not re.fullmatch(r'[0-9]{1,13}', values[key]):
                raise ValueError('NUMBER_INVALID')
            values[key] = int(values[key])
    if 'limit' in values:
        limit = values['limit']
        if not 1 <= limit <= (1500 if path.endswith('/klines') else 1000):
            raise ValueError('LIMIT_INVALID')
        if path.endswith('/depth') and limit not in (5, 10, 20, 50, 100, 500, 1000):
            raise ValueError('LIMIT_INVALID')
    return values


class Egress:
    def __init__(self, token, client, process_lock=None):
        if not re.fullmatch(r'[a-f0-9]{64}', token):
            raise ValueError('EGRESS_TOKEN_INVALID')
        self.token = ('Bearer '+token).encode('ascii')
        self.client = client
        self.process_lock = process_lock  # Keep flock alive for this worker.
        self.request_lock = threading.Lock()

    def __call__(self, env, start_response):
        def reply(code, payload, headers=()):
            body = json.dumps(payload, separators=(',', ':'), allow_nan=False).encode()
            start_response(f'{code} {HTTPStatus(code).phrase}',
                           [('Content-Type', 'application/json'),
                            ('Content-Length', str(len(body))),
                            ('Cache-Control', 'no-store'), *headers])
            return [body]

        supplied = env.get('HTTP_AUTHORIZATION', '').encode('utf-8')
        if not hmac.compare_digest(supplied, self.token):
            return reply(401, {'error': 'UNAUTHORIZED'})
        if env.get('REQUEST_METHOD') != 'GET' or env.get('CONTENT_LENGTH', '') not in ('', '0'):
            return reply(405, {'error': 'READ_ONLY'})
        path = env.get('PATH_INFO', '')
        # Health is local only: no DNS lookup or Binance request.
        if path == '/healthz':
            return reply(200, {'status': 'LISTENING', 'cooldown': self.client.gate.state(),
                               'usage': self.client.usage()})
        try:
            params = parameters(path, env.get('QUERY_STRING', ''))
        except ValueError:
            return reply(400, {'error': 'REQUEST_REFUSED'})
        if not self.request_lock.acquire(blocking=False):
            return reply(503, {'error': 'EGRESS_BUSY'})
        try:
            try:
                payload = self.client.get(path, **params)
            except DataUnavailable:
                state = self.client.gate.state()
                if state['remaining_seconds']:
                    codes = {'BINANCE_HTTP_'+str(n): n for n in (418, 429, 403, 451)}
                    code = codes.get(state['reason'], 429)
                    # Exact deadline survives transport and worker restarts. No IP/body disclosure.
                    return reply(code, {'msg': 'banned until '+str(state['until_ms'])},
                                 [('Retry-After', str(state['remaining_seconds']))])
                return reply(502, {'error': 'UPSTREAM_UNAVAILABLE'})
            headers = []
            if self.client.observed_weight_window == self.client.now()//60000:
                headers.append(('X-MBX-USED-WEIGHT-1M', str(self.client.observed_weight)))
            return reply(200, payload, headers)
        finally:
            self.request_lock.release()


def create_app():
    token = os.environ['TA_EGRESS_TOKEN']
    directory = Path(os.environ.get('TA_EGRESS_STATE_DIR', '/state'))
    # Provisioning must copy the current known ban into this file before boot.
    # Missing/corrupt state fails closed instead of treating a new host as unbanned.
    state = directory/'cooldown.json'
    if not state.is_file():
        raise RuntimeError('EGRESS_COOLDOWN_STATE_REQUIRED')
    process_lock = (directory/'worker.lock').open('a')
    try:
        fcntl.flock(process_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        client = Binance(state, direct=True)
        app = Egress(token, client, process_lock)
    except BaseException:
        process_lock.close()
        raise
    print('TA_EGRESS_BOOT '+json.dumps({'cooldown': client.gate.state(),
                                       'transport': client.transport.mode}), flush=True)
    return app
