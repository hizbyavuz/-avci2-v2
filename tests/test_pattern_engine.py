import copy
import json
import math
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

import long_short_patterns as core
import long_short_pattern_outcomes as outcome
import long_short_pattern_runtime as runtime

M, H = core.MINUTE, core.HOUR
START = 1_788_220_800_000


def candle(t, op=100., hi=101., lo=99., close=100., step=M):
    return dict(open_ms=t, close_ms=t+step-1, open=op, high=hi, low=lo, close=close,
                quote_volume=100_000., taker_buy_quote=50_000.)


def triangle():
    rows = [candle(START+i*H, step=H) for i in range(160)]
    for i in range(127, 159):
        floor = 100+(i-127)*.24
        phase = (i-127) % 8
        weight = abs(phase-4)/4
        price = floor+(109.8-floor)*weight
        rows[i].update(open=price, close=price, high=price+.2, low=price-.2)
    rows[-1].update(open=109.7, close=110.15, high=110.25, low=109.6)
    return rows


def context(now):
    end = now//(4*H)*(4*H)
    return [candle(end-(40-i)*4*H, op=80, hi=81, lo=79, close=80, step=4*H) for i in range(40)]


def plan(side='LONG'):
    return dict(version=core.VERSION, config_hash=core.CONFIG_HASH, id='episode-'+side, symbol='TESTUSDT',
                stage='READY', research_only=True, direction=side, interval='1h', model='RANGE_BREAK',
                entry=100., trigger=99.5 if side == 'LONG' else 100.5, atr=2.,
                stop=97. if side == 'LONG' else 103., target=106. if side == 'LONG' else 94.,
                target_source='HISTORICAL_PIVOT', confirmation_ms=START-1, expires_ms=START+15*M,
                context_4h='RANGE', max_hold_hours=72, geometry=core.geometry(side, 100, 97 if side == 'LONG' else 103, 106 if side == 'LONG' else 94))


class GeometryTests(unittest.TestCase):
    def test_direction_correct_stop_target_and_costs(self):
        for side, stop, target in [('LONG', 97, 106), ('SHORT', 103, 94)]:
            g = core.geometry(side, 100, stop, target)
            self.assertTrue(g['valid'])
            self.assertAlmostEqual(g['rr_after_assumed_cost'], 5.7/3.3)
            self.assertFalse(core.geometry(side, 100, target, stop)['valid'])
        for x in (float('nan'), float('inf'), 0, -1):
            self.assertFalse(core.geometry('LONG', x, 97, 106)['valid'])

    def test_pivot_requires_two_closed_future_confirmers(self):
        rows = [candle(START+i*H, hi=105 if i == 5 else 101, step=H) for i in range(10)]
        self.assertFalse(any(p['price'] == 105 for p in core.pivots(rows[:7])))
        pivot = next(p for p in core.pivots(rows[:8]) if p['price'] == 105)
        self.assertEqual(pivot['known_ms'], rows[7]['close_ms'])

    def test_fibonacci_chronological_and_short_retracement(self):
        rows = [candle(START+i*H, step=H) for i in range(20)]
        rows[4]['low'] = 90.; rows[10]['high'] = 110.; rows[15]['low'] = 95.
        long = core.fibonacci(rows[:14], 'LONG')
        self.assertEqual(long['start']['price'], 90)
        self.assertEqual(long['end']['price'], 110)
        self.assertEqual(long['retracements']['0.5'], 100)
        self.assertAlmostEqual(long['projections']['1.618'], 122.36)
        short = core.fibonacci(rows, 'SHORT')
        self.assertEqual(short['retracements']['0.5'], 102.5)
        self.assertLess(short['projections']['1.618'], 95)
        self.assertLess(short['start']['open_ms'], short['end']['open_ms'])

    def test_broken_obstacle_does_not_reappear(self):
        rows = [candle(START+i*H, step=H) for i in range(20)]
        rows[4]['high'] = 105.
        self.assertIn(105., core.unbroken_levels(rows, 'LONG', 100))
        rows[12].update(open=100, high=107, close=106)
        self.assertNotIn(105., core.unbroken_levels(rows, 'LONG', 100))

    def test_future_gap_stale_and_invalid_ohlc_rejected(self):
        rows = triangle(); now = rows[-1]['close_ms']+1
        for mutation in ('future', 'gap', 'ohlc', 'received'):
            bad = copy.deepcopy(rows)
            if mutation == 'future': now2 = now-H
            else: now2 = now
            if mutation == 'gap': del bad[50]
            if mutation == 'ohlc': bad[50]['low'] = bad[50]['high']+1
            if mutation == 'received': bad[50]['received_ms'] = now+1
            with self.assertRaises(ValueError): core.assess('TUSDT', '1h', bad, context(now2), now2)
        with self.assertRaises(ValueError): core.assess('TUSDT', '1h', rows, context(now+2*H), now+2*H)

    def test_pattern_detected_and_volume_cannot_vote(self):
        rows = triangle(); now = rows[-1]['close_ms']+1
        a = core.assess('TUSDT', '1h', rows, context(now), now)
        self.assertTrue(a, 'Fixture must exercise a real detected pattern')
        self.assertTrue(any(p['model'] == 'ASCENDING_TRIANGLE' and p['direction'] == 'LONG' for p in a), a)
        bad = copy.deepcopy(rows)
        for b in bad: b.update(quote_volume=None, taker_buy_quote=None)
        b = core.assess('TUSDT', '1h', bad, context(now), now)
        fields = lambda xs: [(p['id'], p['stage'], p['direction'], p['entry'], p['stop'], p['target']) for p in xs]
        self.assertEqual(fields(a), fields(b))

    def test_mirror_pattern_and_no_repeated_rolling_break(self):
        rows = triangle(); now = rows[-1]['close_ms']+1
        reflected = [dict(b, open=220-b['open'], high=220-b['low'], low=220-b['high'], close=220-b['close']) for b in rows]
        mirrored_context = [dict(b, open=220-b['open'], high=220-b['low'], low=220-b['high'], close=220-b['close']) for b in context(now)]
        ps = core.assess('TUSDT', '1h', reflected, mirrored_context, now)
        self.assertTrue(any(p['direction'] == 'SHORT' and p['model'] == 'DESCENDING_TRIANGLE' for p in ps), ps)
        original = core.assess('TUSDT', '1h', rows, context(now), now)
        rows.append(candle(now, op=110.2, hi=110.5, lo=110, close=110.4, step=H))
        subsequent = core.assess('TUSDT', '1h', rows, context(now+H), now+H)
        self.assertFalse({p['id'] for p in original} & {p['id'] for p in subsequent})

    def test_live_price_recheck_rejects_chase_and_lost_break(self):
        p = plan()
        self.assertTrue(core.executable(p, 100)[0])
        self.assertFalse(core.executable(p, 102)[0])
        self.assertFalse(core.executable(p, 99)[0])
        self.assertFalse(core.executable(p, 106)[0])

    def test_retest_needs_touch_hold_then_closed_rejection_confirmation(self):
        rows = triangle(); t = rows[-1]['close_ms']+1
        rows += [candle(t, op=110.1, hi=110.2, lo=109.95, close=110.08, step=H),
                 candle(t+H, op=110.1, hi=110.55, lo=110.05, close=110.4, step=H)]
        now = rows[-1]['close_ms']+1
        ps = core.assess('TUSDT', '1h', rows, context(now), now)
        rs = [p for p in ps if p['entry_style'] == 'RETEST']
        self.assertTrue(rs, ps)
        self.assertTrue(any(p['stage'] == 'READY' for p in rs), rs)
        self.assertTrue(all(p['origin_confirmation_ms'] < p['confirmation_ms'] for p in rs))
        for mutation in ('lost_level', 'no_confirmation', 'no_touch'):
            bad = copy.deepcopy(rows)
            if mutation == 'lost_level': bad[-2]['close'] = 109.96
            if mutation == 'no_confirmation': bad[-1]['close'] = 110.15
            if mutation == 'no_touch': bad[-2].update(open=111, high=111.3, low=110.9, close=111)
            self.assertFalse(any(p['entry_style'] == 'RETEST' for p in core.assess('TUSDT', '1h', bad, context(now), now)))

    def test_all_line_families_have_actual_detectors(self):
        cases = [('RANGE_BREAK',0,0,0), ('ASCENDING_TRIANGLE',0,.22,0),
                 ('DESCENDING_TRIANGLE',-.22,0,0), ('SYMMETRIC_TRIANGLE',-.1,.1,0),
                 ('FALLING_WEDGE',-.3,-.1,0), ('RISING_WEDGE',.1,.3,0),
                 ('FLAG',-.04,-.04,35), ('PENNANT',-.1,.1,35)]
        for model, up, low, pole in cases:
            rows = []
            for i in range(160):
                top, bottom = 110+up*(i-127), 100+low*(i-127)
                if i < 127:
                    price = 100-pole if i < 119 else 100-pole+(i-119)*pole/8
                else:
                    weight = abs((i-127)%8-4)/4
                    price = bottom+(top-bottom)*weight
                rows.append(candle(START+i*H, op=price, hi=price+.2, lo=price-.2, close=price, step=H))
            found = {p['model'] for p in core._shape_candidates(rows, core.atr(rows[:-1]))}
            self.assertIn(model, found)


class OutcomeTests(unittest.TestCase):
    def test_stop_before_target_and_post_stop_rally_separate(self):
        p = plan(); s = outcome.initial(p, START, 100)
        s = outcome.advance(p, s, [candle(START, hi=110, lo=96), candle(START+M, hi=150, lo=98)])
        r = outcome.result(p, s)
        self.assertEqual(r['exit']['kind'], 'SL')
        self.assertTrue(r['exit']['same_minute_both'])
        self.assertAlmostEqual(r['fee_slippage_only_pct'], -3.3)
        self.assertEqual(r['raw_mfe_pct'], 50)
        self.assertEqual(r['plan_mfe_lower_pct'], 0)
        self.assertIsNone(r['full_net_pct'])
        self.assertNotIn('10', r['plan_reach'])

    def test_target_profit_capped_and_gap_opens_respected(self):
        for side in ('LONG', 'SHORT'):
            p = plan(side); s = outcome.initial(p, START, 100)
            bar = candle(START, hi=120, lo=99) if side == 'LONG' else candle(START, hi=101, lo=80)
            r = outcome.result(p, outcome.advance(p, s, [bar]))
            self.assertEqual(r['exit']['kind'], 'TP')
            self.assertAlmostEqual(r['fee_slippage_only_pct'], 5.7)
            gap = candle(START, op=90, hi=91, lo=89, close=90) if side == 'LONG' else candle(START, op=110, hi=111, lo=109, close=110)
            r = outcome.result(p, outcome.advance(p, s, [gap]))
            self.assertAlmostEqual(r['fee_slippage_only_pct'], -10.3)

    def test_gap_pauses_and_does_not_skip(self):
        p = plan(); s = outcome.initial(p, START, 100)
        g = outcome.advance(p, s, [candle(START+M)])
        self.assertEqual(g['cursor_ms'], START)
        self.assertEqual(g['gap_ms'], START)
        self.assertIsNone(outcome.result(p, g)['fee_slippage_only_pct'])
        fixed = outcome.advance(p, g, [candle(START), candle(START+M)])
        self.assertIsNone(fixed['gap_ms'])
        self.assertEqual(fixed['cursor_ms'], START+2*M)

    def test_invalid_entry_and_funding_unknown_not_zero(self):
        p = plan('SHORT')
        with self.assertRaises(ValueError): outcome.initial(p, START+16*M, 100)
        with self.assertRaises(ValueError): outcome.initial(p, START, 103)
        s = outcome.initial(p, START, 100)
        s = outcome.advance(p, s, [candle(START), candle(START+M, hi=104)])
        events = [dict(time_ms=START, rate=-.001, mark_price=100)]
        self.assertIsNone(outcome.result(p, s, events)['full_net_pct'])
        r = outcome.result(p, s, events, (START, START+2*M))
        self.assertAlmostEqual(r['funding_cost_pct'], .1)
        self.assertAlmostEqual(r['full_net_pct'], -3.4)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.c = runtime.connect(self.root/'test.db')

    def tearDown(self):
        self.c.close(); self.tmp.cleanup()

    def test_send_once_ack_wrong_recipient_timeout_and_restart(self):
        ok = Mock(return_value=Mock(json=lambda: dict(ok=True, result=dict(message_id=42, chat=dict(id=123)))))
        a = runtime.send(self.c, 'one', 'test', 'abc', '123', START, post=ok, clock=lambda: START+1000)
        self.assertEqual(a['state'], 'ACK')
        runtime.send(self.c, 'one', 'test', 'abc', '123', START, post=ok)
        self.assertEqual(ok.call_count, 1)
        wrong = Mock(return_value=Mock(json=lambda: dict(ok=True, result=dict(message_id=42, chat=dict(id=999)))))
        self.assertEqual(runtime.send(self.c, 'wrong', 'test', 'abc', '123', START, post=wrong)['state'], 'UNCERTAIN')
        fail = Mock(side_effect=TimeoutError())
        self.assertEqual(runtime.send(self.c, 'timeout', 'test', 'abc', '123', START, post=fail)['state'], 'UNCERTAIN')
        runtime.recover(self.c)
        runtime.send(self.c, 'timeout', 'test', 'abc', '123', START, post=fail)
        self.assertEqual(fail.call_count, 1)

    def test_existing_destination_conflict_never_silently_changes(self):
        path = self.root/'long_short_telegram_chat_cache.db'
        with sqlite3.connect(path) as c:
            c.execute('CREATE TABLE runtime_settings(key TEXT,value TEXT)')
            c.execute('INSERT INTO runtime_settings VALUES(?,?)', ('telegram_chat_id', '123'))
        self.assertEqual(runtime.destination({'TELEGRAM_BOT_TOKEN': 'abc'}, self.root), ('abc', '123'))
        with self.assertRaises(ValueError): runtime.destination({'TELEGRAM_BOT_TOKEN': 'abc', 'TELEGRAM_CHAT_ID': '999'}, self.root)

    def test_publish_ack_reference_after_message_and_duplicate_block(self):
        sender = Mock(return_value=dict(state='ACK', ack_ms=START+12_345, message_id=1))
        with patch.object(runtime, 'quote', return_value=100.), patch('long_short_major_context.capture', return_value={}):
            self.assertEqual(runtime.publish(self.c, None, plan(), START+100, 'abc', '123', sender), 'WAIT_ENTRY')
            self.assertEqual(runtime.publish(self.c, None, plan(), START+200, 'abc', '123', sender), 'EXISTING_EPISODE')
        row = self.c.execute('SELECT * FROM plans').fetchone()
        self.assertEqual(row['entry_ms'], START+M)
        self.assertEqual(sender.call_count, 1)

    def test_uncertain_delivery_never_measured_as_trade(self):
        sender = Mock(return_value=dict(state='UNCERTAIN', ack_ms=None))
        with patch.object(runtime, 'quote', return_value=100.), patch('long_short_major_context.capture', return_value={}):
            self.assertEqual(runtime.publish(self.c, None, plan(), START, 'abc', '123', sender), 'DELIVERY_UNCERTAIN')
        with patch.object(runtime, 'read_bars') as reader:
            runtime.measure(self.c, None, START+H)
            reader.assert_not_called()
        self.assertEqual(runtime.report(self.c)['directions']['LONG']['closed'], 0)

    def test_manifest_and_runtime_profile_isolation(self):
        with patch.object(runtime, 'CONFIG_HASH', 'changed'):
            with self.assertRaises(ValueError): runtime.connect(self.root/'test.db')
        import long_short_runtime_profile as profiles
        with patch.dict(os.environ, {'LS_RUNTIME_PROFILE': runtime.PROFILE, 'LS_MAJOR_MOVE_V1': '1'}):
            self.assertTrue(profiles.enabled())
            self.assertFalse(profiles.major_enabled())
            self.assertFalse(profiles.swing_enabled())

    def test_recovery_preserves_ack_after_crash_before_plan_update(self):
        p = plan()
        self.c.execute('INSERT INTO plans VALUES(?,?,?,?,NULL,NULL,?,?,NULL,NULL)',
                       (p['id'], p['symbol'], p['direction'], START, 'PUBLISH_PENDING', json.dumps(p)))
        self.c.commit()
        post = Mock(return_value=Mock(json=lambda: dict(ok=True, result=dict(message_id=1, chat=dict(id=123)))))
        runtime.send(self.c, 'PLAN:'+p['id'], 'x', 'abc', '123', START, post=post, clock=lambda: START+1000)
        runtime.recover(self.c)
        row = self.c.execute('SELECT * FROM plans').fetchone()
        self.assertEqual(row['state'], 'WAIT_ENTRY')
        self.assertEqual(row['entry_ms'], START+M)

    def test_exit_notification_survives_restart_and_is_not_repeated(self):
        runtime.enqueue(self.c, 'EXIT:x', 'exit text', START)
        self.c.commit()
        runtime.recover(self.c)
        sender = Mock(return_value=dict(state='ACK', ack_ms=START+M))
        runtime.flush_events(self.c, 'abc', '123', START+M, sender)
        runtime.flush_events(self.c, 'abc', '123', START+2*M, sender)
        self.assertEqual(sender.call_count, 1)

    def test_full_paper_lifecycle_and_opposite_symbol_guard(self):
        ack = Mock(return_value=dict(state='ACK', ack_ms=START+1000, message_id=1))
        with patch.object(runtime, 'quote', return_value=100.), patch('long_short_major_context.capture', return_value={}):
            self.assertEqual(runtime.publish(self.c, None, plan(), START, 'abc', '123', ack), 'WAIT_ENTRY')
            self.assertEqual(runtime.publish(self.c, None, plan('SHORT'), START, 'abc', '123', ack), 'SYMBOL_ALREADY_ACTIVE')
        bars = [candle(START+M, hi=107, lo=99)]
        with patch.object(runtime, 'read_bars', return_value=bars):
            runtime.measure(self.c, None, START+2*M, 'abc', '123', ack)
        row = self.c.execute('SELECT * FROM plans').fetchone()
        self.assertEqual(row['state'], 'OBSERVING_CLOSED')
        r = json.loads(row['result'])
        self.assertEqual(r['exit']['kind'], 'TP')
        self.assertIsNone(r['full_net_pct'])
        self.assertEqual(runtime.report(self.c)['directions']['LONG']['closed'], 1)
        self.assertEqual(ack.call_count, 2)  # one plan and one push exit notification

    def test_stale_quote_never_creates_pending_delivery(self):
        sender = Mock()
        with patch.object(runtime, 'quote', return_value=None):
            self.assertEqual(runtime.publish(self.c, None, plan(), START, 'abc', '123', sender), 'LIVE_QUOTE_STALE')
        sender.assert_not_called()
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM plans').fetchone()[0], 0)

    def test_real_collector_schema_scan_to_ack_then_minute_exit(self):
        from long_short_futures_kline_shadow import initialize, upsert_rows, SOURCE
        path = self.root/'candles.db'
        kc = initialize(str(path))
        rows = triangle(); now = rows[-1]['close_ms']+1
        def add(iv, bars):
            items = [('TESTUSDT', iv, b['open_ms'], b['close_ms'], b['open'], b['high'], b['low'],
                      b['close'], 10., SOURCE, b['close_ms']+1, b['quote_volume'], 10, b['taker_buy_quote']) for b in bars]
            upsert_rows(kc, items); kc.commit()
        add('1h', rows); add('4h', context(now))
        kc.execute('INSERT INTO futures_last_price VALUES(?,?,?,?,?)', ('TESTUSDT',110.15,now,now,SOURCE)); kc.commit()
        kc.close(); reader = runtime.db(path, True)
        fake_post = Mock(return_value=Mock(json=lambda: dict(ok=True, result=dict(message_id=1, chat=dict(id=123)))))
        with patch.object(runtime, 'universe', return_value=[('TESTUSDT', 10_000_000, 110.15, 0)]), \
             patch.object(runtime.time, 'time', return_value=now/1000), \
             patch('requests.post', fake_post), patch('long_short_major_context.capture', return_value={}):
            runtime.scan(self.c, reader, now, 'abc', '123')
        p = self.c.execute('SELECT * FROM plans').fetchone()
        self.assertIsNotNone(p)
        self.assertEqual(p['state'], 'WAIT_ENTRY')
        self.assertEqual(fake_post.call_count, 1)
        reader.close()
        kc = initialize(str(path)); body = json.loads(p['payload'])
        add('1m', [candle(p['entry_ms'], op=110.15, hi=body['target']+.1, lo=110.14, close=body['target'])])
        kc.close(); reader = runtime.db(path, True)
        runtime.measure(self.c, reader, p['entry_ms']+M)
        reader.close()
        closed = json.loads(self.c.execute('SELECT result FROM plans').fetchone()[0])
        self.assertEqual(closed['exit']['kind'], 'TP')
        self.assertEqual(closed['funding_status'], 'UNKNOWN')


if __name__ == '__main__':
    unittest.main()
