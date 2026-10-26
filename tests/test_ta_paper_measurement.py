import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from ta_paper.core import CFG, MINUTE, sizing
from ta_paper.io import Store,DataUnavailable
from ta_paper.observations import collect,record_setup,funnel_report,GAP_GRACE
from ta_paper.runner import continuous_path,hold_data,previous_version_safe,advance
from test_ta_paper import setup,filled,candle,MARKET,NOW


class MeasurementTests(unittest.TestCase):
    def test_strategy_thresholds_match_frozen_parent(self):
        from dataclasses import asdict
        original=json.loads((Path(__file__).parent/'fixtures/ta_paper_v1_config.json').read_text())
        current=asdict(CFG)
        self.assertNotEqual(original.pop('version'),current.pop('version'))
        self.assertEqual(original,current)

    def test_version_migration_preserves_known_cooldown_without_network(self):
        from types import SimpleNamespace
        from ta_paper.rate_limit import RequestGate
        from ta_paper.runner import run,STOP
        import time
        from contextlib import redirect_stdout
        from io import StringIO
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);old=root/'ta_paper_v1_20261026';old.mkdir()
            until=int(time.time()*1000)+600_000
            gate=RequestGate(old/'binance_cooldown.json');gate.block(until,'BINANCE_HTTP_418','EXCHANGE_RETRY_TIME')
            STOP.clear()
            with patch('urllib.request.build_opener') as network,redirect_stdout(StringIO()):
                code=run(SimpleNamespace(state_root=d,report=False,telegram=False,once=True,recheck_unknown_cooldown=False))
                self.assertEqual(code,2);network.assert_not_called()
            migrated=json.loads((root/CFG.version.lower()/'binance_cooldown.json').read_text())
            self.assertEqual(migrated['until_ms'],until)
            self.assertEqual(migrated['source'],'EXCHANGE_RETRY_TIME')


    def test_quote_spread_counted_once_both_directions(self):
        for direction,sign in [('LONG',1),('SHORT',-1)]:
            p=setup(direction);p['entry_half_spread']=.0005
            quote=100*(1+sign*.0005)
            q=sizing(p,MARKET,1000,5,quote,reference_is_quote=True)
            self.assertAlmostEqual(q['entry'],quote*(1+sign*CFG.entry_slippage))
            b=sizing(p,MARKET,1000,5,100)
            self.assertAlmostEqual(b['entry'],100*(1+sign*(.0005+CFG.entry_slippage)))
            self.assertGreater(sign*(sizing(p,MARKET,1000,5,quote)['entry']-q['entry']),0)

    def test_gap_repair_and_prefix_never_jump_missing_minute(self):
        start=NOW//MINUTE*MINUTE
        class Client:
            def __init__(self):self.calls=[]
            def candles(self,*args,**kwargs):
                self.calls.append(kwargs)
                return [candle(start+MINUTE),candle(start+3*MINUTE)] if len(self.calls)==1 else [candle(start)]
        client=Client()
        rows=continuous_path(client,'TESTUSDT',start,start+5*MINUTE)
        self.assertEqual([r[0] for r in rows],[start,start+MINUTE])
        self.assertEqual(len(client.calls),2)
        self.assertEqual(client.calls[1]['endTime'],start+MINUTE-1)

    def test_hold_not_fake_loss_and_exact_recovery(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'x.db');p=filled();p['checked_through_ms']=p['entry_ms']-1
            hold_data(store,p,'MISSING',NOW)
            hold_data(store,p,'MISSING',NOW+GAP_GRACE)
            self.assertEqual(p['status'],'UNRESOLVED_DATA')
            self.assertNotIn('net_r',p)
            self.assertEqual(len(store.plans(active=True)),1)
            self.assertEqual(funnel_report(store)['unresolved'],1)
            now=p['next_recovery_ms']+MINUTE
            class Client:
                def now(self):return now
                def candles(self,*args,**kwargs):
                    return [candle(t) for t in range(kwargs['startTime'],now//MINUTE*MINUTE-MINUTE,MINUTE)]
            # Fully restored path resumes at the original missing time.
            with patch('ta_paper.runner.continuous_path',return_value=[candle(t) for t in range(p['entry_ms'],now//MINUTE*MINUTE,MINUTE)]):
                advance(store,Client())
            q=store.plans()[0]
            self.assertEqual(q['status'],'OPEN')
            self.assertNotIn('data_hold_since_ms',q)
            self.assertNotIn('net_r',q)

    def test_rejected_setup_path_survives_restart_and_bad_rows_are_bounded(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'x.db';store=Store(path);p=setup();record_setup(store,p,NOW,MARKET,'SYMBOL_COOLDOWN')
            start=p['entry_ms'];end=start+2*MINUTE
            store.db.execute('UPDATE observations SET end_ms=?',(end,));store.db.commit();store.db.close()
            store=Store(path)
            class Client:
                stamp=end+MINUTE
                calls=0
                def now(self):return self.stamp
                def candles(self,*args,**kwargs):
                    self.calls+=1
                    bad=candle(start+MINUTE);bad[2]='NaN'
                    return [candle(start),bad]
            client=Client()
            for _ in range(5):
                collect(store,client);client.stamp+=6*MINUTE
            row=store.db.execute('SELECT * FROM observations').fetchone()
            self.assertEqual(row['status'],'PARTIAL')
            self.assertEqual(row['repairs'],3)
            self.assertEqual(client.calls,4)
            self.assertEqual(len(store.plans()),0)
            self.assertEqual(funnel_report(store)['admission_reasons'],{'SYMBOL_COOLDOWN':1})
            self.assertEqual(json.loads(row['payload'])['source_sha256'],store.provenance['source_sha256'])

    def test_cached_observation_needs_no_network_and_funnel_keeps_cancellation_separate(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'x.db');p=setup();record_setup(store,p,NOW,MARKET,'ACCEPTED')
            start=p['entry_ms'];store.db.execute('UPDATE observations SET end_ms=?',(start+MINUTE,));store.db.commit()
            store.candles(p['symbol'],'1m',[candle(start)])
            p.update(status='CANCELLED',cancel_reason='LATE_ENTRY');store.save(p)
            class Client:
                def now(self):return start+2*MINUTE
                def candles(self,*args,**kwargs):raise AssertionError('cache should avoid network')
            self.assertEqual(collect(store,Client()),0)
            report=funnel_report(store,include_coverage=False)
            self.assertEqual(report['observation_statuses'],{'COMPLETE':1})
            self.assertEqual(report['cancelled'],1);self.assertEqual(report['entered'],0)
            self.assertEqual(report['closed'],0);self.assertNotIn('coverage',report)

    def test_version_migration_refuses_abandoning_old_pending_risk(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);folder=root/'ta_paper_v1_20261026';folder.mkdir()
            with sqlite3.connect(folder/'research.sqlite3') as db:
                db.execute('CREATE TABLE plans(status TEXT)');db.execute("INSERT INTO plans VALUES('OPEN')")
            with self.assertRaisesRegex(RuntimeError,'UNRESOLVED_PLANS'):previous_version_safe(root)
            with sqlite3.connect(folder/'research.sqlite3') as db:db.execute("UPDATE plans SET status='CLOSED'")
            previous_version_safe(root)

if __name__=='__main__':unittest.main()
