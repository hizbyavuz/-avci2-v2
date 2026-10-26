from contextlib import redirect_stdout
from email.message import Message
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
from wsgiref.simple_server import make_server, WSGIRequestHandler

from ta_paper.egress import Egress, create_app, parameters
from ta_paper.io import Binance, DataUnavailable
from ta_paper.transport import MarketTransport
from prepare_ta_egress import prepare

TOKEN = 'a'*64


def invoke(app, path='/fapi/v1/time', query='', token=TOKEN, method='GET', **extra):
    captured = {}
    def start(status, headers):
        captured.update(status=int(status.split()[0]), headers=dict(headers))
    env = dict(PATH_INFO=path, QUERY_STRING=query, REQUEST_METHOD=method,
               HTTP_AUTHORIZATION='Bearer '+token, **extra)
    captured['body'] = json.loads(b''.join(app(env, start)))
    return captured


class EgressTests(unittest.TestCase):
    def test_provisioning_preserves_seed_and_never_overwrites_existing_state(self):
        with tempfile.TemporaryDirectory() as directory:
            seed = Path(directory)/'seed.json'
            value = {'until_ms': int(time.time()*1000)+300_000,
                     'reason': 'BINANCE_HTTP_418', 'source': 'EXCHANGE_RETRY_TIME'}
            seed.write_text(json.dumps(value))
            root = Path(directory)/'prepared'
            result = prepare(root, '127.0.0.1', seed, os.getuid(), os.getgid())
            self.assertEqual(result['status'], 'PREPARED_NOT_STARTED')
            self.assertEqual(json.loads((root/'state/cooldown.json').read_text()), value)
            self.assertEqual((root/'egress.env').stat().st_mode & 0o777, 0o600)
            token = (root/'egress.env').read_text().strip().split('=', 1)[1]
            transport = MarketTransport(environ={'TA_PAPER_EGRESS_URL': 'https://127.0.0.1',
                       'TA_PAPER_EGRESS_TOKEN': token,
                       'TA_PAPER_EGRESS_CA_PEM': (root/'tls/server.crt').read_text()})
            self.assertEqual(transport.mode, 'PRIVATE_EGRESS')
            with self.assertRaises(FileExistsError):
                prepare(root, '127.0.0.1', seed, os.getuid(), os.getgid())
            self.assertEqual(json.loads((root/'state/cooldown.json').read_text()), value)

    def test_health_and_auth_never_contact_binance(self):
        client = Binance(direct=True)
        app = Egress(TOKEN, client)
        with patch.object(client, 'get') as get:
            self.assertEqual(invoke(app, token='wrong')['status'], 401)
            health = invoke(app, path='/healthz')
            self.assertEqual(health['body']['usage']['local_requests_since_start'], 0)
            self.assertEqual(invoke(app, method='POST')['status'], 405)
            self.assertEqual(invoke(app, CONTENT_LENGTH='12')['status'], 405)
            get.assert_not_called()

    def test_relay_is_not_an_open_proxy_and_never_accepts_order_or_host(self):
        client = Binance(direct=True)
        app = Egress(TOKEN, client)
        with patch.object(client, 'get') as get:
            for path, query in [('/fapi/v1/order', ''), ('https://evil.test/', ''),
                                ('/fapi/v1/time', 'url=https://evil.test'),
                                ('/fapi/v1/klines', 'symbol=A&symbol=B&interval=1m'),
                                ('/fapi/v1/depth', 'symbol=X&limit=999'),
                                ('/fapi/v1/klines', 'symbol=X&interval=1m&limit=999999'),
                                ('/fapi/v1/time', 'signature=x'),
                                ('/fapi/v1/time', 'bad'),
                                ('/fapi/v1/time', 'a=1&'*12)]:
                with self.subTest(path=path, query=query):
                    self.assertEqual(invoke(app, path, query)['status'], 400)
            get.assert_not_called()

    def test_unicode_market_symbols_are_preserved(self):
        self.assertEqual(parameters('/fapi/v1/klines',
                         'symbol=%E5%B8%81USDT&interval=30m&limit=3'),
                         {'symbol': '币USDT', 'interval': '30m', 'limit': 3})

    def test_server_requires_persisted_cooldown_and_single_worker(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ,
                {'TA_EGRESS_TOKEN': TOKEN, 'TA_EGRESS_STATE_DIR': directory}):
            with self.assertRaisesRegex(RuntimeError, 'STATE_REQUIRED'):
                create_app()
            path = Path(directory)/'cooldown.json'
            path.write_text('invalid')
            with self.assertRaisesRegex(Exception, 'STATE_INVALID'):
                create_app()
            until = int(time.time()*1000)+300_000
            path.write_text(json.dumps({'until_ms': until, 'reason': 'BINANCE_HTTP_418',
                                        'source': 'EXCHANGE_RETRY_TIME'}))
            with redirect_stdout(StringIO()):
                app = create_app()
            try:
                with self.assertRaises(BlockingIOError):
                    create_app()
                with patch('urllib.request.build_opener') as network:
                    blocked = invoke(app)
                    self.assertEqual(blocked['status'], 418)
                    self.assertIn(str(until), blocked['body']['msg'])
                    network.assert_not_called()
            finally:
                app.process_lock.close()

    def test_upstream_ban_survives_restart_and_blocks_subsequent_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'cooldown.json'
            until = int(time.time()*1000)+300_000
            error = urllib.error.HTTPError('https://fapi.binance.com', 418, 'blocked',
                        Message(), BytesIO(f'IP secret-address banned until {until}'.encode()))
            with patch('urllib.request.build_opener') as network, redirect_stdout(StringIO()) as out:
                network.return_value.open.side_effect = error
                app = Egress(TOKEN, Binance(path, direct=True))
                self.assertEqual(invoke(app)['status'], 418)
                restarted = Egress(TOKEN, Binance(path, direct=True))
                result = invoke(restarted)
                self.assertEqual(result['status'], 418)
                self.assertEqual(network.return_value.open.call_count, 1)
                self.assertNotIn('secret-address', out.getvalue()+json.dumps(result))

    def test_response_carries_data_and_upstream_weight_without_modifying_values(self):
        client = Binance(direct=True)
        client.observed_weight = 12
        client.observed_weight_window = client.now()//60000
        data = [[123, '1.000001', '1.002', '0.999', '1.001', '23.4']]
        with patch.object(client, 'get', return_value=data) as get:
            result = invoke(Egress(TOKEN, client), '/fapi/v1/klines',
                            'symbol=TESTUSDT&interval=30m&limit=3')
            get.assert_called_once_with('/fapi/v1/klines', symbol='TESTUSDT', interval='30m', limit=3)
            self.assertEqual(result['body'], data)
            self.assertEqual(result['headers']['X-MBX-USED-WEIGHT-1M'], '12')

    def test_private_transport_is_exclusive_and_configuration_fails_closed(self):
        for values in [dict(TA_PAPER_EGRESS_URL='http://x'),
                       dict(TA_PAPER_EGRESS_TOKEN=TOKEN),
                       dict(TA_PAPER_EGRESS_URL='https://x', TA_PAPER_EGRESS_TOKEN=TOKEN),
                       dict(TA_PAPER_EGRESS_URL='https://user:pass@x', TA_PAPER_EGRESS_TOKEN=TOKEN)]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                MarketTransport(environ=values)
        direct = MarketTransport(direct=True)
        self.assertIsNone(direct.token)
        self.assertEqual(direct.handlers()[0].proxies, {})


class TLSIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.cert = Path(cls.folder.name)/'server.crt'
        cls.key = Path(cls.folder.name)/'server.key'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                        '-keyout', str(cls.key), '-out', str(cls.cert), '-days', '1',
                        '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost'],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def test_real_tls_hop_authenticates_and_has_no_fallback_or_ban_bypass(self):
        upstream = Binance(direct=True)
        app = Egress(TOKEN, upstream)
        class Quiet(WSGIRequestHandler):
            def log_message(self, *args): pass
        server = make_server('127.0.0.1', 0, app, handler_class=Quiet)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.cert, self.key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        values = {'TA_PAPER_EGRESS_URL': f'https://localhost:{server.server_port}',
                  'TA_PAPER_EGRESS_TOKEN': TOKEN,
                  'TA_PAPER_EGRESS_CA_PEM': self.cert.read_text()}
        try:
            with patch.dict(os.environ, values), patch.object(upstream, 'get',
                    return_value={'serverTime': 123}) as get:
                client = Binance()
                self.assertEqual(client.get('/fapi/v1/time'), {'serverTime': 123})
                self.assertEqual(client.usage()['transport'], 'PRIVATE_EGRESS')
                self.assertEqual(get.call_count, 1)
                client.gate.block(int(time.time()*1000)+60_000, 'BINANCE_HTTP_418', 'EXCHANGE_RETRY_TIME')
                with self.assertRaisesRegex(DataUnavailable, 'COOLDOWN'):
                    client.get('/fapi/v1/time')
                self.assertEqual(get.call_count, 1)
            # Wrong token cannot consume the exchange request budget.
            with patch.dict(os.environ, dict(values, TA_PAPER_EGRESS_TOKEN='b'*64)), patch.object(upstream, 'get') as get:
                with self.assertRaisesRegex(DataUnavailable, '401'):
                    Binance().get('/fapi/v1/time')
                get.assert_not_called()
            # Certificate name mismatch is refused, without direct Binance fallback.
            with patch.dict(os.environ, dict(values, TA_PAPER_EGRESS_URL=f'https://127.0.0.1:{server.server_port}')), patch.object(upstream, 'get') as get:
                with self.assertRaises(DataUnavailable):
                    Binance().get('/fapi/v1/time')
                get.assert_not_called()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
