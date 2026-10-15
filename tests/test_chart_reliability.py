import contextlib
import io
import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from test_chart_first import sample, NOW
import test_chart_lifecycle as lifecycle
import long_short_chart_first as chart
from long_short_chart_first_worker import connect, record, resolve, advance
from long_short_chart_dispatch_audit import admission_reason, note
from long_short_chart_reliability import summarize, build, stats, rejection_review


class ReliabilityTests(unittest.TestCase):
    def test_old_confirmation_is_not_advertised_as_ready_both_sides(self):
        for short in (False, True):
            frames,price=sample(short)
            fresh=chart.assess('XUSDT',frames,price,50e6,NOW)
            stale=chart.assess('XUSDT',frames,price,50e6,NOW+80000)
            self.assertEqual(fresh['stage'],'READY')
            self.assertEqual(stale['stage'],'REJECTED')
            self.assertEqual(stale['reason'],'CONFIRMATION_WINDOW_EXPIRED')

    def test_reversal_gate_never_revives_expired_price_time_or_changed_direction(self):
        for cause in ('time','price','direction'):
            for short in (False,True):
                with tempfile.TemporaryDirectory() as root,patch.dict(os.environ,{'LS_STATE_DIR':root}),patch('time.time',return_value=NOW/1000):
                    c=connect();k,p=lifecycle.LifecycleTests().db(short)
                    p['stage']='WATCH'
                    if cause=='time':p['created_ms']=NOW-181*60000
                    elif cause=='price':p['entry_limit']=p['price']+(.01 if short else -.01)
                    c.execute('INSERT INTO episodes VALUES(?,?,?,?,?,?,?,?)',
                        ('e','XUSDT',p['direction'],p['created_ms'],'WATCH','WATCH',None,json.dumps(p)));c.commit()
                    decision=dict(p,stage='REJECTED',reason='REVERSAL_FLOW_NOT_ALIGNED')
                    if cause=='direction':decision['direction']='LONG' if short else 'SHORT'
                    with patch('long_short_chart_first_worker.path_guard',return_value={'status':'CLEAR'}):
                        advance(c,k,{'XUSDT':decision},NOW,Mock(return_value={'status':'ACK'}),'token','123')
                    row=c.execute('SELECT stage,ended_ms FROM episodes').fetchone()
                    self.assertEqual(row[0],'EXPIRED',cause)
                    self.assertIsNotNone(row[1])
                    c.close();k.close()

    def test_admission_reports_cap_active_and_same_coin_cooldown(self):
        p={'strategy_id':'new'}
        r=dict(ended_ms=NOW-1000,created_ms=NOW-60000,payload=json.dumps(p))
        self.assertEqual(admission_reason(r,p,NOW,3,chart.CONFIG),'CYCLE_NOTICE_CAP')
        self.assertEqual(admission_reason(dict(r,ended_ms=None),p,NOW,0,chart.CONFIG),'EXISTING_ACTIVE_EPISODE')
        self.assertEqual(admission_reason(r,p,NOW,0,chart.CONFIG),'SAME_COIN_COOLDOWN')
        self.assertIsNone(admission_reason(r,p,NOW+3600000,0,chart.CONFIG))
        self.assertIsNone(admission_reason(r,{'strategy_id':'other'},NOW,0,chart.CONFIG))

    def test_dispatch_notes_are_bounded_per_bucket(self):
        with tempfile.TemporaryDirectory() as root,patch.dict(os.environ,{'LS_STATE_DIR':root}):
            c=connect();p=dict(strategy_id='x',symbol='X',direction='LONG',stage='READY',setup_key='s')
            for _ in range(100):note(c,p,NOW,'CREATE','SAME_COIN_COOLDOWN')
            self.assertEqual(c.execute('SELECT COUNT(*) FROM dispatch_audit').fetchone()[0],1)
            note(c,p,NOW+300000,'CREATE','SAME_COIN_COOLDOWN')
            self.assertEqual(c.execute('SELECT COUNT(*) FROM dispatch_audit').fetchone()[0],2)
            c.close()

    def test_deliveries_keep_distinct_episode_and_cohort_identity(self):
        with tempfile.TemporaryDirectory() as root,patch.dict(os.environ,{'LS_STATE_DIR':root}):
            c=connect()
            base=dict(strategy_id='old',symbol='X',observed_ms=NOW,direction='LONG',
                stage='DELIVERED_READY',reason='TELEGRAM_ACK',price=100,trigger=100,stop=99)
            for sid,episode in [('old','a'),('old','b'),('new','a'),('old','a')]:
                record(c,dict(base,strategy_id=sid,episode_id=episode))
            self.assertEqual(c.execute('SELECT COUNT(*) FROM assessments').fetchone()[0],3)
            c.close()

    def test_resolver_uses_signal_time_cost_not_current_release_cost(self):
        with tempfile.TemporaryDirectory() as root,patch.dict(os.environ,{'LS_STATE_DIR':root}):
            c=connect();k=sqlite3.connect(':memory:')
            k.execute('CREATE TABLE futures_closed_klines(symbol,interval,open_ms,open,high,low,close,volume,close_ms,source)')
            for i in range(1,17):
                k.execute('INSERT INTO futures_closed_klines VALUES(?,?,?,?,?,?,?,?,?,?)',
                    ('X','1m',i*60000,100,100.1,99.9,100,1,i*60000+59999,'BINANCE_UM_FUTURES_WEBSOCKET'))
            p=dict(version=chart.VERSION,strategy_id='old',outcome_schema='PARTIAL_MINUTE_V2',
                cost_assumption_pct=.7,symbol='X',observed_ms=60000,price=100,direction='LONG',
                stage='DELIVERED_READY',reason='TEST',stop=99,obstacle=102)
            record(c,p);c.commit();resolve(c,k,1200000)
            result=json.loads(c.execute('SELECT payload FROM outcomes WHERE horizon=15').fetchone()[0])
            self.assertAlmostEqual(result['model_net_return_pct'],-.7)
            self.assertEqual(result['cost_assumption_pct'],.7)
            c.close();k.close()

    def row(self,sid='a',stage='DELIVERED_READY',h=180,state='RESOLVED',episode='e',net=1):
        p=dict(strategy_id=sid,version='v',episode_id=episode,model='M')
        v=dict(directional_return_pct=2,model_net_return_pct=net,first_level='OBSTACLE')
        return dict(payload=json.dumps(p),horizon=h,stage=stage,event_key=episode,symbol='X',
            observed_ms=60000,direction='LONG',reason='OK',state=state,outcome_payload=json.dumps(v))

    def test_report_keeps_cohorts_horizons_and_observations_separate(self):
        rows=[self.row(),self.row(),self.row(sid='b',net=-2),self.row(h=15),self.row(stage='REJECTED')]
        report=summarize(rows,NOW)
        self.assertEqual(len(report['groups']),4)
        self.assertEqual(report['deduplicated_rows'],1)
        rejected=next(g for g in report['groups'] if g['stage']=='REJECTED')
        self.assertEqual(rejected['reference_net']['n'],0)
        self.assertEqual(rejected['direction_only']['mean_pct'],2)
        self.assertFalse(report['edge_proven'])

    def test_missing_and_unresolved_are_not_silently_counted_as_wins_or_losses(self):
        missing=self.row(state=None);unknown=self.row(state='UNRESOLVED_BOUNDARY',episode='b')
        unknown['outcome_payload']=json.dumps({'pessimistic_net_return_pct':-1.3})
        g=summarize([missing,unknown],NOW)['groups'][0]
        self.assertEqual(g['missing'],1);self.assertEqual(g['unresolved'],1)
        self.assertEqual(g['reference_net']['n'],0)
        self.assertAlmostEqual(g['known_boundary_pessimistic']['mean_pct'],-1.3)
        self.assertFalse(g['sensitivity_complete'])

    def test_report_reads_existing_db_without_altering_assessments(self):
        with tempfile.TemporaryDirectory() as root,patch.dict(os.environ,{'LS_STATE_DIR':root}):
            c=connect();f,price=sample();record(c,chart.assess('X',f,price,50e6,NOW));c.commit()
            before=c.total_changes;r=build(root,NOW+20000000)
            self.assertEqual(c.total_changes,before)
            self.assertEqual(len(r['groups']),3)
            self.assertTrue(all(g['missing']==1 for g in r['groups']))
            self.assertFalse(r['edge_proven']);c.close()

    def test_reference_stats_no_losses_not_infinite_profit_factor(self):
        self.assertIsNone(stats([1,2])['profit_factor'])
        self.assertEqual(stats([1,-2])['profit_factor'],.5)

    def test_rejection_review_measures_without_outcome_queue_or_lookahead_selection(self):
        with tempfile.TemporaryDirectory() as root,patch.dict(os.environ,{'LS_STATE_DIR':root}):
            c=connect();k=sqlite3.connect(root+'/binance_futures_kline_shadow.db')
            k.execute('CREATE TABLE futures_closed_klines(symbol,interval,open_ms,open,high,low,close,volume,close_ms,source)')
            for symbol,side in [('A','LONG'),('B','SHORT')]:
                record(c,dict(strategy_id=chart.STRATEGY_ID,symbol=symbol,observed_ms=60000,
                    price=100,direction=side,stage='REJECTED',reason='NO_DEFINED_SETUP'))
                for i in range(1,16):
                    k.execute('INSERT INTO futures_closed_klines VALUES(?,?,?,?,?,?,?,?,?,?)',
                        (symbol,'1m',i*60000,100,102,99,101,1,i*60000+59999,'BINANCE_UM_FUTURES_WEBSOCKET'))
            c.commit();k.commit()
            r=rejection_review(c,root,1200000)
            self.assertEqual(r['measured'],2);self.assertEqual(r['missing'],0)
            self.assertAlmostEqual(r['groups'][0]['raw_directional_endpoint']['mean_pct'],0)
            self.assertEqual(r['examples_best'][0]['symbol'],'A')
            self.assertEqual(r['examples_worst'][0]['symbol'],'B')
            self.assertEqual(c.execute('SELECT COUNT(*) FROM outcomes').fetchone()[0],0)
            k.execute("DELETE FROM futures_closed_klines WHERE symbol='B' AND open_ms=60000");k.commit()
            self.assertEqual(rejection_review(c,root,1200000)['missing'],1)
            self.assertEqual(rejection_review(c,root,900000)['observations'],0)
            k.close();c.close()


if __name__=='__main__':unittest.main()
