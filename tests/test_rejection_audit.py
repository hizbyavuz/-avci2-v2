import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import long_short_rejection_audit as m
from long_short_native_derivatives import SOURCE


def bar(t,o=100,h=100.5,l=99.5,c=100):
    return [t,o,h,l,c,1000,t+59999]


def snap(**kw):
    return dict(symbol='XUSDT',direction='LONG',observed_ms=m.CUTOFF_MS+1234,
                stop=98,target=104,cost_pct=.3,**kw)


def funding(path,args):
    return {'source':SOURCE,'symbol':args['symbol'],'rates':[]}


class RejectionTests(unittest.TestCase):
    def setUp(self):
        self.s=snap();self.start=(self.s['observed_ms']//60000+1)*60000

    def result(self,bars,now=None,reader=funding):
        return m.outcome(self.s,now or self.start+181*60000,lambda *a:bars,reader)

    def test_frozen_long_and_short_not_future_best_direction(self):
        r=self.result([bar(self.start,h=105,l=99)])
        self.assertEqual(r['result'],'TP1');self.assertAlmostEqual(r['net_return_pct'],3.7)
        self.s.update(direction='SHORT',stop=102,target=96)
        r=self.result([bar(self.start,h=101,l=95)])
        self.assertEqual(r['result'],'TP1');self.assertAlmostEqual(r['net_return_pct'],3.7)

    def test_low_r_rejected_plan_is_measured_without_rejecting_it_again(self):
        self.s.update(stop=95,target=100.1)
        r=self.result([bar(self.start,h=100.2,l=99.9)])
        self.assertEqual(r['state'],'RESOLVED');self.assertEqual(r['result'],'TP1')
        self.assertLess(r['net_return_pct'],0) # target hit is not a net win

    def test_same_bar_stop_first_and_gap_loss(self):
        r=self.result([bar(self.start,h=105,l=97)])
        self.assertEqual(r['result'],'STOP')
        r=self.result([bar(self.start),bar(self.start+60000,95,96,94,95)])
        self.assertEqual(r['exit_price'],95);self.assertLess(r['net_r'],-2)

    def test_missing_before_exit_unresolved_but_missing_after_exit_irrelevant(self):
        r=self.result([bar(self.start),bar(self.start+120000)])
        self.assertEqual(r['state'],'DATA_UNRESOLVED')
        self.assertEqual(self.result([bar(self.start,h=105,l=99)])['state'],'RESOLVED')

    def test_signal_minute_gain_and_unclosed_candle_excluded(self):
        rows=[bar(self.start-60000,h=120,l=80),bar(self.start)]
        self.assertEqual(self.result(rows,self.start+59999)['state'],'PENDING')
        self.assertEqual(self.result(rows,self.start+60000)['state'],'PENDING')

    def test_stop_target_and_cost_never_invented(self):
        for key,value in [('stop',None),('target',0),('cost_pct',None),('cost_pct',float('nan')),('direction','NONE')]:
            old=self.s[key];self.s[key]=value
            self.assertEqual(self.result([])['state'],'NO_FROZEN_PLAN');self.s[key]=old

    def test_late_entry_is_not_credited_with_prior_gain(self):
        self.assertEqual(self.result([bar(self.start,105,106,104,105)])['state'],'ENTRY_MISSED')

    def test_funding_missing_not_assumed_zero(self):
        r=self.result([bar(self.start,h=105,l=99)],reader=lambda *a:{})
        self.assertEqual(r['state'],'DATA_UNRESOLVED');self.assertNotIn('net_return_pct',r)

    def test_funding_sign_and_cost_sensitivity(self):
        def rates(path,args):
            return {'source':SOURCE,'symbol':'XUSDT','rates':[{'symbol':'XUSDT','fundingTime':self.start,'fundingRate':'.001','markPrice':'100'}]}
        r=self.result([bar(self.start,h=105,l=99)],reader=rates)
        self.assertAlmostEqual(r['net_return_pct'],3.6)
        self.assertAlmostEqual(r['additional_cost_sensitivity_pct']['plus_0.7_percentage_points'],2.9)
        self.s.update(direction='SHORT',stop=102,target=96)
        r=self.result([bar(self.start,h=101,l=95)],reader=rates)
        self.assertAlmostEqual(r['net_return_pct'],3.8)

    def test_duplicate_funding_is_unresolved(self):
        def rates(path,args):
            row={'symbol':'XUSDT','fundingTime':self.start,'fundingRate':'.001','markPrice':'100'}
            return {'source':SOURCE,'symbol':'XUSDT','rates':[row,row]}
        self.assertEqual(self.result([bar(self.start,h=105,l=99)],reader=rates)['state'],'DATA_UNRESOLVED')

    def test_timeout_uses_predeclared_180_minutes(self):
        rows=[bar(self.start+i*60000) for i in range(180)]
        r=self.result(rows,self.start+180*60000)
        self.assertEqual(r['result'],'TIMEOUT');self.assertAlmostEqual(r['net_return_pct'],-.3)

    def test_ingestion_cutoff_dedup_cohorts_and_outcomes_are_immutable(self):
        with tempfile.TemporaryDirectory() as td:
            path=os.path.join(td,'research.db')
            c=sqlite3.connect(path);c.row_factory=sqlite3.Row
            c.execute('CREATE TABLE observations(id INTEGER PRIMARY KEY,symbol,observed_ms,cohort,direction,price,payload)')
            def add(oid,stamp,identity='v1',cohort='FULL_4_OF_4'):
                p={'direction_engine':{'eligible':False,'decision_reasons':['direction_score_below_minimum']},
                   'setup_plan':{'direction':'LONG','invalidation':98,'target1':104,'trigger_level':101},
                   'structure_gate':{'_strategy_identity':{'id':identity},'minimum_round_trip_cost_pct':.3}}
                c.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?)',(oid,'XUSDT',stamp,cohort,'NONE',100,json.dumps(p)))
            add(1,m.CUTOFF_MS-1);add(2,m.CUTOFF_MS+1000);add(3,m.CUTOFF_MS+61000)
            add(4,m.CUTOFF_MS+62000,'v2');add(5,m.CUTOFF_MS+63000,'v2','PARTIAL_3_OF_4')
            c.commit();m.init(c);m.ingest(c,m.CUTOFF_MS+180000)
            self.assertEqual([r[0] for r in c.execute('SELECT observation_id FROM rejection_audit_episodes ORDER BY observation_id')],[2,4,5])
            c.execute("UPDATE observations SET payload='{}' WHERE id=2");c.commit()
            m.ingest(c,m.CUTOFF_MS+180000)
            self.assertEqual(json.loads(c.execute('SELECT payload FROM rejection_audit_episodes WHERE observation_id=2').fetchone()[0])['stop'],98)
            c.close()
            result=m.resolve(m.CUTOFF_MS+240000,path,lambda sym,start,n:[bar(start,h=105,l=99)],funding)
            self.assertEqual(result['states'],{'RESOLVED':3})
            again=m.resolve(m.CUTOFF_MS+300000,path,lambda *a: (_ for _ in ()).throw(AssertionError('relabelled')),funding)
            self.assertEqual(again['states'],{'RESOLVED':3})
            self.assertEqual(len(result['groups']),3)

    def test_data_failure_without_plan_remains_visible_not_a_loss(self):
        row={'payload':json.dumps({'reason':'native stale'}),'symbol':'XUSDT','observed_ms':m.CUTOFF_MS,
             'price':100,'cohort':'DATA_BLOCKED','direction':'NONE'}
        s=m.frozen_snapshot(row)
        self.assertEqual(s['block_class'],'DATA_BLOCKED')
        self.assertEqual(m.outcome(s,self.start+60000,lambda *a:[],funding)['state'],'NO_FROZEN_PLAN')


if __name__=='__main__':unittest.main()
