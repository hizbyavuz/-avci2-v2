import copy
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import long_short_major_move as core
import long_short_major_move_store as store
import long_short_major_move_worker as worker

NOW=1792170001000
SOURCE='BINANCE_UM_FUTURES_WEBSOCKET'


def bars(period,count,price=100):
    end=(NOW-1000)//period*period
    return [[end-(count-i)*period,price,price+.5,price-.5,price,100.,end-(count-i-1)*period-1] for i in range(count)]


def frames():
    return {'15m':bars(900000,120),'1h':bars(3600000,120),'4h':bars(14400000,220)}


def base_features():
    return dict(price=101.,return_4h_pct=4.,return_24h_pct=4.,btc_return_4h_pct=0.,relative_strength_4h_pp=4.,
        volume_1h_multiple=2.,compression_ratio=.5,box_width_pct=2.,leg_base=95.,leg_peak=102.,leg_gain_pct=7.37,
        peak_ms=NOW-7200000,retention=.8,drawdown_from_peak_pct=-1.,range_7d_position=.6,atr1h=2.,atr15=1.,
        prior_day_high=100.,box_high=100.,box_low=97.,confirmed_hour_highs=[(1,99.),(2,102.)],
        confirmed_hour_lows=[(1,95.),(2,97.)])


def ready_frames():
    f=frames();m=f['15m']
    for r in m[-9:-1]:r[2]=100.;r[4]=99.8;r[3]=99.5;r[1]=99.8
    m[-1][1:5]=[99.8,100.5,99.7,100.3]
    return f


class Decisions(unittest.TestCase):
    def test_complete_ohlc_pipeline_without_mocked_features(self):
        f=frames();b=frames()
        for r in f['1h'][-29:-5]:r[2:4]=[101.,99.]
        for r in f['1h'][-5:-1]:r[2:4]=[100.1,99.9]
        f['1h'][-1][1:6]=[100.,103.3,99.8,103.1,200.]
        f['15m'][-5][2]=103.
        f['15m'][-1][1:6]=[100.,103.3,99.8,103.1,150.]
        p=core.assess('TESTUSDT',f,103.1,10000000,NOW,b)
        self.assertEqual((p['stage'],p.get('model')),('READY','WAKE_UP'))
        self.assertEqual(p['horizon_hours'],72)
        f['1h'][-1][5]=50.
        self.assertEqual(core.assess('TESTUSDT',f,103.1,10000000,NOW,b)['stage'],'REJECTED')

    def assess(self,f=None,features=None,price=100.3):
        f=f or ready_frames()
        with patch.object(core,'features',return_value=features or base_features()):
            return core.assess('TESTUSDT',f,price,10000000,NOW,frames())

    def test_large_move_wakeup_and_explicit_unproven_objective(self):
        p=self.assess();self.assertEqual(p['model'],'WAKE_UP');self.assertEqual(p['stage'],'READY')
        self.assertAlmostEqual(p['objective']/p['price'],1.1)
        self.assertIsNone(p['obstacle'])
        self.assertIn('tahmini değildir',core.message(p));self.assertTrue(core.message(p).startswith('🟢 LONG'))
        self.assertLess(len(core.message(p)),4096)

    def test_small_local_breakout_without_major_evidence_is_rejected(self):
        f=base_features();f.update(volume_1h_multiple=.6,relative_strength_4h_pp=.2)
        self.assertEqual(self.assess(features=f)['reason'],'NO_MAJOR_MOVE_STRUCTURE')

    def test_large_prior_gain_is_not_automatic_short(self):
        f=base_features();f.update(leg_gain_pct=80.,volume_1h_multiple=.5,relative_strength_4h_pp=-5.,retention=.8)
        self.assertEqual(self.assess(features=f)['reason'],'NO_MAJOR_MOVE_STRUCTURE')

    def test_short_requires_lower_high_and_broken_hourly_structure(self):
        f=base_features();f.update(price=99.,leg_gain_pct=50.,volume_1h_multiple=.5,relative_strength_4h_pp=-4.,
            confirmed_hour_highs=[(1,105.),(2,103.)],confirmed_hour_lows=[(1,97.),(2,100.)])
        m=ready_frames();m['15m'][-1][1:5]=[100.,100.,99.,99.2]
        p=self.assess(m,f,99.2)
        self.assertEqual(p['model'],'TOP_BREAKDOWN');self.assertEqual(p['direction'],'SHORT')
        f['price']=100.1
        self.assertEqual(self.assess(m,f,99.2)['reason'],'NO_MAJOR_MOVE_STRUCTURE')

    def test_continuation_needs_retention_and_higher_low(self):
        f=base_features();f.update(leg_gain_pct=30.,leg_peak=120.,price=105.,volume_1h_multiple=.7,retention=.7,peak_ms=NOW-20000000)
        p=self.assess(features=f);self.assertEqual(p['model'],'MAJOR_CONTINUATION')
        f['retention']=.3
        self.assertEqual(self.assess(features=f)['reason'],'NO_MAJOR_MOVE_STRUCTURE')

    def test_micro_room_and_wide_stop_and_late_chase_reject(self):
        with patch.object(core,'pivots',return_value=[(1,105.)]):
            self.assertEqual(self.assess()['reason'],'MAJOR_ROOM_BLOCKED')
        f=base_features();f['box_low']=80.
        self.assertEqual(self.assess(features=f)['reason'],'STRUCTURAL_STOP_TOO_DISTANT')
        self.assertEqual(self.assess(price=102.)['reason'],'LATE_CHASE')

    def test_old_confirmation_only_watch(self):
        f=ready_frames();f['15m'][-1][6]-=120000
        self.assertEqual(self.assess(f)['stage'],'WATCH')

    def test_complete_closed_history_validation_and_no_future_pivots(self):
        f=frames();core.validate(f,NOW)
        bad=copy.deepcopy(f);bad['1h'][-1][6]=NOW+1
        with self.assertRaises(ValueError):core.validate(bad,NOW)
        bad=copy.deepcopy(f);bad['4h'].pop(-5)
        with self.assertRaises(ValueError):core.validate(bad,NOW)
        r=bars(3600000,20);r[-1][2]=1000
        self.assertNotIn((r[-1][0],1000),core.pivots(r))

    def test_feature_volume_and_btc_same_timestamp(self):
        f=frames();b=frames();f['1h'][-1][5]=300.
        self.assertAlmostEqual(core.features(f,b,NOW)['volume_1h_multiple'],3.)
        b['1h']=[r for r in b['1h'][:-1]]
        with self.assertRaisesRegex(ValueError,'BTC_NOT_TIME_ALIGNED'):core.features(f,b,NOW)


class Paths(unittest.TestCase):
    def plan(self,observed=60000,side='LONG'):
        return dict(price=100.,observed_ms=observed,direction=side,stop=95. if side=='LONG' else 105.,
                    features={'atr1h':2.},objective=110. if side=='LONG' else 90.,expires_ms=observed+6*3600000)

    def bar(self,t,lo=99.,hi=101.,cl=100.,op=100.):return [t,op,hi,lo,cl,t+59999,SOURCE]

    def test_stop_then_large_rally_keeps_loss_and_post_stop_reach(self):
        p=self.plan();g=store.initial_progress(p)
        g,_=store.advance_path(p,g,[self.bar(60000,94.,101.,96.),self.bar(120000,96.,121.,120.,96.)])
        self.assertEqual(g['plan_exit']['kind'],'STOP')
        self.assertAlmostEqual(g['plan_exit']['net_return_pct'],-5.3)
        self.assertIn('20',g['milestones']);self.assertEqual(g['first_barrier']['kind'],'STOP')

    def test_partial_minute_never_fabricates_winning_or_losing_exit(self):
        p=self.plan(65000);g=store.initial_progress(p)
        g,_=store.advance_path(p,g,[self.bar(60000,94.,112.,100.)])
        self.assertTrue(g['boundary_ambiguous']);self.assertIsNone(g['plan_exit'])
        self.assertNotIn('10',g['milestones'])

    def test_gap_pauses_then_repair_advances_same_cursor(self):
        p=self.plan();g=store.initial_progress(p)
        g,_=store.advance_path(p,g,[self.bar(120000)])
        self.assertEqual(g['cursor_ms'],60000);self.assertIsNotNone(g['missing_since_ms'])
        g,_=store.advance_path(p,g,[self.bar(60000),self.bar(120000)])
        self.assertEqual(g['cursor_ms'],180000);self.assertIsNone(g['missing_since_ms'])

    def test_stop_gap_and_same_minute_are_pessimistic(self):
        p=self.plan();g,_=store.advance_path(p,store.initial_progress(p),[self.bar(60000,89.,112.,100.,90.)])
        self.assertEqual(g['plan_exit']['kind'],'STOP');self.assertAlmostEqual(g['plan_exit']['net_return_pct'],-10.3)
        self.assertTrue(g['first_barrier']['same_minute_ambiguous'])

    def test_horizon_snapshot_is_not_contaminated_by_later_rally(self):
        p=self.plan();g=store.initial_progress(p)
        rows=[self.bar(60000+i*60000) for i in range(360)]
        rows.append(self.bar(60000+360*60000,99.,150.,145.))
        g,out=store.advance_path(p,g,rows)
        self.assertEqual([o['horizon_hours'] for o in out],[.25,.5,1,3,6])
        self.assertLess(out[-1]['max_favorable_pct'],2);self.assertGreater(g['max_favorable_pct'],40)

    def test_short_path_mirrors_long_and_never_counts_pre_signal_gain(self):
        p=self.plan(side='SHORT');p['gain_before_signal_pct']=90
        g,_=store.advance_path(p,store.initial_progress(p),[self.bar(60000,79.,101.,80.)])
        self.assertIn('20',g['milestones']);self.assertNotIn('90',g['milestones'])
        self.assertIsNone(g['plan_exit']);self.assertTrue(g['trail_armed'])

    def test_ten_percent_is_not_an_exit_and_trail_applies_next_minute(self):
        p=self.plan(observed=3600000);g=store.initial_progress(p)
        rows=[self.bar(3600000+i*60000,99.,111.,110.) for i in range(60)]
        g,_=store.advance_path(p,g,rows)
        self.assertIsNone(g['plan_exit']);self.assertAlmostEqual(g['active_stop'],98.7)
        g,_=store.advance_path(p,g,[self.bar(7200000,98.,115.,113.)])
        self.assertEqual(g['plan_exit']['kind'],'STOP')
        self.assertAlmostEqual(g['plan_exit']['net_return_pct'],-1.6)

    def test_trail_never_loosens_and_partial_hour_does_not_tighten(self):
        p=self.plan(observed=3660000);g=store.initial_progress(p)
        rows=[self.bar(3660000+i*60000,99.,111.,110.) for i in range(59)]
        g,_=store.advance_path(p,g,rows);self.assertEqual(g['active_stop'],95.)
        rows=[self.bar(7200000+i*60000,105.,121.,120.,110.) for i in range(60)]
        g,_=store.advance_path(p,g,rows);self.assertAlmostEqual(g['active_stop'],104.7)



class StorageAndRuntime(unittest.TestCase):
    def test_stalled_database_bounds_buffer_without_dropping_or_receiving(self):
        from long_short_futures_kline_shadow import FuturesKlineStream,MAX_PENDING_CANDLES
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as tmp:
            ws=Mock();stream=FuturesKlineStream(str(Path(tmp)/'feed.db'),ws_module=ws)
            stream.buffer=[('retained',)]*MAX_PENDING_CANDLES
            stop=Mock();stop.is_set.side_effect=[False,True]
            with patch.object(stream,'flush',return_value=False):stream.run(lambda:['BTCUSDT'],stop)
            ws.create_connection.assert_not_called()
            self.assertEqual(len(stream.buffer),MAX_PENDING_CANDLES)

    def test_publish_ack_price_is_fresh_and_restart_never_replays(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'LS_STATE_DIR':tmp}):
            c=store.connect();p=Decisions().assess()
            with patch.object(worker,'quote',side_effect=[100.3,100.4]),patch.object(worker.time,'time',return_value=NOW/1000):
                calls=[]
                def publisher(*args,**kw):calls.append((args,kw));return {'status':'ACK'}
                self.assertEqual(worker.publish_candidate(c,None,p,'sid',publisher,'test','123',NOW),'DELIVERED')
            row=c.execute('SELECT * FROM ideas').fetchone();saved=json.loads(row['payload'])
            self.assertEqual(saved['price'],100.4);self.assertEqual(saved['notice_price'],100.3)
            self.assertEqual(worker.publish_candidate(c,None,p,'sid',publisher,'test','123',NOW),'COOLDOWN')
            self.assertEqual(len(calls),1)
            c.execute("UPDATE ideas SET state='PUBLISH_PENDING',delivered_ms=NULL");c.commit()
            worker.recover_interrupted(c)
            self.assertEqual(c.execute('SELECT state FROM ideas').fetchone()[0],'DELIVERY_UNCERTAIN');c.close()

    def test_terminal_notice_is_same_episode_and_old_episode_not_resent(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'LS_STATE_DIR':tmp,'TELEGRAM_BOT_TOKEN':'test','TELEGRAM_CHAT_ID':'123'}):
            c=store.connect();p=Decisions().assess();g=store.initial_progress(p)
            g['plan_exit']={'kind':'STOP','net_return_pct':-3.}
            for eid,created in [('old',NOW-1000),('new',NOW)]:
                c.execute('INSERT INTO ideas VALUES(?,?,?,?,?,?,?,?)',(eid,p['symbol'],created,created,'TRACKING',json.dumps(p),json.dumps(g),created))
            c.commit();calls=[]
            def publisher(*args,**kw):calls.append((args,kw));return {'status':'ACK'}
            worker.notify_exits(c,publisher);worker.notify_exits(c,publisher)
            self.assertEqual(len(calls),1);self.assertEqual(calls[0][1]['episode_key'],'new')
            self.assertIn('PLAN KAPANDI',calls[0][0][4]);c.close()

    def test_single_new_engine_and_fewer_workers(self):
        import long_short_runtime_profile as profile
        self.assertEqual(len(profile.MAJOR_WORKERS),5)
        names={x[0] for x in profile.MAJOR_WORKERS}
        self.assertIn('major_move',names)
        self.assertFalse(names.intersection({'chart_first','chart_delivery_outcomes','binance_taker_shadow'}))

    def test_no_network_or_orders_in_dry_scan_and_cooldown(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'LS_STATE_DIR':tmp}):
            c=store.connect();p={'symbol':'TESTUSDT'}
            c.execute('INSERT INTO ideas VALUES(?,?,?,?,?,?,?,?)',('a','TESTUSDT',NOW,None,'DELIVERY_UNCERTAIN','{}','{}',NOW));c.commit()
            self.assertEqual(worker.eligible_to_publish(c,p,NOW+1000),'COOLDOWN')
            c.close()

    def test_universe_fails_closed_and_demand_retains_tracking(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'LS_STATE_DIR':tmp}):
            native=sqlite3.connect(Path(tmp)/'native_derivatives_v2.db')
            native.execute('CREATE TABLE markets(id INTEGER,observed_ms INTEGER,payload TEXT)')
            native.execute('INSERT INTO markets VALUES(1,?,?)',(NOW,json.dumps(['GOODUSDT','BTCUSDT'])));native.commit();native.close()
            c=sqlite3.connect(Path(tmp)/'binance_futures_universe.db')
            c.execute('CREATE TABLE futures_tickers(symbol TEXT,quote_volume REAL,last_price REAL,change_pct REAL,received_ms INTEGER,source TEXT)')
            c.executemany('INSERT INTO futures_tickers VALUES(?,?,?,?,?,?)',[(s,10000000,100,10,NOW,SOURCE) for s in ['GOODUSDT','BADUSDT','BTCUSDT']]);c.commit();c.close()
            self.assertEqual({r[0] for r in store.universe(NOW)},{'GOODUSDT','BTCUSDT'})
            with self.assertRaises(ValueError):store.universe(NOW+3600001)

    def test_measure_replaces_repaired_data_pending_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'LS_STATE_DIR':tmp}):
            c=store.connect();k=sqlite3.connect(':memory:')
            k.execute('CREATE TABLE futures_closed_klines(symbol,interval,open_ms,open,high,low,close,close_ms,source)')
            p=Paths().plan();p['symbol']='TESTUSDT';g=store.initial_progress(p)
            g['cursor_ms']=60000+359*60000;g['evaluated_ms']=g['cursor_ms']-1
            c.execute('INSERT INTO ideas VALUES(?,?,?,?,?,?,?,?)',('id','TESTUSDT',60000,60000,'DATA_PENDING',json.dumps(p),json.dumps(g),0))
            c.execute('INSERT INTO outcomes VALUES(?,?,?,?)',('id',6,'DATA_PENDING','{}'));c.commit()
            t=g['cursor_ms'];k.execute('INSERT INTO futures_closed_klines VALUES(?,?,?,?,?,?,?,?,?)',('TESTUSDT','1m',t,100.,101.,99.,100.,t+59999,SOURCE));k.commit()
            store.measure(c,k,t+61000,emit=lambda _:None);store.measure(c,k,t+62000,emit=lambda _:None)
            self.assertEqual(c.execute('SELECT state FROM outcomes WHERE idea_id=? AND horizon_hours=6',('id',)).fetchone()[0],'RESOLVED')
            self.assertEqual(c.execute('SELECT COUNT(*) FROM outcomes').fetchone()[0],1);c.close();k.close()


if __name__=='__main__':unittest.main()
