import copy
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch, Mock
import long_short_major_move as core
import long_short_major_move_store as store
import long_short_major_move_worker as worker
import test_major_move as fixtures

NOW=fixtures.NOW


def wake_frames():
    f=fixtures.frames()
    for r in f['15m'][:-17]:r[2:4]=[101.,99.]
    for r in f['15m'][-17:-1]:r[1:5]=[99.8,100.,99.5,99.8]
    f['15m'][-17][1:5]=[96.,96.2,95.8,96.]
    f['15m'][-1][1:6]=[99.8,100.2,99.7,100.15,900.]
    return f


def ready():
    return core.assess('TESTUSDT',wake_frames(),100.15,10000000,NOW,fixtures.frames())


class DecisionsV2(unittest.TestCase):
    def test_complete_pipeline_fast_context_and_genuinely_closed_entry(self):
        p=ready()
        self.assertEqual((p['stage'],p['model'],p['entry_kind']),('READY','WAKE_UP','BREAK'))
        self.assertEqual(p['features']['price'],100.15)
        self.assertGreater(p['features']['relative_strength_4h_pp'],4)
        self.assertAlmostEqual(p['features']['volume_1h_multiple'],3.)
        self.assertEqual(p['features']['context_4h'],'MIXED')
        self.assertIn('15 dakikalık kırılma',core.message(p));self.assertLess(len(core.message(p)),4096)
        bad=wake_frames();bad['15m'][-1][6]=NOW+1
        self.assertEqual(core.assess('TESTUSDT',bad,100.15,1e7,NOW,fixtures.frames())['stage'],'REJECTED')

    def test_new_high_continuation_and_no_prior_pump_required_for_short(self):
        f=core.features(wake_frames(),fixtures.frames(),NOW)
        f.update(leg_gain_pct=20.,retention=1.1,price=125.,leg_peak=120.,return_24h_pct=25.)
        self.assertEqual(core.choose_model(f),('MAJOR_CONTINUATION','LONG'))
        f.update(context_4h='DOWN',down_leg_pct=25.,leg_gain_pct=2.,relative_strength_4h_pp=-5.,return_4h_pct=-5.)
        self.assertEqual(core.choose_model(f),('TREND_CONTINUATION_SHORT','SHORT'))
        f.update(leg_gain_pct=30.,confirmed_hour_lows=[(1,130.)])
        self.assertEqual(core.choose_model(f),('TOP_BREAKDOWN','SHORT'))

    def test_down_context_does_not_turn_every_bounce_into_long(self):
        f=core.features(wake_frames(),fixtures.frames(),NOW);f['context_4h']='DOWN'
        self.assertEqual(core.choose_model(f),(None,None))

    def test_old_90_second_window_replaced_by_next_closed_bar_expiry(self):
        p=core.assess('TESTUSDT',wake_frames(),100.15,1e7,NOW+120000,fixtures.frames())
        self.assertEqual(p['stage'],'READY')
        p=core.assess('TESTUSDT',wake_frames(),100.15,1e7,NOW+900000,fixtures.frames())
        self.assertNotEqual(p['stage'],'READY')

    def test_retest_uses_original_level_and_failed_hold_is_not_retest(self):
        m=wake_frames()['15m']
        m[-3][1:5]=[99.8,100.2,99.7,100.15]
        m[-2][1:5]=[100.15,100.2,100.05,100.12]
        m[-1][1:5]=[100.12,100.18,99.98,100.1]
        event=core.entry_event(m,'LONG',.5)
        self.assertEqual(event['kind'],'RETEST');self.assertEqual(event['trigger'],100.)
        m[-2][4]=99.9
        self.assertEqual(core.entry_event(m,'LONG',.5,100.)['kind'],'BREAK')
        m[-1][4]=99.8
        self.assertIsNone(core.entry_event(m,'LONG',.5,100.))

    def test_short_retest_is_symmetric(self):
        m=wake_frames()['15m']
        m[-3][1:5]=[99.8,99.9,99.3,99.4]
        m[-2][1:5]=[99.4,99.45,99.2,99.3]
        m[-1][1:5]=[99.3,99.51,99.2,99.4]
        event=core.entry_event(m,'SHORT',.5,99.5)
        self.assertEqual(event['kind'],'RETEST');self.assertEqual(event['trigger'],99.5)

    def test_near_obstacle_waits_for_break_but_distant_under_ten_is_not_veto(self):
        with patch.object(core,'pivots',side_effect=[[(1,100.2)],[]]):
            p=ready()
        self.assertEqual((p['stage'],p['reason']),('WATCH','WAIT_OBSTACLE_BREAK'))
        self.assertEqual(p['trigger'],100.2)
        with patch.object(core,'pivots',side_effect=[[(1,105.)],[]]):
            p=ready()
        self.assertEqual(p['stage'],'READY');self.assertLess(p['structural_room_pct'],10.)
        self.assertGreater(p['obstacle_to_risk'],0.)

    def test_wide_stop_and_chase_remain_rejected(self):
        self.assertEqual(core.assess('TESTUSDT',wake_frames(),101.,1e7,NOW,fixtures.frames())['reason'],'LATE_CHASE')
        f=wake_frames();f['15m'][-1][3]=80.
        self.assertEqual(core.assess('TESTUSDT',f,100.15,1e7,NOW,fixtures.frames())['reason'],'PLANNED_STOP_TOO_DISTANT')


class RuntimeV2(unittest.TestCase):
    def test_quote_timestamp_captured_after_concurrent_read_but_stale_future_fail(self):
        conn=sqlite3.connect(':memory:')
        conn.execute('CREATE TABLE futures_last_price(symbol,price,event_ms,received_ms,source)')
        conn.execute('INSERT INTO futures_last_price VALUES(?,?,?,?,?)',('TESTUSDT',100.,1000010,1000020,fixtures.SOURCE))
        self.assertIsNone(store.quote(conn,'TESTUSDT',1000000))
        with patch.object(store.time,'time',return_value=1000.030):self.assertEqual(store.quote(conn,'TESTUSDT'),100.)
        self.assertIsNone(store.quote(conn,'TESTUSDT',1005030));conn.close()

    def test_paper_reference_never_becomes_delivery_and_outcomes_stay_separate(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'LS_STATE_DIR':tmp}):
            c=store.connect();k=sqlite3.connect(':memory:')
            k.execute('CREATE TABLE futures_closed_klines(symbol,interval,open_ms,open,high,low,close,close_ms,source)')
            p=fixtures.Paths().plan();p.update(symbol='TESTUSDT',stage='READY',confirmation_ms=59999)
            self.assertTrue(store.record_research(c,p,'v2'));self.assertFalse(store.record_research(c,p,'v2'))
            self.assertTrue(store.record_research(c,p,'v1'))
            self.assertEqual(c.execute('SELECT COUNT(*) FROM ideas').fetchone()[0],0)
            for i in range(15):
                t=60000+i*60000
                k.execute('INSERT INTO futures_closed_klines VALUES(?,?,?,?,?,?,?,?,?)',('TESTUSDT','1m',t,100.,101.,99.,100.,t+59999,fixtures.SOURCE))
            k.commit();store.measure(c,k,961000,emit=lambda _:None,paper=True)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM research_outcomes').fetchone()[0],2)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM outcomes').fetchone()[0],0)
            c.close();k.close()

    def test_scanner_only_reads_full_frames_on_new_close_and_retries_missing_quotes(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'LS_STATE_DIR':tmp,'TELEGRAM_BOT_TOKEN':'test','TELEGRAM_CHAT_ID':'123'}):
            c=store.connect();k=sqlite3.connect(':memory:')
            k.execute('CREATE TABLE futures_closed_klines(symbol,interval,open_ms,close_ms)')
            k.execute('INSERT INTO futures_closed_klines VALUES(?,?,?,?)',('TESTUSDT','15m',NOW-901001,NOW-1001))
            sc=worker.CandleScanner();context={'flow':{'status':'UNAVAILABLE'},'version':'test','observer_id':'test','captured_ms':NOW,'groups':{},'issues':[]}
            with patch.object(worker,'universe',return_value=[('TESTUSDT',1e7,100.15,1.)]), \
                 patch.object(worker,'frames',side_effect=lambda _,s:fixtures.frames() if s=='BTCUSDT' else wake_frames()) as read, \
                 patch.object(worker,'quote',side_effect=[None,100.15]), \
                 patch.object(worker,'capture_context',return_value=context),patch.object(worker,'record_context'), \
                 patch.object(worker,'publish_candidate',return_value='DELIVERED') as publish, \
                 patch.object(worker.time,'time',return_value=NOW/1000),patch.object(worker.time,'monotonic',return_value=100.):
                sc.run(c,k,'v2');self.assertEqual(sc.latest['TESTUSDT']['reason'],'FRESH_PRICE_MISSING')
                sc.run(c,k,'v2');self.assertEqual(read.call_count,2)
                sc.retry_at['TESTUSDT']=0
                sc.run(c,k,'v2');self.assertEqual(read.call_count,4)
                sc.send_at=0;sc.run(c,k,'v2')
                self.assertEqual(publish.call_count,1);self.assertEqual(read.call_count,4)
                sc.run(c,k,'v2');self.assertEqual(publish.call_count,1)
            c.close();k.close()

    def test_delayed_candidate_rechecks_live_quote_and_acknowledgement(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'LS_STATE_DIR':tmp}):
            c=store.connect();p=ready();pub=Mock(return_value={'status':'ACK'})
            with patch.object(worker,'quote',side_effect=[100.15,100.16]),patch.object(worker.time,'time',return_value=(NOW+120000)/1000):
                self.assertEqual(worker.publish_candidate(c,None,p,'v2',pub,'test','123',NOW+120000),'DELIVERED')
            self.assertEqual(pub.call_count,1)
            stored=json.loads(c.execute('SELECT payload FROM ideas').fetchone()[0]);self.assertEqual(stored['price'],100.16)
            c.close()
