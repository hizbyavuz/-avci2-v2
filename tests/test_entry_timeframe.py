import copy
import unittest

from research.entry_timeframe_v1 import compare as tf


def bar(t, period=tf.MINUTE, price=100., q=100_000/60):
    return dict(open_ms=t, close_ms=t+period-1, open=price, high=price+1,
                low=price-1, close=price, quote_volume=q, taker_buy_quote=.6*q)


class EntryTimeframeTest(unittest.TestCase):
    def test_aggregation_does_not_fill_missing_or_invalid_minutes(self):
        rows = [bar(i*tf.MINUTE) for i in range(30)]
        rows.pop(7)
        rows[20]['low'] = 200
        out, reasons = tf.aggregate(rows, 15)
        self.assertEqual(out, [])
        self.assertEqual(reasons, {'PARTIAL_BUCKET':1, 'INVALID_OHLC':1})

    def test_only_completed_buckets_survive_prefix(self):
        rows = [bar(i*tf.MINUTE, price=100+i/100) for i in range(60)]
        full, _ = tf.aggregate(rows, 15)
        prefix, _ = tf.aggregate(rows[:38], 15)
        self.assertEqual(prefix, full[:2])
        self.assertEqual(full[0]['high'], rows[14]['high'])
        self.assertEqual(full[0]['open'], rows[0]['open'])

    def test_hour_context_ignores_entire_trigger_hour_and_future(self):
        hours = [bar(i*tf.HOUR, tf.HOUR, q=100_000) for i in range(168)]
        before = tf.context(hours, 168*tf.HOUR)
        poisoned = hours+[bar(168*tf.HOUR, tf.HOUR, price=10000, q=1e12)]
        self.assertEqual(before, tf.context(poisoned, 168*tf.HOUR))
        self.assertEqual(before['trigger'], 101)
        self.assertEqual(before['stop'], 98.5)
        self.assertEqual(before['atr_1h'], 2)
        self.assertIsNone(tf.context(hours[:-1], 168*tf.HOUR))

    def test_smaller_entry_does_not_shrink_hourly_stop(self):
        hours = [bar(i*tf.HOUR, tf.HOUR, q=100_000) for i in range(168)]
        ctx = tf.context(hours, 168*tf.HOUR)
        plans = []
        for minutes in tf.PERIODS:
            t = 168*tf.HOUR
            b = bar(t, minutes*tf.MINUTE, q=minutes*10_000)
            b.update(close=101.2, high=101.3)
            baseline = {t-d*tf.DAY:bar(t-d*tf.DAY, minutes*tf.MINUTE,
                       q=minutes*100_000/60) for d in range(1,8)}
            plan, reason = tf.candidate('TESTUSDT', b, baseline, ctx, minutes)
            self.assertEqual(reason, 'READY')
            self.assertEqual(plan['stop'], 98.5)
            self.assertEqual(plan['atr_1h'], 2)
            self.assertEqual(plan['context_end_ms'], t-1)
            plans.append(plan)
        self.assertEqual(len({p['volume_multiple'] for p in plans}), 1)

    def test_small_bar_candidate_not_conditioned_on_future_hour_break(self):
        t = 168*tf.HOUR
        rows = [bar(i*tf.MINUTE) for i in range(168*60+60)]
        for b in rows[-60:-45]:
            b['quote_volume'] *= 6
            b['taker_buy_quote'] *= 6
        rows[-46].update(close=101.2, high=101.3)
        hours, _ = tf.aggregate(rows, 60)
        contexts = {t:tf.context(hours, t)}
        small, _ = tf.aggregate(rows, 15)
        p15, _ = tf.select('TESTUSDT', 15, small, hours, contexts, t, t+tf.HOUR)
        p60, _ = tf.select('TESTUSDT', 60, hours, hours, contexts, t, t+tf.HOUR)
        self.assertEqual(len(p15), 1)
        self.assertEqual(len(p60), 0)
        prefix = rows[:168*60+15]
        phours, _ = tf.aggregate(prefix, 60)
        psmall, _ = tf.aggregate(prefix, 15)
        pp, _ = tf.select('TESTUSDT', 15, psmall, phours,
                         {t:tf.context(phours,t)}, t, t+tf.HOUR)
        self.assertEqual(p15, pp)

    def test_missing_prior_same_slot_does_not_default_to_zero(self):
        hours = [bar(i*tf.HOUR, tf.HOUR, q=100_000) for i in range(168)]
        b = bar(168*tf.HOUR, 15*tf.MINUTE)
        b.update(close=101.2, high=101.3)
        p, reason = tf.candidate('TESTUSDT', b, {}, tf.context(hours,168*tf.HOUR),15)
        self.assertIsNone(p)
        self.assertEqual(reason,'VOLUME_BASELINE_MISSING')

    def test_measure_starts_strictly_after_observation_and_rechecks_break(self):
        p = dict(direction='LONG', stop=98.5, trigger=101, atr_1h=2,
                 origin_price=99, obstacle=None, observed_ms=15*tf.MINUTE+999)
        rows = [bar(i*tf.MINUTE, price=100) for i in range(15,20)]
        a = tf.measure(p,rows,[b['open_ms'] for b in rows])
        self.assertEqual(a['reference_ms'],16*tf.MINUTE)
        self.assertEqual(a['status'],'ENTRY_INVALID')
        self.assertEqual(a['reason'],'BREAK_NOT_HELD')

    def test_gap_after_entry_is_visible_not_a_complete_loss_or_win(self):
        p = dict(direction='LONG', stop=98.5, trigger=101, atr_1h=2,
                 origin_price=99, obstacle=None, observed_ms=15*tf.MINUTE+999)
        rows = [bar(16*tf.MINUTE, price=101.2), bar(18*tf.MINUTE, price=103)]
        a = tf.measure(p,rows,[b['open_ms'] for b in rows])
        self.assertEqual(a['status'],'MEASURED')
        self.assertEqual(a['outcome']['status'],'DATA_GAP')
        self.assertIsNone(a['outcome']['fee_slippage_only_pct'])
        self.assertIsNone(a['full_net_pct'])


if __name__ == '__main__':
    unittest.main()
