import tempfile
import json
import sqlite3
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

import long_short_analyst as analyst
import long_short_futures_history_worker as history
import long_short_futures_kline_shadow as shadow


class PrewarmTests(unittest.TestCase):
    def test_real_catalogue_and_tickers_match_two_analyst_windows(self):
        now=3000
        symbols=[f'C{i:03d}USDT' for i in range(95)]
        with tempfile.TemporaryDirectory() as root, patch.dict('os.environ',{
                'LS_STATE_DIR':root,'LS_NATIVE_CHAIN_V2':'1','LS_DATA_OPPORTUNITY_V2':'0'}), \
                patch.object(analyst,'MAX_SYMBOLS',30), patch.object(analyst.time,'time',return_value=now):
            from long_short_native_derivatives import database
            c=database()
            with c:c.execute('INSERT INTO markets VALUES(1,?,?)',(now*1000,json.dumps(symbols)))
            c.close()
            with sqlite3.connect(str(Path(root)/'binance_futures_universe.db')) as c:
                c.execute('CREATE TABLE futures_tickers(symbol,quote_volume,last_price,change_pct,received_ms,source)')
                c.executemany('INSERT INTO futures_tickers VALUES(?,?,?,?,?,?)',
                    [(s,1e9,1,0,now*1000,shadow.SOURCE) for s in symbols])
            current=analyst.native_futures_websocket_universe(slot=10,quiet=True)
            future=analyst.native_futures_websocket_universe(slot=11,quiet=True)
            combined=analyst.native_futures_websocket_universe(slot=10,slot_offsets=(0,1),quiet=True)
            self.assertEqual(combined,current+future)
            requested=shadow.requested_symbols(str(Path(root)/'missing.db'))
            self.assertEqual(requested[:30],[r[0] for r in current])
            self.assertTrue(set(r[0] for r in future).issubset(requested))

    def test_oversized_forecast_preserves_live_shortlist_and_reports_deferrals(self):
        forecast=[(f'C{i:03d}USDT',1e9,1,0) for i in range(60)]
        live=[f'L{i:03d}USDT' for i in range(30)]
        with tempfile.TemporaryDirectory() as root, \
                patch.dict('os.environ',{'LS_NATIVE_CHAIN_V2':'1','LS_DATA_OPPORTUNITY_V2':'0'}), \
                patch.object(analyst,'MAX_SYMBOLS',30), \
                patch.object(analyst,'native_futures_websocket_universe',return_value=forecast), \
                patch('long_short_v4_native_pipeline.choose_symbols',return_value=live), \
                patch.object(shadow,'log') as log:
            requested=shadow.requested_symbols(str(Path(root)/'missing.db'))
        self.assertEqual(len(requested),90)
        self.assertTrue(set(live).issubset(requested))
        tag,payload=log.call_args.args
        self.assertEqual(tag,'FUTURES_REQUEST_CAPACITY')
        self.assertEqual(len(payload['deferred_forecast']),3)
    def test_collectors_prepare_current_and_next_before_scan_exists(self):
        calls = []
        def window(slot=None, slot_offsets=(0,), quiet=False):
            calls.append((slot,slot_offsets,quiet))
            return [(f'C{i:03d}USDT', 10000000, 1, 0)
                    for offset in slot_offsets
                    for i in range(((slot+offset) % 4)*30, ((slot+offset) % 4)*30+30)]
        with tempfile.TemporaryDirectory() as root, \
                patch.dict('os.environ', {'LS_NATIVE_CHAIN_V2':'1', 'LS_DATA_OPPORTUNITY_V2':'0'}), \
                patch.object(shadow.time, 'time', return_value=3000), \
                patch.object(analyst, 'MAX_SYMBOLS', 30), \
                patch.object(analyst, 'native_futures_websocket_universe', side_effect=window), \
                patch('long_short_v4_native_pipeline.choose_symbols', return_value=['OLDUSDT']):
            requested = shadow.requested_symbols(str(Path(root)/'absent.db'))
        self.assertEqual(calls, [(10,(0,1),True)])
        self.assertEqual(requested[:30], [f'C{i:03d}USDT' for i in range(60, 90)])
        self.assertTrue(set(f'C{i:03d}USDT' for i in range(60,120)).issubset(requested))
        self.assertIn('BTCUSDT', requested)
        self.assertIn('OLDUSDT', requested)
        self.assertLessEqual(len(requested), 90)
        self.assertEqual(len(requested), len(set(requested)))

    def test_unavailable_catalogue_keeps_existing_requests(self):
        with tempfile.TemporaryDirectory() as root, \
                patch.dict('os.environ', {'LS_NATIVE_CHAIN_V2':'1', 'LS_DATA_OPPORTUNITY_V2':'0'}), \
                patch.object(analyst, 'native_futures_websocket_universe', side_effect=RuntimeError('not ready')), \
                patch.object(shadow, 'log'), \
                patch('long_short_v4_native_pipeline.choose_symbols', return_value=['OLDUSDT']):
            requested = shadow.requested_symbols(str(Path(root)/'absent.db'))
        self.assertIn('OLDUSDT', requested)
        self.assertEqual(set(requested), {'BTCUSDT','ETHUSDT','SOLUSDT','OLDUSDT'})

    def run_priority(self, code=None, advance=False):
        seen = []
        clock = [0]
        def repair(db, base, sym, iv):
            seen.append((sym, iv))
            if code and sym == 'BADUSDT':
                raise HTTPError(base, code, 'failed', None, None)
        def requested(db):
            return ['NEWUSDT'] if advance and clock[0] >= 40 else ['BADUSDT','GOODUSDT']
        def monotonic():
            clock[0] += 21 if advance else 0
            return clock[0]
        with patch.object(history, 'requested_symbols', side_effect=requested), \
                patch.object(history, 'needs_repair', return_value=True), \
                patch.object(history, 'repair', side_effect=repair), \
                patch.object(history, 'log'), \
                patch.object(history.time, 'monotonic', side_effect=monotonic):
            stop = threading.Event()
            with patch.object(stop, 'wait'):
                failed = history.priority_pass('db', 'http://relay', 'analyst', stop)
        return failed, seen

    def test_context_repairs_are_preempted_for_new_admission_window(self):
        failed, seen = self.run_priority(advance=True)
        self.assertFalse(failed)
        self.assertIn(('NEWUSDT','5m'), seen)
        self.assertLess(seen.index(('NEWUSDT','5m')), seen.index(('NEWUSDT','30m')))
        # A new forecast must receive its long history during THIS pass.
        self.assertIn(('NEWUSDT','1h'), seen)
        self.assertIn(('NEWUSDT','4h'), seen)

    def test_symbol_rejection_does_not_block_healthy_symbol(self):
        failed, seen = self.run_priority(code=400)
        self.assertTrue(failed)
        self.assertEqual([x for x in seen if x[0]=='BADUSDT'], [('BADUSDT','5m')])
        self.assertIn(('GOODUSDT','4h'), seen)

    def test_rate_limit_stops_pass(self):
        failed, seen = self.run_priority(code=429)
        self.assertTrue(failed)
        self.assertEqual(seen, [('BADUSDT','5m')])

    def test_ready_websocket_candles_do_not_consume_rest_requests(self):
        with patch.object(history, 'requested_symbols', return_value=['BTCUSDT']), \
                patch.object(history, 'needs_repair', return_value=False), \
                patch.object(history, 'repair') as repair, patch.object(history, 'log'):
            self.assertFalse(history.priority_pass('db', 'relay', 'analyst', threading.Event()))
            repair.assert_not_called()

    def test_slow_feed_refreshed_before_existing_stale_gate(self):
        for status, expected in [({'ready':True,'last_close_age_s':299}, False),
                                 ({'ready':True,'last_close_age_s':331}, True),
                                 ({'ready':False,'reason':'gap'}, True)]:
            with patch('long_short_data_health.interval_status', return_value=status), \
                    patch.object(shadow, 'connect'):
                self.assertEqual(history.needs_repair('db','BTCUSDT','5m',1000000), expected)

    def test_rest_repaired_tail_is_not_reported_as_new_websocket_gap(self):
        def candle(start):
            return ('BTCUSDT','1m',start,start+59999,100,101,99,100,1,
                    shadow.REST_SOURCE,start+60000,100,1,50)
        with tempfile.TemporaryDirectory() as root:
            c=shadow.initialize(str(Path(root)/'candles.db'))
            tracker=shadow.GapTracker()
            self.assertIsNone(tracker.observe(c,candle(0)))
            shadow.upsert_rows(c,[candle(0),candle(60000),candle(120000)]);c.commit()
            self.assertIsNone(tracker.observe(c,candle(180000)))
            self.assertEqual(tracker.observe(c,candle(300000))['missing_bars'],1)
            c.close()


class OutcomeAuditTests(unittest.TestCase):
    def test_stop_candle_does_not_count_post_exit_low_as_mae(self):
        from long_short_outcomes_v2 import label_path
        result=label_path('LONG',100,99,103,[[60000,100,102,90,92]],0.3)
        self.assertAlmostEqual(result['mae_pct'],-1)
        self.assertEqual(result['mfe_pct'],0)
        self.assertFalse(result['intrabar_excursion_exact'])

    def test_gap_exit_does_not_count_later_candle_extreme(self):
        from long_short_outcomes_v2 import label_path
        for side,stop,target,bar in [('LONG',99,103,[60000,97,100,90,92]),
                                     ('SHORT',101,97,[60000,103,110,100,108])]:
            result=label_path(side,100,stop,target,[bar],0.3)
            self.assertAlmostEqual(result['mae_pct'],-3)

    def test_bad_direction_and_nonfinite_costs_rejected(self):
        from long_short_outcomes_v2 import label_path
        for side,cost in [('TYPO',0.3),('SHORT',float('nan')),('SHORT',-0.3)]:
            with self.assertRaises(ValueError):
                label_path(side,100,101,97,[[60000,100,100,100,100]],cost)

    def test_health_report_matches_reader_closed_candle_guard(self):
        from long_short_data_health import interval_status
        with tempfile.TemporaryDirectory() as root:
            c=shadow.initialize(str(Path(root)/'candles.db'))
            row=('BTCUSDT','1m',0,59999,100,101,99,100,1,shadow.SOURCE,60000,100,1,50)
            shadow.upsert_rows(c,[row]);c.commit()
            self.assertFalse(interval_status(c,'BTCUSDT','1m',60001,required=1)['ready'])
            self.assertTrue(interval_status(c,'BTCUSDT','1m',60249,required=1)['ready'])
            c.close()
