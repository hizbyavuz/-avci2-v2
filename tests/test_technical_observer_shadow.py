"""Explicit synthetic engineering fixtures; these are not market samples."""
from copy import deepcopy
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from tests.test_technical_observer_regression import random_frames
from technical_observer_v1.engine import swings
from technical_observer_v1.records import digest
from technical_observer_v1.shadow_config import OPS
from technical_observer_v1 import shadow_store as store
from technical_observer_v1.shadow_runtime import (append_prefix, readonly, scan_symbol,
                                                 tick_catalogue, tick_for, main)
from technical_observer_v1.shadow_outcomes import barrier_contacts, measure_pending, validate_path
from structure_shadow_start import supervise


SOURCE='BINANCE_UM_FUTURES_REST'


class ShadowFixture(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.directory=self.root/'observer';self.path=self.root/'collector.db'
        self.writer=sqlite3.connect(self.path)
        self.writer.execute('''CREATE TABLE futures_closed_klines(symbol TEXT,interval TEXT,
            open_ms INTEGER,close_ms INTEGER,open REAL,high REAL,low REAL,close REAL,
            quote_volume REAL,taker_buy_quote REAL,source TEXT,received_ms INTEGER,
            PRIMARY KEY(symbol,interval,open_ms))''')
        self.frames,self.now=random_frames(1)
        for tf,rows in self.frames.items():
            for b in rows:
                b.update(quote_volume=b['quote_volume']*1000,taker_buy_quote=b['quote_volume']*500,
                         source=SOURCE,received_ms=b['close_ms']+1)
                self.add(tf,b)
        self.writer.commit();self.source=readonly(self.path)
        self.db=store.connect(self.directory/'shadow.db',self.now-900_000)
        market=dict(symbol='SYNTHUSDT',filters=[dict(filterType='PRICE_FILTER',minPrice='0.001',
                                                   maxPrice='100000',tickSize='0.001')])
        payload=dict(source=SOURCE,markets=[market])
        self.catalogue=(self.now-1,{'SYNTHUSDT':market},digest(payload))

    def add(self,tf,b):
        self.writer.execute('INSERT INTO futures_closed_klines VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
            ('SYNTHUSDT',tf,*[b[k] for k in ('open_ms','close_ms','open','high','low','close',
                                          'quote_volume','taker_buy_quote','source','received_ms')]))

    def tearDown(self):
        self.source.close();self.writer.close();self.db.close();self.temp.cleanup()

    def minutes(self,start,count=180):
        rows=[]
        for i in range(count):
            row=dict(open_ms=start+i*60_000,close_ms=start+(i+1)*60_000-1,
                     open=100.,high=101.,low=99.,close=100.5,quote_volume=1000.,taker_buy_quote=500.,
                     source=SOURCE,received_ms=start+(i+1)*60_000)
            rows.append(row);self.add('1m',row)
        self.writer.commit();return rows

    def seed_pair(self,pair_ready=True):
        key='fixture-scan'
        group,cohort=store.assign_group(self.db,'SYNTHUSDT',self.now,self.now-1,key,pair_ready)
        pair=dict(old=dict(decisions=[dict(direction='LONG',entry_reference=100.,stop=98.,
              target=102.,event_id='SYNTHETIC-REJECTED',plan_eligible=False)]),new_records=[])
        path=store.write_blob(self.directory,pair)
        self.db.execute('INSERT INTO scans VALUES(?,?,?,?,?,?,?,?,?)',
                        (key,'SYNTHUSDT',self.now-1,self.now,cohort,int(pair_ready),group,path,store.pack({})))
        self.db.commit();return self.db.execute('SELECT * FROM groups_v1 WHERE id=?',(group,)).fetchone()


class PrefixTests(ShadowFixture):
    def test_readonly_collector_and_future_candles_excluded(self):
        with self.assertRaises(sqlite3.OperationalError):self.source.execute('DELETE FROM futures_closed_klines')
        original=self.path.read_bytes()
        rows=append_prefix(self.db,self.source,'SYNTHUSDT','15m',self.now)
        self.assertEqual(len(rows),220);self.assertLess(rows[-1]['close_ms'],self.now)
        self.assertEqual(self.path.read_bytes(),original)

    def test_fixed_prefix_survives_source_pruning_and_restart(self):
        before=append_prefix(self.db,self.source,'SYNTHUSDT','15m',self.now)
        prior_swings=swings(before,'15m')['confirmed']
        self.writer.execute("DELETE FROM futures_closed_klines WHERE interval='15m' AND open_ms<?",
                            (before[-8]['open_ms'],))
        row=deepcopy(before[-1]);row.update(open_ms=self.now,close_ms=self.now+899999,received_ms=self.now+900000)
        self.add('15m',row);self.writer.commit()
        self.db.close();self.db=store.connect(self.directory/'shadow.db',self.now+900000)
        after=append_prefix(self.db,self.source,'SYNTHUSDT','15m',self.now+900000)
        self.assertEqual(after[:-1],before)
        self.assertEqual(swings(after,'15m')['confirmed'][:len(prior_swings)],prior_swings)
        self.assertEqual(self.db.execute('SELECT started_ms FROM activation').fetchone()[0],self.now-900000)

    def test_source_revision_quarantines_without_changing_saved_bar(self):
        original=append_prefix(self.db,self.source,'SYNTHUSDT','15m',self.now)
        self.writer.execute("UPDATE futures_closed_klines SET close=close+.1 WHERE interval='15m' AND open_ms=?",
                            (original[-1]['open_ms'],));self.writer.commit()
        with self.assertRaisesRegex(ValueError,'SOURCE_REVISION'):
            append_prefix(self.db,self.source,'SYNTHUSDT','15m',self.now)
        with self.assertRaisesRegex(ValueError,'QUARANTINED'):
            append_prefix(self.db,self.source,'SYNTHUSDT','15m',self.now)
        row=self.db.execute("SELECT payload FROM bars WHERE tf='15m' ORDER BY open_ms DESC LIMIT 1").fetchone()
        self.assertEqual(store.unpack(row[0]),original[-1])

    def test_gap_and_future_received_are_not_filled(self):
        self.writer.execute("DELETE FROM futures_closed_klines WHERE interval='15m' AND open_ms=?",
                            (self.frames['15m'][-20]['open_ms'],));self.writer.commit()
        with self.assertRaisesRegex(ValueError,'CANDLE_GAP'):
            append_prefix(self.db,self.source,'SYNTHUSDT','15m',self.now)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM bars').fetchone()[0],0)

    def test_prefix_cap_never_reseeds(self):
        with patch.dict(OPS,max_prefix_bars=219):
            with self.assertRaisesRegex(ValueError,'NO_RESEED'):
                append_prefix(self.db,self.source,'SYNTHUSDT','15m',self.now)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM streams').fetchone()[0],0)


class RecordingTests(ShadowFixture):
    def test_worker_once_reads_existing_sources_and_reports_real_counters(self):
        metadata=self.root/'metadata.db';m=sqlite3.connect(metadata)
        m.execute('CREATE TABLE market_metadata(id INTEGER,observed_ms INTEGER,payload TEXT)')
        m.execute('INSERT INTO market_metadata VALUES(1,?,?)',(self.now-1,json.dumps(
                  dict(source=SOURCE,markets=list(self.catalogue[1].values())))))
        m.commit();m.close();output=io.StringIO()
        argv=['shadow','--source-db',str(self.path),'--metadata-db',str(metadata),
              '--state-dir',str(self.directory),'--once']
        with patch.dict(os.environ,TA_OBSERVER_SHADOW='1'),patch('sys.argv',argv),\
             patch('technical_observer_v1.shadow_runtime.time.time',return_value=self.now/1000),\
             patch('technical_observer_v1.shadow_runtime.signal.signal'),\
             patch('long_short_swing_runtime.universe',return_value=[('SYNTHUSDT',1)]),\
             redirect_stdout(output):
            self.assertEqual(main(),0)
        health=json.loads(output.getvalue().split('TA_SHADOW_HEALTH ')[-1])
        self.assertEqual(health['states']['PAIRED'],1);self.assertEqual(health['issues'],{})
        self.assertEqual(health['completed_forward_groups'],0)

    def test_full_scan_saved_once_new_rejected_candidate_kept(self):
        result=scan_symbol(self.db,self.source,self.catalogue,'SYNTHUSDT',self.now,self.directory)
        self.assertEqual(result['state'],'PAIRED');self.assertGreater(result['setups'],0)
        row=self.db.execute('SELECT * FROM scans').fetchone();pair=store.read_blob(self.directory,row['evidence'])
        for ref in pair['new_records']:
            record=store.read_blob(self.directory,ref['evidence'])
            self.assertFalse(record['order_authorized']);self.assertEqual(record['decision_effect'],'NONE')
            self.assertIn('frames',record);self.assertIn('setup',record['evidence'])
            self.assertEqual(record['display']['levels']['entry'],
                             record['display']['broken_region']['high' if record['direction']=='LONG' else 'low'])
        again=scan_symbol(self.db,self.source,self.catalogue,'SYNTHUSDT',self.now+1000,self.directory)
        self.assertEqual(again['state'],'ALREADY_SCANNED')
        self.assertEqual(store.counters(self.db)['scans'],1)

    def test_old_rejected_candidates_retained_without_new_filtering(self):
        old=dict(status='VALID',decisions=[dict(event_id='synthetic-rejection',plan_eligible=False,
                 direction='SHORT',entry_reference=100,stop=125,target=99)],context={})
        with patch('technical_observer_v1.shadow_runtime.assess',return_value=old):
            scan_symbol(self.db,self.source,self.catalogue,'SYNTHUSDT',self.now,self.directory)
        row=self.db.execute('SELECT evidence FROM scans').fetchone()
        pair=store.read_blob(self.directory,row[0]);self.assertEqual(pair['old']['decisions'],old['decisions'])
        self.assertEqual(pair['decision_effect'],'NONE')

    def test_data_failure_recorded_not_counted_and_can_heal(self):
        first=scan_symbol(self.db,self.source,None,'SYNTHUSDT',self.now,self.directory)
        self.assertEqual(first['state'],'RECORDED_WITH_DATA_ISSUE')
        second=scan_symbol(self.db,self.source,self.catalogue,'SYNTHUSDT',self.now+1,self.directory)
        self.assertEqual(second['state'],'PAIRED')
        self.assertEqual(store.counters(self.db)['completed_forward_groups'],0)
        self.assertEqual(store.counters(self.db)['scans'],2)

    def test_no_current_candidate_scan_kept(self):
        f,t=random_frames(0)
        for tf,rows in f.items():
            for b in rows:
                self.writer.execute('UPDATE futures_closed_klines SET open=?,high=?,low=?,close=? WHERE interval=? AND open_ms=?',
                    (b['open'],b['high'],b['low'],b['close'],tf,b['open_ms']))
        self.writer.commit()
        result=scan_symbol(self.db,self.source,self.catalogue,'SYNTHUSDT',t,self.directory)
        self.assertEqual(result['setups'],0);self.assertEqual(store.counters(self.db)['scans'],1)

    def test_evidence_immutable_and_corruption_detected(self):
        relative=store.write_blob(self.directory,dict(test='SYNTHETIC'))
        self.assertEqual(store.write_blob(self.directory,dict(test='SYNTHETIC')),relative)
        (self.directory/relative).write_bytes(store.pack(dict(test='ALTERED')))
        with self.assertRaisesRegex(ValueError,'CORRUPT'):store.read_blob(self.directory,relative)

    def test_metadata_missing_stale_future_and_source_rejected(self):
        m=sqlite3.connect(':memory:');m.row_factory=sqlite3.Row
        m.execute('CREATE TABLE market_metadata(id INTEGER,observed_ms INTEGER,payload TEXT)')
        for timestamp,source in ((self.now+1,SOURCE),(self.now-3600001,SOURCE),(self.now,'UNKNOWN')):
            m.execute('DELETE FROM market_metadata')
            m.execute('INSERT INTO market_metadata VALUES(1,?,?)',(timestamp,json.dumps(dict(source=source,markets=[]))))
            with self.assertRaises(ValueError):tick_catalogue(m,self.now)
        m.close()
        tick=tick_for(self.catalogue,'SYNTHUSDT');self.assertEqual(tick['price_filter']['tickSize'],'0.001')
        self.assertEqual(tick['catalogue_sha256'],self.catalogue[2])


class MeasurementTests(ShadowFixture):
    def test_symbol_overlap_both_sides_cannot_inflate_count(self):
        first=store.assign_group(self.db,'SYNTHUSDT',self.now,self.now-1,'LONG',True)[0]
        second=store.assign_group(self.db,'SYNTHUSDT',self.now+900000,self.now+899999,'SHORT',True)[0]
        self.assertEqual(first,second)
        third=store.assign_group(self.db,'SYNTHUSDT',self.now+10800000,self.now+10799999,'NEXT',True)[0]
        self.assertNotEqual(first,third)
        self.assertEqual(store.counters(self.db)['deduplicated_groups'],2)
        self.assertFalse(store.counters(self.db)['review_ready'])

    def test_complete_rejected_observation_counted_without_fake_trade(self):
        group=self.seed_pair();self.minutes(group['start_ms'])
        result=measure_pending(self.db,self.source,group['end_ms'],self.directory)
        self.assertEqual(result['complete'],1)
        counters=store.counters(self.db);self.assertEqual(counters['completed_forward_groups'],1)
        self.assertFalse(counters['automatic_promotion']);self.assertFalse(counters['review_ready'])
        row=self.db.execute('SELECT evidence FROM outcomes').fetchone()
        outcome=store.read_blob(self.directory,row[0]);self.assertIsNone(outcome['trade_pnl'])
        self.assertFalse(outcome['old'][0]['entry_assumed'])
        self.assertIsNone(outcome['old'][0]['pnl'])
        self.assertEqual(measure_pending(self.db,self.source,group['end_ms']+1,self.directory)['checked'],0)

    def test_gap_waits_then_unresolved_not_counted(self):
        group=self.seed_pair();self.minutes(group['start_ms'],170)
        first=measure_pending(self.db,self.source,group['end_ms'],self.directory)
        self.assertEqual(first['waiting_data'],1)
        second=measure_pending(self.db,self.source,group['end_ms']+OPS['measurement_grace_ms'],self.directory)
        self.assertEqual(second['unresolved'],1)
        self.assertEqual(store.counters(self.db)['completed_forward_groups'],0)

    def test_startup_replay_never_counts(self):
        self.db.execute('UPDATE activation SET started_ms=?',(self.now+1,));self.db.commit()
        group=self.seed_pair();self.minutes(group['start_ms'])
        measure_pending(self.db,self.source,group['end_ms'],self.directory)
        self.assertEqual(store.counters(self.db)['completed_forward_groups'],0)

    def test_only_closed_available_valid_minutes_count(self):
        start=self.now+60000;rows=self.minutes(start,2)
        self.assertEqual(validate_path(rows,start,start+120000,start+120000),'COMPLETE')
        self.assertEqual(validate_path(rows,start,start+120000,start+119999),'UNAVAILABLE_CANDLE')
        rows[-1]['received_ms']=start+120001
        self.assertEqual(validate_path(rows,start,start+120000,start+120000),'UNAVAILABLE_CANDLE')

    def test_ambiguous_barrier_contacts_never_invent_sequence(self):
        bars=[dict(open=100,high=103,low=97,close=100,close_ms=self.now)]
        result=barrier_contacts('LONG',98,102,bars)
        self.assertEqual(result['first'],'SAME_MINUTE_ORDER_UNKNOWN');self.assertFalse(result['entry_assumed'])
        inverse=barrier_contacts('SHORT',102,98,bars)
        self.assertEqual(result,inverse)


class IsolationTests(unittest.TestCase):
    def test_descendant_memory_included_without_raising_existing_limit(self):
        from structure_shadow_start import process_resources
        class Child:
            pid=1001
            def poll(self):return None
        def descendants(path):
            return '1002 1003' if '/1001/' in str(path) else ''
        with patch('structure_shadow_start.Path.read_text',descendants),\
             patch('structure_shadow_start.snapshot',side_effect=lambda p:p):
            included=process_resources((Child(),))
        self.assertEqual(set(included),{'1001','1002','1003'})

    def test_shadow_failure_does_not_stop_or_restart_primary(self):
        class Stop:
            count=0
            def is_set(self):return self.count>=3
            def wait(self,_):self.count+=1
        class Child:
            def __init__(self,code):self.returncode=code;self.pid=123;self.terminated=False
            def poll(self):return self.returncode
            def terminate(self):self.terminated=True;self.returncode=0
            def wait(self,timeout):return self.returncode
        stop=Stop();primary=Child(None);shadow=Child(3);calls=[]
        def spawn(command,env):
            calls.append(command)
            if len(calls)>1:self.assertFalse(primary.terminated)
            return primary if len(calls)==1 else shadow
        with tempfile.TemporaryDirectory() as d:
            for name in ('binance_futures_kline_shadow.db','native_derivatives_v2.db'):(Path(d)/name).touch()
            with patch('builtins.print'):
                self.assertEqual(supervise(d,stop,spawn),0)
        self.assertEqual(len(calls),2);self.assertEqual(stop.count,3)

    def test_shadow_storage_guard_is_separate(self):
        with tempfile.TemporaryDirectory() as d:
            directory=Path(d)/'shadow';directory.mkdir();(directory/'synthetic').write_bytes(b'x'*4096)
            with patch.dict(OPS,storage_limit_mb=.001):
                result=store.limits(directory)
        self.assertFalse(result['ok']);self.assertIn('SHADOW_STORAGE_LIMIT',result['reasons'])


if __name__=='__main__':unittest.main()
