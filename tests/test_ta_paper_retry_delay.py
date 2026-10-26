"""Offline checks of a user retry floor, independent of exchange cooldown evidence."""
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ta_paper.core import CFG
from ta_paper.io import Binance, DataUnavailable, Store, Telegram
from ta_paper.rate_limit import CooldownError, RequestGate
from ta_paper.runner import STOP, health_message, run
from ta_paper.streams import LiveFeed
from test_ta_paper_operations import market_response

EXCHANGE = 1793121240264
DEFERRED = EXCHANGE + 600_000


class RetryDelayTests(unittest.TestCase):
    def test_durable_floor_preserves_exchange_evidence_without_restart_extension(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'cooldown.json'
            gate = RequestGate(path)
            gate.block(EXCHANGE, 'BINANCE_HTTP_418', 'EXCHANGE_RETRY_TIME')
            self.assertTrue(gate.defer_until(DEFERRED))
            raw = json.loads(path.read_text())
            self.assertEqual(raw['until_ms'], EXCHANGE)
            self.assertEqual(raw['source'], 'EXCHANGE_RETRY_TIME')
            self.assertEqual(raw['reason'], 'BINANCE_HTTP_418')
            restarted = RequestGate(path)
            self.assertFalse(restarted.defer_until(DEFERRED))
            self.assertFalse(restarted.defer_until(DEFERRED-300_000))
            self.assertEqual(restarted.state()['until_ms'], DEFERRED)
            self.assertEqual(restarted.state()['exchange_until_ms'], EXCHANGE)
            self.assertEqual(json.loads(path.read_text()), raw)

    def test_no_rest_request_after_exchange_expiry_until_user_boundary(self):
        client = Binance()
        client.gate.block(EXCHANGE, 'BINANCE_HTTP_418', 'EXCHANGE_RETRY_TIME')
        client.gate.defer_until(DEFERRED)
        with patch('urllib.request.build_opener') as net, patch('time.time') as clock:
            for stamp in (EXCHANGE, EXCHANGE+300_000, DEFERRED-1):
                clock.return_value = stamp/1000
                with self.assertRaisesRegex(DataUnavailable, 'USER_REQUESTED_RETRY_DELAY'):
                    client.get('/fapi/v1/time')
                self.assertGreater(client.gate.state()['remaining_seconds'], 0)
            net.assert_not_called()
            clock.return_value = DEFERRED/1000
            net.return_value.open.return_value = market_response({'serverTime':DEFERRED})
            self.assertEqual(client.get('/fapi/v1/time'), {'serverTime':DEFERRED})
            self.assertEqual(net.return_value.open.call_count, 1)

    def test_later_exchange_block_wins_and_preserves_user_floor(self):
        gate = RequestGate()
        with patch('time.time', return_value=(EXCHANGE-1000)/1000):
            gate.block(EXCHANGE, 'BINANCE_HTTP_418', 'EXCHANGE_RETRY_TIME')
            gate.defer_until(DEFERRED)
            later = DEFERRED+3600_000
            gate.block(later, 'BINANCE_HTTP_418', 'EXCHANGE_RETRY_TIME')
        with patch('time.time', return_value=DEFERRED/1000):
            self.assertEqual(gate.state()['until_ms'], later)
            self.assertEqual(gate.state()['user_not_before_ms'], DEFERRED)
            with self.assertRaisesRegex(CooldownError, 'EXCHANGE_COOLDOWN'):
                gate.acquire(1)

    def test_assumed_timer_recheck_cannot_bypass_user_delay(self):
        with patch('time.time', return_value=(EXCHANGE-1000)/1000):
            gate = RequestGate()
            gate.block(EXCHANGE, 'BINANCE_HTTP_418', 'CONSERVATIVE_FALLBACK')
            self.assertTrue(gate.reserve_unknown_recheck('before-delay'))
            gate.defer_until(DEFERRED)
            self.assertFalse(gate.reserve_unknown_recheck('after-delay'))
            with self.assertRaises(CooldownError):
                gate.acquire(1, unknown_recheck=True)
            self.assertEqual(gate.state()['recheck_ids'], ['before-delay'])

    def test_corrupt_durable_floor_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'cooldown.json'
            for bad in (-1, 'tomorrow', None, 1.5, True):
                path.write_text(json.dumps(dict(until_ms=EXCHANGE, reason='BINANCE_HTTP_418',
                    source='EXCHANGE_RETRY_TIME', user_not_before_ms=bad)))
                with self.assertRaisesRegex(CooldownError, 'RATE_LIMIT_STATE_INVALID'):
                    RequestGate(path)

    def test_new_stream_handshake_waits_after_original_exchange_expiry(self):
        with tempfile.TemporaryDirectory() as d:
            store = Store(Path(d)/'research.sqlite3')
            gate = RequestGate()
            gate.block(EXCHANGE, 'BINANCE_HTTP_418', 'EXCHANGE_RETRY_TIME')
            gate.defer_until(DEFERRED)
            feed = LiveFeed(Path(d)/'streams', gate, store)
            try:
                with patch('time.time', return_value=(EXCHANGE+1000)/1000), \
                        patch('websockets.sync.client.connect') as connect, \
                        patch.object(feed.stop, 'wait', side_effect=lambda *_: feed.stop.set()) as wait:
                    feed._connection('market-0', ['BTCUSDT'])
                    connect.assert_not_called()
                    wait.assert_called_once_with(15)
            finally:
                feed.close()
                store.db.close()

    def test_runner_applies_floor_before_stream_start_and_keeps_external_delivery(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)/CFG.version.lower()
            store = Store(folder/'research.sqlite3')
            store.setting('system:charts:CHARTS_20261026_1','queued')
            store.db.close()
            gate = RequestGate(folder/'binance_cooldown.json')
            gate.block(EXCHANGE, 'BINANCE_HTTP_418', 'EXCHANGE_RETRY_TIME')
            external_store = Store(folder/'external_bybit_v1'/'research.sqlite3')
            external = SimpleNamespace(delivery=external_store, now=lambda:EXCHANGE+1000, close=lambda:None)
            delivered = []
            gates_at_stream_start = []
            def flush(sender, target, now, **kwargs):
                delivered.append((target, kwargs.get('signals_enabled')))
                return 0
            def stream_start(feed):
                gates_at_stream_start.append(feed.gate.state()['until_ms'])
            args = SimpleNamespace(state_root=d, telegram=False, report=False, once=True,
                market_streams=True, external_watch='bybit', binance_not_before_ms=DEFERRED)
            STOP.clear()
            try:
                with patch('time.time', return_value=(EXCHANGE+1000)/1000), \
                        patch('urllib.request.build_opener') as network, \
                        patch.object(LiveFeed, 'start', stream_start), \
                        patch('ta_paper.external_watch.ExternalWatch') as worker, \
                        patch.object(Telegram, 'flush', flush), redirect_stdout(StringIO()) as output:
                    worker.return_value.start.return_value = external
                    self.assertEqual(run(args), 2)
                    # The floor remains even if the flag is omitted on restart.
                    args.binance_not_before_ms = 0
                    self.assertEqual(run(args), 2)
                    network.assert_not_called()
                self.assertEqual(gates_at_stream_start, [DEFERRED, DEFERRED])
                self.assertEqual(delivered.count((external_store, True)), 2)
                self.assertEqual(output.getvalue().count('TA_PAPER_RETRY_DELAY '), 1)
                bybit_gate = RequestGate(folder/'external_bybit_v1'/'bybit_cooldown.json')
                self.assertEqual(bybit_gate.state()['remaining_seconds'], 0)
                bybit_gate.acquire(3)
                state = RequestGate(folder/'binance_cooldown.json').state()
                self.assertIn('27.10.2026 20:24:00', health_message('Bekleniyor',state))
                self.assertIn('ek bekleme', health_message('Bekleniyor',state))
            finally:
                external_store.db.close()
                STOP.clear()


if __name__ == '__main__':
    unittest.main()
