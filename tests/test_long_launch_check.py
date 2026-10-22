import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from long_short_launch_check import inspect, read_db


class LaunchCheckTests(unittest.TestCase):
    def test_missing_state_is_not_created_and_never_looks_started(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'absent'
            with patch('socket.socket', side_effect=AssertionError('no network')):
                out = inspect(path, {}, 1_000_000)
            self.assertFalse(path.exists())
            self.assertFalse(out['signal_engine_started'])
            self.assertFalse(out['latest_candle_freshness']['1h']['recent'])

    def test_existing_state_is_read_only_and_credentials_are_redacted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root/'long_short_telegram_chat_cache.db'
            with sqlite3.connect(cache) as c:
                c.execute('CREATE TABLE runtime_settings(key,value)')
                c.execute('INSERT INTO runtime_settings VALUES(?,?)', ('telegram_chat_id', '987654321'))
            bars = root/'binance_futures_kline_shadow.db'
            with sqlite3.connect(bars) as c:
                c.execute('CREATE TABLE futures_closed_klines(symbol,interval,open_ms,close_ms)')
                c.execute('INSERT INTO futures_closed_klines VALUES(?,?,?,?)', ('TESTUSDT','1h',0,3_599_999))
            before = {p.name: p.read_bytes() for p in root.iterdir()}
            out = inspect(root, {'TELEGRAM_BOT_TOKEN':'secret-token','TELEGRAM_CHAT_ID':'987654321',
                                 'LS_FUTURES_HISTORY_RELAY_URL':'http://private-secret'}, 7_200_000)
            self.assertEqual(before, {p.name: p.read_bytes() for p in root.iterdir()})
            self.assertTrue(out['telegram']['cached_destination'])
            self.assertTrue(out['telegram']['destination_match'])
            self.assertFalse(out['telegram']['recipient_verified'])
            self.assertFalse(out['latest_candle_freshness']['1h']['recent'])
            self.assertEqual(out['databases'][bars.name]['tables']['futures_closed_klines']['count'],1)
            for secret in ('secret-token','987654321','private-secret'):
                self.assertNotIn(secret,json.dumps(out))
            c = read_db(cache)
            try:
                with self.assertRaises(sqlite3.OperationalError):
                    c.execute('DELETE FROM runtime_settings')
            finally:
                c.close()

    def test_corrupt_database_is_explicit_and_does_not_leak_contents(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'major_move_v1.db'
            path.write_text('private-not-a-database')
            out = inspect(Path(tmp), {}, 0)
            self.assertEqual(out['databases'][path.name]['status'], 'READ_INCOMPLETE')
            self.assertNotIn('private-not-a-database',json.dumps(out))


if __name__ == '__main__':
    unittest.main()
