import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from long_short_trade_ledger import evaluate, summarize, resolve, pending_symbols
from long_short_outcomes_v2 import label_path
from long_short_native_derivatives import SOURCE


def bar(ms,o=100,h=100.5,l=99.5,c=100):
    return [ms,o,h,l,c,1000,ms+59999]


class LedgerTests(unittest.TestCase):
    def test_long_short_mirror_and_costs(self):
        a=evaluate('LONG',98,104,[bar(60000,100,105,99,104)],60000,120000,.3)
        b=evaluate('SHORT',102,96,[bar(60000,100,101,95,96)],60000,120000,.3)
        for r in (a,b):
            self.assertEqual(r['state'],'PRICE_RESOLVED')
            self.assertAlmostEqual(r['net_return_pct'],3.7)
            self.assertAlmostEqual(r['net_r'],3.7/2.3)

    def test_unknown_intraminute_order_stop_first(self):
        r=evaluate('LONG',98,104,[bar(60000,100,105,97,100)],60000,120000,.3)
        self.assertEqual(r['result'],'STOP')
        self.assertAlmostEqual(r['net_r'],-1)

    def test_gap_stop_uses_open_not_theoretical_stop(self):
        r=evaluate('LONG',98,104,[bar(60000),bar(120000,95,96,94,95)],60000,180000,.3)
        self.assertEqual(r['exit_price'],95)
        self.assertLess(r['net_r'],-2)

    def test_gap_target_precedes_later_stop_in_same_bar(self):
        r=label_path('LONG',100,98,104,[bar(60000,105,106,97,100)],.3)
        self.assertEqual(r['result'],'TP1')
        self.assertEqual(r['exit_ms'],60000)

    def test_missing_after_exit_does_not_erase_exit(self):
        r=evaluate('LONG',98,104,[bar(60000,100,105,99,104)],60000,600000,.3)
        self.assertEqual(r['state'],'PRICE_RESOLVED')

    def test_missing_before_exit_unresolved(self):
        r=evaluate('LONG',98,104,[bar(60000),bar(180000)],60000,240000,.3)
        self.assertEqual(r['state'],'DATA_UNRESOLVED')

    def test_does_not_use_signal_minute_or_unclosed_bar(self):
        r=evaluate('LONG',98,104,[bar(0,100,120,80,100),bar(60000)],60000,119999,.3)
        self.assertEqual(r['state'],'PENDING')

    def test_no_profit_from_entry_after_target_or_bad_r(self):
        a=evaluate('LONG',98,104,[bar(60000,105,106,104,105)],60000,120000,.3)
        b=evaluate('LONG',98,104,[bar(60000,103,103.5,102.5,103)],60000,120000,.3)
        self.assertEqual(a['state'],'ENTRY_MISSED')
        self.assertEqual(b['state'],'ENTRY_MISSED')

    def test_timeout_exactly_180_full_minutes(self):
        rows=[bar(60000+i*60000) for i in range(180)]
        self.assertEqual(evaluate('LONG',98,104,rows,60000,180*60000,.3)['state'],'PENDING')
        r=evaluate('LONG',98,104,rows,60000,181*60000,.3)
        self.assertEqual(r['result'],'TIMEOUT')
        self.assertAlmostEqual(r['net_return_pct'],-.3)

    def test_delayed_entry_must_preserve_live_room_gate(self):
        r=evaluate('LONG',99.9,101,[bar(60000,100.2,100.5,100,100.2)],
                   60000,120000,.1,1.0,.9)
        self.assertEqual(r['state'],'ENTRY_MISSED')
        self.assertEqual(r['reason'],'ROOM_LOST_BEFORE_ENTRY')

    def test_nonfinite_or_gapped_data_is_not_profit(self):
        self.assertEqual(evaluate('LONG',98,104,[bar(60000,h=float('nan'))],60000,120000,.3)['state'],'DATA_UNRESOLVED')
        with self.assertRaises(ValueError):label_path('LONG',100,98,104,[bar(0),bar(120000)],.3)

    def test_report_keeps_unresolved_separate(self):
        r=summarize([dict(state='RESOLVED',net_r=2,net_return_pct=4),
                     dict(state='RESOLVED',net_r=-1,net_return_pct=-2),dict(state='DATA_UNRESOLVED')])
        self.assertEqual(r['closed'],2)
        self.assertEqual(r['expectancy_net_r'],.5)
        self.assertEqual(r['profit_factor'],2)
        self.assertEqual(r['pessimistic_one_r_sensitivity'],0)

    def test_failed_telegram_still_resolves_and_legacy_cannot_starve(self):
        with tempfile.TemporaryDirectory() as root:
            db=str(Path(root)/'live.db')
            with sqlite3.connect(db) as c:
                c.execute('CREATE TABLE events(id INTEGER PRIMARY KEY,event_time_utc,symbol,direction,stage_to,telegram_status,payload_json)')
                for i in range(60):
                    c.execute("INSERT INTO events VALUES(?,?,'XUSDT','LONG','TRIGGERED','SENT','{}')",(i,'1970-01-01T00:00:10+00:00'))
                p=dict(_native_chain_v2=True,invalidation=98,target1=104,
                       _trigger_execution_gate=dict(qualified=True,round_trip_cost_pct=.3))
                c.execute("INSERT INTO events VALUES(100,?,'XUSDT','LONG','TRIGGERED','FAILED',?)",('1970-01-01T00:00:10+00:00',json.dumps(p)))
            bars=lambda *a:[bar(60000,100,105,99,104)]
            funding=lambda *a:dict(source=SOURCE,symbol='XUSDT',rates=[])
            self.assertEqual(pending_symbols(db,120000),['XUSDT'])
            self.assertEqual(pending_symbols(db,20000000),[])
            resolve(db,120000,bars,funding)
            self.assertEqual(pending_symbols(db,120000),[])
            resolve(db,180000,bars,lambda *a:self.fail('resolved outcome changed'))
            with sqlite3.connect(db) as c:
                rows=c.execute('SELECT state,payload FROM independent_paper_v1').fetchall()
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0][0],'RESOLVED')
            self.assertEqual(json.loads(rows[0][1])['telegram_status'],'FAILED')
