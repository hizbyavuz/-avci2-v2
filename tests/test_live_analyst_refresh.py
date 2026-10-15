import tempfile
import unittest
import sqlite3
from pathlib import Path
from unittest.mock import patch
import long_short_live_pool as lp


class LiveRefreshTests(unittest.TestCase):
    def test_new_scan_consumed_without_worker_restart(self):
        with tempfile.TemporaryDirectory() as root:
            analyst=str(Path(root)/'analyst.db')
            with sqlite3.connect(analyst) as c:
                c.execute('CREATE TABLE analyses(scan_time_utc TEXT,symbol TEXT)')
                c.execute('INSERT INTO analyses VALUES(?,?)',('2026-10-13T16:30:00Z','BTWUSDT'))
            with patch.object(lp,'ANALYST_DB',analyst), \
                    patch.object(lp,'LIVE_DB',str(Path(root)/'live.db')), \
                    patch.object(lp,'load_watchlist',return_value=[{'symbol':'BTWUSDT'}]), \
                    patch.object(lp,'sync_watchlist') as sync, \
                    patch.object(lp,'sync_dual_v32',return_value={'scanned':1}):
                self.assertEqual(lp.refresh_live_watchlist('2026-10-13T16:25:00Z'),
                                 '2026-10-13T16:30:00Z')
                sync.assert_called_once_with([{'symbol':'BTWUSDT'}],reviewed_symbols={'BTWUSDT'})

    def test_unchanged_scan_is_not_resynchronized(self):
        with patch.object(lp,'latest_analyst_scan',return_value='same'), \
                patch.object(lp,'load_watchlist') as load:
            self.assertEqual(lp.refresh_live_watchlist('same'),'same')
            load.assert_not_called()

    def test_failed_sync_retries_without_advancing_marker(self):
        with patch.object(lp,'latest_analyst_scan',return_value='new'), \
                patch.object(lp,'load_watchlist',return_value=[]), \
                patch.object(lp,'reviewed_symbols',return_value=set()), \
                patch.object(lp,'sync_watchlist',side_effect=sqlite3.OperationalError('locked')):
            self.assertEqual(lp.refresh_live_watchlist('old'),'old')

    def test_missing_analyst_does_not_clear_live_state(self):
        with patch.object(lp,'latest_analyst_scan',return_value=None), \
                patch.object(lp,'sync_watchlist') as sync:
            self.assertEqual(lp.refresh_live_watchlist('old'),'old')
            sync.assert_not_called()
