import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import long_short_native_derivatives as n
import long_short_rejection_audit as audit
import long_short_rejection_direction_report as report
from long_short_structural_plan_v5 import rebuild,from_candles
from long_short_structural_plan_v4 import rebuild as frozen
from test_native_chain_v2 import fixture,NOW,SYMBOL
import test_local_geometry_continuity_v2 as geometry_fixtures
from test_local_geometry_continuity_v2 import zone

class RefreshTests(unittest.TestCase):
    def test_urgent_after_capacity_cannot_starve(self):
        requested=[f'X{i}' for i in range(120)];urgent=['X119']
        plan=n.refresh_order(requested,urgent,{},NOW,limit=90)
        self.assertEqual(plan[0],'X119');self.assertEqual(len(set(plan)),90)

    def test_refresh_old_before_fresh_and_backoff_failures(self):
        actual=n.refresh_order(['A','B','C','D'],['A','B'],
            {'A':NOW-10000,'B':NOW-100000,'C':NOW-200000},NOW,{'D':NOW+30000})
        self.assertEqual(actual,['B','C'])

    def test_one_refresh_per_symbol_and_never_stamp_stale_as_fresh(self):
        with tempfile.TemporaryDirectory() as d,patch.dict('os.environ',{'LS_STATE_DIR':d}),patch.object(n.time,'time',return_value=NOW/1000):
            with patch.object(n,'relay_get',return_value=fixture()) as upstream:
                self.assertTrue(n.refresh(SYMBOL)['oi_fresh']);self.assertEqual(upstream.call_count,1)
            c=n.database()
            with c:
                c.execute('UPDATE snapshots SET payload=?',(json.dumps(dict(fixture(),observed_ms=NOW-200000)),))
                c.execute('INSERT INTO refresh_leases VALUES(?,?)',(SYMBOL,NOW+30000))
            c.close()
            with patch.object(n,'relay_get',side_effect=AssertionError('duplicate request')):
                with self.assertRaisesRegex(ValueError,'PENDING'):n.refresh(SYMBOL)
            self.assertFalse(n.snapshot(SYMBOL)['oi_fresh'])

    def test_invalid_remote_payload_is_not_written_and_failure_backoff_is_kept(self):
        with tempfile.TemporaryDirectory() as d,patch.dict('os.environ',{'LS_STATE_DIR':d}),patch.object(n.time,'time',return_value=NOW/1000):
            with patch.object(n,'relay_get',return_value=dict(fixture(),source='WRONG')):
                with self.assertRaises(ValueError):n.refresh(SYMBOL)
            c=n.database()
            self.assertEqual(c.execute('SELECT count(*) FROM snapshots').fetchone()[0],0)
            self.assertEqual(c.execute('SELECT count(*) FROM refresh_leases').fetchone()[0],1);c.close()

    def test_archive_urls_encode_unicode_symbols(self):
        import datetime
        from urllib.parse import unquote
        from long_short_futures_kline_shadow import archive_urls
        urls=archive_urls('牛来USDT','5m',datetime.date(2026,10,15))
        self.assertTrue(urls)
        for _,_,url in urls:
            url.encode('ascii');self.assertIn('牛来USDT',unquote(url))

class GeometryV5Tests(unittest.TestCase):
    def inputs(self,side='LONG'):return geometry_fixtures.LocalGeometryTests().inputs(side)

    def test_same_common_region_is_cleared_not_used_as_target_both_sides(self):
        for side in ('LONG','SHORT'):
            p,g,z=self.inputs(side)
            z.append(zone(99.7,100.3,'RESISTANCE' if side=='LONG' else 'SUPPORT','1h'))
            old,og=frozen(p,g,z,.3,1,.9)
            self.assertFalse(og['qualified_precheck'])
            before=copy.deepcopy((p,g,z));new,ng=rebuild(p,g,z,.3,1,.9)
            self.assertEqual(before,(p,g,z));self.assertTrue(ng['qualified_precheck'])
            self.assertEqual(new['trigger_level'],100.3 if side=='LONG' else 99.7)
            self.assertEqual(new['target1'],104 if side=='LONG' else 96)
            self.assertEqual(new['invalidation'],p['invalidation'])
            self.assertAlmostEqual(new['retest_high']-new['retest_low'],.2)

    def test_independent_overlapping_obstacle_is_not_skipped(self):
        p,g,z=self.inputs();z.append(zone(100.05,100.5,timeframe='1h'))
        _,ng=rebuild(p,g,z,.3,1,.9)
        self.assertEqual(ng['structural_plan_v3']['reason'],'ENTRY_INSIDE_OPPOSING_ZONE')
        self.assertFalse(ng['qualified_precheck'])

    def test_wide_common_region_is_not_chased_and_stale_metrics_removed(self):
        p,g,z=self.inputs();z.append(zone(99,110,timeframe='4h'))
        g.update(room_pct=4,net_t1_r=3)
        new,ng=rebuild(p,g,z,.3,1,.9)
        self.assertFalse(ng['qualified_precheck']);self.assertEqual(new['target1'],0)
        self.assertEqual(ng['structural_plan_v3']['reason'],'COMMON_REGION_TOO_WIDE')
        self.assertIsNone(ng['room_pct']);self.assertIsNone(ng['net_t1_r'])

    def test_costs_near_obstacle_and_missing_target_still_block(self):
        p,g,z=self.inputs();z[1]=zone(100.5,100.6)
        new,ng=rebuild(p,g,z,.3,1,.9)
        self.assertFalse(ng['qualified_precheck']);self.assertEqual(new['target1'],100.5)
        _,ng=rebuild(p,g,z[:1],.3,1,.9)
        self.assertEqual(ng['structural_plan_v3']['reason'],'NO_OBSERVED_TARGET')

    def test_identical_inputs_compare_frozen_and_new_versions(self):
        p,g,z=self.inputs();_,ng=from_candles(p,g,[(None,'5m',1)],100,lambda *a:z,.3,1,.9)
        self.assertEqual(set(ng['geometry_comparison'])&{'v4','v5'},{'v4','v5'})
        self.assertFalse(ng['geometry_comparison']['pnl_comparison'])

class DirectionReportTests(unittest.TestCase):
    def setUp(self):
        self.c=sqlite3.connect(':memory:');self.c.row_factory=sqlite3.Row
        self.c.execute('CREATE TABLE observations(id INTEGER PRIMARY KEY,symbol,observed_ms,cohort,direction,price,payload)')
        audit.init(self.c)
        self.c.execute('CREATE TABLE forward_labels(observation_id,horizon_minutes,state,payload,checked_ms)')
        self.now=audit.CUTOFF_MS+240*60000
    def tearDown(self):self.c.close()
    def add(self,i,direction,ret=None,state='RESOLVED',strategy='v5'):
        result={'state':state,'net_return_pct':-.2,'net_r':-.1} if state=='RESOLVED' else {'state':state}
        self.c.execute('INSERT INTO rejection_audit_episodes VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            (i,'XUSDT',direction,audit.CUTOFF_MS,strategy,'FULL_4_OF_4','STRUCTURE_BLOCKED',self.now,state,
             json.dumps({'structure_reason':'INSUFFICIENT_GEOMETRY'}),json.dumps(result)))
        if ret is not None:
            label=dict(return_pct=ret,max_up_pct=4,max_down_pct=-3,hits={'up_3pct_first_full_minute':2,'down_3pct_first_full_minute':5})
            self.c.execute('INSERT INTO forward_labels VALUES(?,?,?,?,?)',(i,15,'RESOLVED',json.dumps(label),self.now))
    def test_direction_accuracy_is_not_target_hit_or_net_profit(self):
        self.add(1,'LONG',2);self.add(2,'SHORT',-2);self.add(3,'SHORT',2)
        groups=report.report(self.c,self.now)['groups'];self.assertEqual(len(groups),2)
        long=next(g for g in groups if g['direction']=='LONG');short=next(g for g in groups if g['direction']=='SHORT')
        self.assertEqual(long['horizons']['15']['correct'],1)
        self.assertEqual(long['hypothetical_net_pnl']['net_losses'],1)
        self.assertEqual(short['horizons']['15']['correct'],1);self.assertEqual(short['horizons']['15']['wrong'],1)
        self.assertEqual(short['horizons']['30']['unresolved'],2)
    def test_unknown_direction_cohorts_and_missing_paths_are_separate(self):
        self.add(1,'NONE',-2,state='NO_FROZEN_PLAN');self.add(2,'LONG',None);self.add(3,'LONG',3,strategy='v4')
        groups=report.report(self.c,self.now)['groups'];self.assertEqual(len(groups),3)
        unknown=next(g for g in groups if g['direction']=='NONE')['horizons']['15']
        self.assertEqual(unknown['down'],1);self.assertEqual(unknown['direction_evaluated'],0)
        self.assertIsNone(unknown['correct_pct'])
        pending=report.report(self.c,audit.CUTOFF_MS+60000)['groups']
        self.assertTrue(all(g['horizons']['15']['pending']==1 for g in pending))
    def test_reporting_never_changes_stored_results(self):
        self.add(1,'LONG',3)
        before=list(self.c.execute('SELECT result FROM rejection_audit_episodes'))
        report.report(self.c,self.now)
        self.assertEqual(before,list(self.c.execute('SELECT result FROM rejection_audit_episodes')))

class RejectionCoverageTests(unittest.TestCase):
    def test_persistent_data_gap_and_unselected_coins_keep_forward_observations(self):
        import long_short_data_opportunity_v2 as data
        with tempfile.TemporaryDirectory() as d,patch.dict('os.environ',{'LS_STATE_DIR':d}),patch.object(data,'current_price',return_value=100):
            data.blocked('XUSDT','stale',NOW)
            data.blocked('XUSDT','stale',NOW+60000)
            data.blocked('XUSDT','missing',NOW+300000)
            data.record_not_selected('YUSDT',{'rank':3},NOW)
            c=data.connect();c.row_factory=sqlite3.Row
            self.assertEqual(c.execute('SELECT COUNT(*) FROM samples').fetchone()[0],3)
            rows=c.execute('SELECT * FROM observations ORDER BY id').fetchall()
            self.assertEqual(len(rows),3)
            self.assertEqual(audit.frozen_snapshot(rows[-1])['block_class'],'SCAN_SELECTION_BLOCKED')
            self.assertEqual(audit.frozen_snapshot(rows[-1])['direction'],'NONE')
            c.close()

if __name__=='__main__':unittest.main()
