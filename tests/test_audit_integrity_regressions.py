import copy
import contextlib
import io
import os
import json
import math
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import long_short_analyst as analyst
import long_short_live_pool as live
import long_short_delivery_outbox as outbox
from long_short_v3_core import beta_residual_3h
from long_short_strategy_identity import snapshot, reference, event_identity
import test_long_short_live_levels as fixtures


class EntryIntegrityTests(unittest.TestCase):
    def row(self,side='LONG',stage='CLOSE_CONFIRMED'):
        return dict(direction=side,stage=stage,trigger_level=100,
            retest_low=99.8 if side=='LONG' else 100,
            retest_high=100 if side=='LONG' else 100.2,
            invalidation=98 if side=='LONG' else 102,analyst_active=1,
            structure_gate_json='{}',close_confirmed_time=live.now_iso(),
            retest_seen_at_utc='2026-10-15T00:00:20+00:00')

    def quality(self,stamp='2026-10-15T00:04:59.999+00:00',acceptance=2):
        return dict(qualified=True,acceptance_bars_beyond_last3=acceptance,
                    setup_type='BREAKOUT',_closed_candle_time=stamp)

    def test_fast_path_requires_current_price_beyond_level_both_sides(self):
        for side,bad,good,closed in [('LONG',99.9,100.3,100.4),('SHORT',100.1,99.7,99.6)]:
            with self.subTest(side=side):
                row=self.row(side)
                self.assertNotEqual(live.next_stage(row,bad,closed,self.quality()),'TRIGGERED')
                self.assertEqual(live.next_stage(row,good,closed,self.quality()),'TRIGGERED')

    def test_retest_requires_new_closed_candle_both_sides(self):
        for side,price,closed in [('LONG',100.3,100.4),('SHORT',99.7,99.6)]:
            row=self.row(side,'RETESTING')
            for stamp in [None,'2026-10-14T23:59:59.999+00:00',row['retest_seen_at_utc'],'bad']:
                self.assertEqual(live.next_stage(row,price,closed,self.quality(stamp)),'RETESTING')
            self.assertEqual(live.next_stage(row,price,closed,self.quality()),'TRIGGERED')

    def test_rearmed_setup_cannot_reuse_old_confirmation(self):
        row=self.row(stage='WATCH'); row['confirmation_after_utc']='2026-10-15T00:05:00+00:00'
        self.assertEqual(live.next_stage(row,100.3,100.4,self.quality()),'WATCH')
        self.assertEqual(live.next_stage(row,100.3,100.4,self.quality('2026-10-15T00:09:59.999+00:00')),'CLOSE_CONFIRMED')

    def test_short_close_at_low_remains_zero(self):
        row=self.row('SHORT');row['structure_gate_json']=json.dumps({'version':live.STRUCTURE_GATE_VERSION,'_v3_precheck_ok':True})
        early=dict(closed_5m_open=101,closed_5m_high=101,closed_5m_low=99,closed_5m_close=99,
            closed_5m_body_ratio=1,closed_5m_close_location=0,closed_5m_volume_mult=1)
        result=live.live_structure_confirmation(row,99,early)
        self.assertEqual(result['close_location'],0)
        self.assertTrue(result['qualified'])
        self.assertEqual(result['quality_score'],4)

    def test_terminal_new_level_rearms_but_same_snapshot_does_not(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(live,'LIVE_DB',str(Path(tmp)/'live.db')):
            live.init_db(); first=fixtures.LiveLifecycleTests()._item();live.sync_watchlist([first])
            with sqlite3.connect(live.LIVE_DB) as c:c.execute("UPDATE watch_state SET stage='INVALIDATED'")
            live.sync_watchlist([first])
            with sqlite3.connect(live.LIVE_DB) as c:self.assertEqual(c.execute('SELECT stage FROM watch_state').fetchone()[0],'INVALIDATED')
            second=copy.deepcopy(first);second.update(trigger_level=101,scan_time='2026-10-15T00:05:00+00:00')
            live.sync_watchlist([second])
            with sqlite3.connect(live.LIVE_DB) as c:
                stage,trigger,after=c.execute('SELECT stage,trigger_level,confirmation_after_utc FROM watch_state').fetchone()
                self.assertEqual((stage,trigger),('WATCH',101));self.assertIsNotNone(after)
                self.assertEqual(c.execute('SELECT COUNT(*) FROM watch_episodes').fetchone()[0],2)

    def test_same_coin_not_rearmed_while_independent_trade_open(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(live,'LIVE_DB',str(Path(tmp)/'live.db')):
            live.init_db();first=fixtures.LiveLifecycleTests()._item();live.sync_watchlist([first])
            with sqlite3.connect(live.LIVE_DB) as c:
                c.execute("UPDATE watch_state SET stage='TRIGGERED'")
                c.execute("INSERT INTO events(event_time_utc,symbol,direction,stage_to) VALUES(?,'TESTUSDT','LONG','TRIGGERED')",(live.now_iso(),))
            second=copy.deepcopy(first);second.update(trigger_level=101,scan_time=live.now_iso())
            live.sync_watchlist([second])
            with sqlite3.connect(live.LIVE_DB) as c:self.assertEqual(c.execute('SELECT stage FROM watch_state').fetchone()[0],'TRIGGERED')


class NumericIntegrityTests(unittest.TestCase):
    def test_rsi_flat_rising_falling(self):
        self.assertEqual(analyst.rsi([100.]*30),50)
        self.assertEqual(analyst.rsi(list(range(1,31))),100)
        self.assertEqual(analyst.rsi(list(range(30,0,-1))),0)

    def series(self,shift=0):
        return {'raw':[[i*3600000+shift] for i in range(30)],
                'close':[100+i*.1+math.sin(i)*.2 for i in range(30)]}

    def test_beta_aligned_identical_returns(self):
        r=beta_residual_3h(self.series(),self.series())
        self.assertTrue(r['available']);self.assertAlmostEqual(r['beta'],1)
        self.assertAlmostEqual(r['residual_3h_pct'],0)

    def test_beta_shift_or_missing_timestamps_cannot_generate_bonus(self):
        for btc in [self.series(3600000),{'close':[100]*30},self.series(1)]:
            r=beta_residual_3h(self.series(),btc)
            self.assertFalse(r['available']);self.assertEqual(r['residual_3h_pct'],0)

    def test_beta_does_not_bridge_a_recent_hour_gap(self):
        btc=self.series();btc['raw'].pop(-5);btc['close'].pop(-5)
        self.assertFalse(beta_residual_3h(self.series(),btc)['available'])

    def test_effective_override_changes_identity_and_plan_cohort(self):
        before=reference()
        with patch.object(live,'EXECUTION_FEE_BPS_PER_SIDE',9):self.assertNotEqual(before['id'],reference()['id'])
        self.assertEqual(before['id'],reference()['id'])
        self.assertNotEqual(event_identity({})['cohort_id'],event_identity({'_strategy_identity':before})['cohort_id'])
        self.assertNotIn('TELEGRAM_BOT_TOKEN',json.dumps(snapshot()))


class DeliveryIntegrityTests(unittest.TestCase):
    at='2026-10-15T00:05:20+00:00'
    candle='2026-10-15T00:04:59.999+00:00'

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=str(Path(self.tmp.name)/'live.db')
        with patch.object(live,'LIVE_DB',self.path):live.init_db()
        self.c=sqlite3.connect(self.path);self.c.row_factory=sqlite3.Row

    def tearDown(self):
        self.c.close();self.tmp.cleanup()

    def queue(self):
        values=(self.at,'TESTUSDT','LONG','CLOSE_CONFIRMED','TRIGGERED',100,100,
                self.at,None,'PENDING',None,None,'{}')
        return outbox.enqueue(self.c,values,'signal',self.candle)

    def test_event_and_outbox_roll_back_together(self):
        self.queue();self.c.rollback()
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM events').fetchone()[0],0)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM core_delivery_outbox').fetchone()[0],0)

    def test_recovered_pending_event_sends_once_after_validation(self):
        eid=self.queue();self.c.commit();self.c.close();self.c=sqlite3.connect(self.path)
        sent=[];send=lambda msg:sent.append(msg) or self.at
        for _ in range(2):outbox.dispatch(self.c,send,lambda event:{'qualified':True},at=self.at)
        self.assertEqual(sent,['signal'])
        self.assertEqual(self.c.execute('SELECT telegram_status FROM events WHERE id=?',(eid,)).fetchone()[0],'SENT')

    def test_expired_or_invalidated_signal_is_not_delivered(self):
        self.queue();self.c.commit()
        outbox.dispatch(self.c,lambda msg:self.fail('stale message sent'),lambda ev:{'qualified':True},at='2026-10-15T00:10:00+00:00')
        self.assertEqual(self.c.execute('SELECT telegram_status FROM events').fetchone()[0],'SUPPRESSED')
        self.queue();self.c.commit()
        outbox.dispatch(self.c,lambda msg:self.fail('invalid message sent'),lambda ev:{'qualified':False,'reason':'BROKEN'},at=self.at)
        self.assertEqual(self.c.execute('SELECT telegram_status FROM events ORDER BY id DESC').fetchone()[0],'SUPPRESSED')

    def test_ambiguous_network_send_is_not_replayed(self):
        self.queue();self.c.commit();calls=[]
        def send(msg):calls.append(msg);raise TimeoutError('unknown response')
        for _ in range(2):outbox.dispatch(self.c,send,lambda ev:{'qualified':True},at=self.at)
        self.assertEqual(len(calls),1)
        self.assertEqual(self.c.execute('SELECT telegram_status FROM events').fetchone()[0],'UNKNOWN')

    def test_crash_after_claim_preserves_event_and_does_not_duplicate(self):
        self.queue();self.c.commit()
        def crash(msg):raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):outbox.dispatch(self.c,crash,lambda ev:{'qualified':True},at=self.at)
        outbox.dispatch(self.c,lambda msg:self.fail('ambiguous send replayed'),lambda ev:{'qualified':True},at='2026-10-15T00:10:00+00:00')
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM events').fetchone()[0],1)
        self.assertEqual(self.c.execute('SELECT telegram_status FROM events').fetchone()[0],'UNKNOWN')

    def test_slow_revalidation_checks_clock_again(self):
        self.queue();self.c.commit()
        with patch.object(outbox,'now_iso',side_effect=[self.at,'2026-10-15T00:10:00+00:00']):
            outbox.dispatch(self.c,lambda msg:self.fail('late message sent'),lambda ev:{'qualified':True})
        self.assertEqual(self.c.execute('SELECT telegram_status FROM events').fetchone()[0],'SUPPRESSED')


class CoreLoopIntegrationTests(unittest.TestCase):
    def run_case(self,break_record=False):
        with tempfile.TemporaryDirectory() as tmp,contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ,{'LS_NATIVE_CHAIN_V2':'1','LS_BINANCE_FUTURES_ONLY':'1'}))
            stack.enter_context(patch.object(live,'LIVE_DB',str(Path(tmp)/'live.db')))
            stack.enter_context(patch.object(live,'ANALYST_DB',str(Path(tmp)/'analyst.db')))
            stack.enter_context(patch.object(live,'TELEGRAM_TRADE_ONLY',True))
            output=io.StringIO();stack.enter_context(contextlib.redirect_stdout(output))
            live.init_db()
            item=fixtures.LiveLifecycleTests()._item();item['target1']=104
            item['structure_gate']['_strategy_identity']=reference()
            live.sync_watchlist([item])
            stamp=(datetime.now(timezone.utc)-timedelta(seconds=10)).isoformat()
            with sqlite3.connect(live.LIVE_DB) as c:
                c.execute("UPDATE watch_state SET stage='CLOSE_CONFIRMED',close_confirmed_time=?",(stamp,))
            early=dict(closed_5m_open=100.1,closed_5m_high=100.6,closed_5m_low=100,
                closed_5m_close=100.5,closed_5m_body_ratio=.7,closed_5m_close_location=.84,
                closed_5m_volume_mult=1.5,recent_closed_5m_closes=[100.2,100.3,100.5],
                live_price_source='BINANCE_FUTURES',rolling_24h_stats_verified=True,
                compression_pct=2,ema7_slope_pct=0,vol_mult=1,taker_buy_share=.5,
                atr1=.1,local_high=101,local_low=99,short_range_pct=2,
                quote_volume_24h=50000000,day_change_pct=0)
            proxy=dict(available=True,source='BINANCE_FUTURES_BOOK',notional_usdt=250,
                costs={'250':{'buy_bps':1,'sell_bps':1}})
            stack.enter_context(patch.object(live,'market_snapshot',return_value=(100.4,100.5,stamp,early)))
            stack.enter_context(patch.object(live,'binance_four_factor_confirmation_gate',return_value={'qualified':True,'tier':'FULL_4_OF_4'}))
            stack.enter_context(patch.object(live,'trigger_execution_proxy',return_value=proxy))
            stack.enter_context(patch.object(live,'resolve_confirmed_trade_outcomes'))
            calls=[]
            def send(msg,attempts=3):
                with sqlite3.connect(live.LIVE_DB) as c:
                    self.assertEqual(c.execute('SELECT stage FROM watch_state').fetchone()[0],'TRIGGERED')
                    self.assertEqual(c.execute("SELECT COUNT(*) FROM events WHERE stage_to='TRIGGERED'").fetchone()[0],1)
                self.assertEqual(attempts,1);calls.append(msg);return live.now_iso()
            stack.enter_context(patch.object(live,'send_telegram',side_effect=send))
            if break_record:
                stack.enter_context(patch.object(live,'enqueue_core_event',side_effect=sqlite3.OperationalError('simulated disk failure')))
            live.loop_once();live.loop_once()
            with sqlite3.connect(live.LIVE_DB) as c:
                stage=c.execute('SELECT stage FROM watch_state').fetchone()[0]
                records=c.execute("SELECT telegram_status,payload_json FROM events WHERE stage_to='TRIGGERED'").fetchall()
            if break_record:
                self.assertEqual(stage,'CLOSE_CONFIRMED');self.assertEqual(records,[]);self.assertEqual(calls,[])
            else:
                self.assertEqual(stage,'TRIGGERED',output.getvalue());self.assertEqual(len(calls),1)
                self.assertEqual(len(records),1);self.assertEqual(records[0][0],'SENT')
                self.assertTrue(json.loads(records[0][1])['_strategy_identity']['cohort_id'])

    def test_core_loop_commits_event_before_one_send(self):self.run_case()
    def test_core_loop_rolls_back_state_on_event_storage_failure(self):self.run_case(True)


if __name__=='__main__':unittest.main()
