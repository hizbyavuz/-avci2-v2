import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from long_short_chart_first import VERSION, STRATEGY_ID
from long_short_chart_first_worker import connect, record, resolve
from long_short_chart_delivery_outcomes import (
    run_batch, due_jobs, coverage, RETRY_MS, export_completed, measure,
)


class DeliveryOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'LS_STATE_DIR': self.tmp.name})
        self.env.start()
        self.c = connect()
        self.k = sqlite3.connect(':memory:')
        self.k.execute('CREATE TABLE futures_closed_klines(symbol,interval,open_ms,open,high,low,close,volume,close_ms,source)')
        self.now = 190*60000
        self.bars()

    def tearDown(self):
        self.c.close()
        self.k.close()
        self.env.stop()
        self.tmp.cleanup()

    def bars(self, symbol='X', first_high=100.2, source='BINANCE_UM_FUTURES_WEBSOCKET'):
        self.k.executemany('INSERT INTO futures_closed_klines VALUES(?,?,?,?,?,?,?,?,?,?)', [
            (symbol,'1m',i*60000,100,first_high if i==1 else 100.2,99.8,100.1,1,i*60000+59999,source)
            for i in range(1,190)])
        self.k.commit()

    def delivery(self, stage='DELIVERED_WATCH', symbol='X', **kwargs):
        p = dict(version=VERSION, strategy_id=STRATEGY_ID, symbol=symbol,
                 episode_id='e'+str(self.c.execute('SELECT COUNT(*) FROM assessments').fetchone()[0]),
                 observed_ms=68000, price=100, direction='LONG', stage=stage,
                 reason='TELEGRAM_ACK', trigger=100, stop=99, obstacle=102,
                 cost_assumption_pct=.7, hourly_flow={'buy_share':.62},
                 context={'1h':{'direction':'UP'}})
        p.update(kwargs)
        record(self.c,p)
        self.c.commit()
        return self.c.execute('SELECT MAX(id) FROM assessments').fetchone()[0]

    def test_reproduces_old_queue_starvation_and_repairs_watch_without_changing_decisions(self):
        for i in range(200):
            self.delivery(stage='REJECTED',symbol='OLD'+str(i),reason='NO_SETUP')
        watch = self.delivery()
        ready = self.delivery(stage='DELIVERED_READY')
        resolve(self.c,self.k,self.now)
        self.assertIsNone(self.c.execute('SELECT 1 FROM outcomes WHERE assessment_id=?',(watch,)).fetchone())
        self.assertIsNotNone(self.c.execute('SELECT 1 FROM outcomes WHERE assessment_id=?',(ready,)).fetchone())
        before = [tuple(r) for r in self.c.execute('SELECT * FROM assessments')]
        emitted = []
        run_batch(self.c,self.k,self.now,emit=emitted.append)
        outcomes = list(self.c.execute('SELECT horizon,state FROM outcomes WHERE assessment_id=?',(watch,)))
        self.assertEqual({r[0] for r in outcomes},{15,30,60,180})
        self.assertTrue(all(r[1]=='RESOLVED' for r in outcomes))
        self.assertEqual(before,[tuple(r) for r in self.c.execute('SELECT * FROM assessments')])
        self.assertTrue(all(x['signal']['stage'].startswith('DELIVERED_') for x in emitted))

    def test_final_outcomes_preserved_and_restart_is_idempotent(self):
        aid = self.delivery()
        self.c.execute('INSERT INTO outcomes VALUES(?,?,?,?,?)',(aid,15,'RESOLVED',1,'{"sentinel":123}'))
        self.c.commit()
        run_batch(self.c,self.k,self.now)
        self.assertEqual(self.c.execute('SELECT payload FROM outcomes WHERE assessment_id=? AND horizon=15',(aid,)).fetchone()[0],'{"sentinel":123}')
        self.assertEqual(run_batch(self.c,self.k,self.now)['selected'],0)

    def test_concurrent_native_completion_is_not_overwritten(self):
        aid = self.delivery()
        def native_completes(p, kdb, horizon, now_ms):
            result = measure(p,kdb,horizon,now_ms)
            self.c.execute('INSERT INTO outcomes VALUES(?,?,?,?,?)',
                           (aid,horizon,'RESOLVED',now_ms,'{"native_completed":true}'))
            self.c.commit()
            return result
        with patch('long_short_chart_delivery_outcomes.measure',side_effect=native_completes):
            result = run_batch(self.c,self.k,self.now)
        self.assertEqual(result['concurrent_completion_or_retry'],4)
        self.assertTrue(all(json.loads(r[0]).get('native_completed')
                            for r in self.c.execute('SELECT payload FROM outcomes')))

    def test_missing_native_minutes_are_unknown_and_recover_after_retry(self):
        aid = self.delivery(symbol='MISSING')
        run_batch(self.c,self.k,self.now)
        self.assertEqual({r[0] for r in self.c.execute('SELECT state FROM outcomes')},{'UNRESOLVED'})
        self.assertEqual(due_jobs(self.c,self.now+1000),[])
        self.bars('MISSING')
        run_batch(self.c,self.k,self.now+RETRY_MS)
        self.assertEqual({r[0] for r in self.c.execute('SELECT state FROM outcomes')},{'RESOLVED'})

    def test_verified_sources_only_and_30m_must_be_due(self):
        self.delivery(symbol='SPOT')
        self.bars('SPOT',source='BINANCE_SPOT')
        self.assertEqual(run_batch(self.c,self.k,31*60000)['selected'],1)
        self.assertEqual([tuple(r) for r in self.c.execute('SELECT horizon,state FROM outcomes')],[(15,'UNRESOLVED')])
        self.assertEqual([j['horizon'] for j in due_jobs(self.c,33*60000)],[30])

    def test_first_partial_minute_ambiguity_preserved_and_watch_is_not_a_trade(self):
        self.k.execute('DELETE FROM futures_closed_klines')
        self.bars(first_high=103)
        self.delivery(stage='DELIVERED_READY')
        self.delivery(stage='DELIVERED_WATCH')
        run_batch(self.c,self.k,self.now)
        for row in self.c.execute('SELECT state,payload FROM outcomes'):
            result = json.loads(row[1])
            self.assertEqual(row[0],'UNRESOLVED_BOUNDARY')
            self.assertNotIn('model_net_return_pct',result)
            self.assertIn('directional_return_pct',result)

    def test_historical_cost_and_signal_features_are_preserved(self):
        self.delivery(stage='DELIVERED_READY')
        emitted=[]
        run_batch(self.c,self.k,self.now,emit=emitted.append)
        first = emitted[0]
        self.assertEqual(first['signal']['hourly_flow']['buy_share'],.62)
        self.assertEqual(first['signal']['context_directions'],{'1h':'UP'})
        self.assertEqual(first['signal']['strategy_id'],STRATEGY_ID)
        self.assertAlmostEqual(first['outcome']['model_net_return_pct'],-.6)
        self.assertEqual(first['outcome']['cost_assumption_pct'],.7)

    def test_legacy_labels_keep_their_original_endpoint_convention(self):
        self.delivery(version='CHART_FIRST_MULTIFRAME_V1_2026_10_15')
        emitted=[]
        run_batch(self.c,self.k,self.now,emit=emitted.append)
        self.assertTrue(emitted[0]['outcome']['path_starts_at_next_full_minute'])
        self.assertNotIn('model_net_return_pct',emitted[0]['outcome'])

    def test_coverage_and_batch_cap_do_not_hide_unknowns(self):
        self.delivery()
        self.delivery(observed_ms=self.now-60000)
        self.delivery(reason='NOT_ACKNOWLEDGED')
        stats=coverage(self.c,self.now)
        self.assertTrue(all(x['records']==2 and x['missing_due']==1 and x['pending']==1 and x['resolved']==0 for x in stats))
        self.assertEqual(run_batch(self.c,self.k,self.now,limit=2)['selected'],2)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM outcomes').fetchone()[0],2)

    def test_completed_export_is_bounded_read_only_and_marked_as_replay(self):
        self.delivery(stage='DELIVERED_READY')
        run_batch(self.c,self.k,self.now)
        before = self.c.total_changes
        emitted=[]
        stats=export_completed(self.c,self.now,emitted.append,limit=2)
        self.assertTrue(stats['truncated'])
        self.assertEqual(stats['exported'],2)
        self.assertEqual(self.c.total_changes,before)
        self.assertTrue(all(x['startup_replay'] for x in emitted))


if __name__=='__main__':
    unittest.main()
