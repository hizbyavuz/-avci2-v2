import copy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from ta_paper.core import (CFG, MINUTE, assess, closed_bars, descriptive_stats,
                           exit_on_bar, funding_cost, quantize, risk_room, sizing)
from ta_paper.io import Binance, Store, Telegram, book_check, tradable
from ta_paper.runner import advance, plan_message, scan

NOW = 200 * CFG.context_ms + 2*MINUTE
MARKET = dict(symbol='TESTUSDT', baseAsset='TEST', quoteAsset='USDT', marginAsset='USDT',
              underlyingType='COIN', underlyingSubType=[], status='TRADING',
              contractType='PERPETUAL', onboardDate=1,
              filters=[dict(filterType='PRICE_FILTER', tickSize='0.01'),
                       dict(filterType='MARKET_LOT_SIZE', stepSize='0.001', minQty='0.001', maxQty='10000'),
                       dict(filterType='MIN_NOTIONAL', notional='5')])


def series(period, n=120, now=NOW, reverse=False):
    end = now//period*period
    result = []
    for i in range(n):
        c = 100+.04*i
        o = c-.02
        if reverse:
            o, c = 210-o, 210-c
        start = end-(n-i)*period
        result.append([start, o, max(o,c)+.9, min(o,c)-.9, c, 100, start+period-1])
    return result


def setup(direction='LONG'):
    p = dict(symbol='TESTUSDT', direction=direction, observed_ms=NOW, candle_ms=NOW-CFG.entry_ms,
             status='SETUP', config_hash=CFG.fingerprint, version=CFG.version,
             reference_entry=100, stop=98, target=104, entry_limit=100.4,
             entry_ms=(NOW//MINUTE+1)*MINUTE, expires_ms=NOW+10*MINUTE, market=MARKET)
    if direction == 'SHORT':
        p.update(stop=102, target=96, entry_limit=99.6)
    return p


def filled(direction='LONG'):
    p = setup(direction)
    p.update(sizing(p, MARKET, 1000, 5), status='OPEN', id='test')
    return p


def candle(start, o=100, h=101, l=99, c=100):
    return [start,o,h,l,c,100,start+MINUTE-1]


class CandlesTests(unittest.TestCase):
    def test_open_and_future_bars_are_excluded(self):
        data = series(CFG.entry_ms)
        last = data[-1][0]+CFG.entry_ms
        extra = [last,100,1000,1,999,1,last+CFG.entry_ms-1]
        self.assertEqual(closed_bars(data+[extra], CFG.entry_ms, NOW), data)

    def test_gap_duplicate_bad_print_and_staleness_fail_closed(self):
        rows = series(CFG.entry_ms)
        for bad in (rows[:30]+rows[31:], rows[:30]+[rows[29]]+rows[30:], rows[:-1]):
            with self.assertRaises(ValueError):
                closed_bars(bad, CFG.entry_ms, NOW)
        rows[-1][2] = float('nan')
        with self.assertRaises(ValueError):
            closed_bars(rows, CFG.entry_ms, NOW)

    def test_assessment_is_causal_and_deterministic(self):
        bars = series(CFG.entry_ms)
        bars[-2][1:5] = [104.3,104.6,103.5,103.9]
        bars[-1][1:5] = [104.4,105.1,104.2,105.0]
        context = series(CFG.context_ms)
        p = assess('TESTUSDT', bars, context, NOW)
        self.assertEqual(p['status'], 'SETUP')
        self.assertEqual(p['direction'], 'LONG')
        future = [NOW//CFG.entry_ms*CFG.entry_ms,1,10000,.1,9999,100,
                  (NOW//CFG.entry_ms+1)*CFG.entry_ms-1]
        self.assertEqual(p, assess('TESTUSDT', bars+[future], context, NOW))
        mirrored = [[r[0],210-r[1],210-r[3],210-r[2],210-r[4],r[5],r[6]] for r in bars]
        q = assess('TESTUSDT', mirrored, series(CFG.context_ms, reverse=True), NOW)
        self.assertEqual(q['status'], 'SETUP')
        self.assertEqual(q['direction'], 'SHORT')

    def test_late_scan_does_not_create_plan(self):
        p = assess('TESTUSDT', series(CFG.entry_ms), series(CFG.context_ms), NOW+5*MINUTE)
        self.assertEqual(p['reason'], 'LATE_SCAN')


class SizingTests(unittest.TestCase):
    def test_risk_budget_after_fees_and_stop_slippage_both_sides(self):
        for direction in ('LONG','SHORT'):
            p = filled(direction)
            self.assertLessEqual(p['planned_loss'], 5)
            self.assertGreater(p['stress_loss'], p['planned_loss'])
            self.assertGreaterEqual(p['net_rr'], CFG.min_net_reward_risk)
            self.assertAlmostEqual(p['net_rr'],p['target_net']/p['planned_loss'])

    def test_notional_and_margin_caps_apply(self):
        cfg = replace(CFG, risk_fraction=.9, max_margin_equity=.1)
        p = sizing(setup(), MARKET, 1000, 900, cfg=cfg)
        self.assertLessEqual(p['notional'],300)
        self.assertLessEqual(p['indicative_margin'],100)

    def test_leverage_only_changes_margin_if_cap_does_not_bind(self):
        a = sizing(setup(), MARKET,1000,5,cfg=replace(CFG,indicative_leverage=3))
        b = sizing(setup(), MARKET,1000,5,cfg=replace(CFG,indicative_leverage=5))
        self.assertEqual(a['quantity'],b['quantity'])
        self.assertEqual(a['planned_loss'],b['planned_loss'])
        self.assertGreater(a['indicative_margin'],b['indicative_margin'])

    def test_rounding_decimal_steps_and_minimum_order(self):
        self.assertEqual(quantize(1.239,.01),1.23)
        self.assertEqual(quantize(1.231,.01,True),1.24)
        with self.assertRaisesRegex(ValueError,'MINIMUM'):
            sizing(setup(), MARKET,1,.005)

    def test_invalid_geometry_chase_and_tiny_stop(self):
        p = setup()
        for change in ({'stop':101},{'target':99},{'stop':99.9}):
            with self.assertRaises(ValueError):
                sizing(dict(p,**change),MARKET,1000,5)
        with self.assertRaisesRegex(ValueError,'LATE_ENTRY'):
            sizing(p,MARKET,1000,5,102)

    def test_portfolio_and_daily_guards(self):
        p = filled()
        with self.assertRaisesRegex(ValueError,'SAME_DIRECTION'):
            risk_room(1000,1000,0,[p],'LONG')
        with self.assertRaisesRegex(ValueError,'RISK_LIMIT'):
            risk_room(980,1000,-20,[],'LONG')
        with self.assertRaisesRegex(ValueError,'MAX_OPEN'):
            risk_room(1000,1000,0,[p,filled('SHORT')],'LONG')
        self.assertLessEqual(risk_room(1000,1000,0,[p],'SHORT'),10-p['planned_loss'])


class ExecutionTests(unittest.TestCase):
    def test_stop_first_and_gap_fill_are_pessimistic(self):
        p = filled()
        result = exit_on_bar(p,candle(p['entry_ms'],100,106,97,101))
        self.assertEqual(result['reason'],'STOP')
        self.assertTrue(result['same_minute_ambiguous'])
        gap = exit_on_bar(p,candle(p['entry_ms'],95,96,94,95))
        self.assertLess(gap['exit_price'],95)
        self.assertGreater(abs(gap['pnl_before_funding']),p['planned_loss'])

    def test_short_gap_and_target_touch(self):
        p = filled('SHORT')
        gap = exit_on_bar(p,candle(p['entry_ms'],106,107,105,106))
        self.assertGreater(gap['exit_price'],106)
        self.assertIsNone(exit_on_bar(p,candle(p['entry_ms'],100,101,96,99)))
        result = exit_on_bar(p,candle(p['entry_ms'],100,101,95.99,99))
        self.assertEqual(result['reason'],'TARGET')

    def test_funding_crossing_counts_even_for_short_holding_time(self):
        p = filled()
        rows = [dict(symbol=p['symbol'],fundingTime=p['entry_ms']+MINUTE,
                     fundingRate='0.001',markPrice='101')]
        cost, uncertain = funding_cost(p,rows,p['entry_ms']+2*MINUTE)
        self.assertAlmostEqual(cost,p['quantity']*.101)
        self.assertFalse(uncertain)
        q = dict(p,direction='SHORT')
        self.assertAlmostEqual(funding_cost(q,rows,p['entry_ms']+2*MINUTE)[0],-cost)

    def test_ambiguous_exit_minute_does_not_assume_funding_credit(self):
        p = filled('SHORT')
        p['exit_interval_start'] = p['entry_ms']+MINUTE
        rows = [dict(symbol=p['symbol'],fundingTime=p['entry_ms']+MINUTE,
                     fundingRate='0.001',markPrice='101')]
        self.assertEqual(funding_cost(p,rows,p['entry_ms']+2*MINUTE),(0,True))


class IOTests(unittest.TestCase):
    def test_no_order_account_endpoints(self):
        client = Binance()
        for endpoint in ['/fapi/v1/order','/fapi/v2/account','/fapi/v1/leverage','https://other/fapi/v1/time']:
            with self.assertRaisesRegex(ValueError,'REFUSED'):
                client.get(endpoint)

    def test_universe_excludes_noncrypto_new_and_stale(self):
        now = 100*86_400_000
        ticker = dict(symbol='TESTUSDT',closeTime=now-100,quoteVolume='3000000')
        self.assertTrue(tradable(MARKET,ticker,now))
        for edit in ({'underlyingType':'INDEX'},{'underlyingSubType':['TradFi']},
                     {'baseAsset':'XAG'},{'onboardDate':now-86_400_000}):
            self.assertFalse(tradable(dict(MARKET,**edit),ticker,now))
        self.assertFalse(tradable(MARKET,dict(ticker,closeTime=now-500000),now))

    def test_book_age_spread_and_depth(self):
        book = dict(E=NOW,bids=[['99.99','100']],asks=[['100.01','100']])
        self.assertGreater(book_check(book,NOW,'LONG',200)['vwap'],100)
        with self.assertRaisesRegex(ValueError,'STALE'):
            book_check(book,NOW+6000,'LONG',200)
        with self.assertRaisesRegex(ValueError,'DEPTH'):
            book_check(book,NOW,'LONG',1_000_000)

    def test_restart_dedup_config_freeze_and_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'state.sqlite3'
            s=Store(path)
            p=filled()
            s.save(p)
            s.queue('one','text',NOW)
            s.queue('one','text',NOW)
            rows=series(MINUTE)
            s.candles('TESTUSDT','1m',rows)
            s.db.close()
            s=Store(path)
            self.assertEqual(len(s.plans()),1)
            self.assertEqual(s.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0],1)
            rows[-1][4]+=.01
            with self.assertRaisesRegex(ValueError,'CHANGED'):
                s.candles('TESTUSDT','1m',rows)
            s.db.close()
            with self.assertRaisesRegex(RuntimeError,'CONFIG_CHANGED'):
                Store(path,replace(CFG,risk_fraction=.006))

    def test_full_paper_lifecycle_and_no_pre_entry_credit(self):
        class Client:
            stamp=NOW+3*MINUTE
            def now(self): return self.stamp
            def candles(self,*args,**kw):
                return [candle(NOW//MINUTE*MINUTE),candle((NOW//MINUTE+1)*MINUTE,100,106,99,105)]
            def funding(self,*args): return []
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'s.db')
            p=filled()
            p.update(status='PENDING')
            store.save(p)
            client=Client()
            advance(store,client)
            result=store.plans()[0]
            self.assertEqual(result['status'],'FUNDING_PENDING')
            self.assertEqual(result['exit_reason'],'TARGET')
            self.assertEqual(result['entry_ms'],p['entry_ms'])
            client.stamp += 3*MINUTE
            advance(store,client)
            result=store.plans()[0]
            self.assertEqual(result['status'],'CLOSED')
            self.assertGreater(result['net_pnl'],0)
            self.assertEqual(descriptive_stats([result])['status'],'UNVALIDATED')

    def test_missing_path_is_not_classified_as_loss_or_profit(self):
        class Client:
            def now(self): return NOW+3*MINUTE
            def candles(self,*args,**kw):
                return [candle((NOW//MINUTE+2)*MINUTE)]
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'s.db')
            store.save(filled())
            advance(store,Client())
            self.assertEqual(store.plans()[0]['status'],'DATA_HOLD')
            self.assertEqual(descriptive_stats(store.plans())['n'],0)

    def test_pre_entry_target_is_cancelled_not_counted_as_a_win(self):
        class Client:
            def now(self): return NOW+3*MINUTE
            def candles(self,*args,**kw):
                return [candle(NOW//MINUTE*MINUTE,100,106,99,105),
                        candle((NOW//MINUTE+1)*MINUTE,100,106,99,105)]
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'s.db')
            p=filled()
            p['status']='PENDING'
            store.save(p)
            advance(store,Client())
            self.assertEqual(store.plans()[0]['status'],'CANCELLED')
            self.assertEqual(descriptive_stats(store.plans())['n'],0)

    def test_messages_are_explicitly_example_paper_accounts(self):
        text=plan_message(filled())
        for label in ['ÖRNEK TEST HESABI','DOĞRULANMADI','Gerçek emir yok','likidasyon']:
            self.assertIn(label,text)

    def test_full_scan_records_both_sides_and_portfolio_limits(self):
        bars=series(CFG.entry_ms)
        bars[-2][1:5]=[104.3,104.6,103.5,103.9]
        bars[-1][1:5]=[104.4,105.1,104.2,105]
        mirrored=[[r[0],210-r[1],210-r[3],210-r[2],210-r[4],r[5],r[6]] for r in bars]
        class Client:
            def now(self): return NOW
            def sync_time(self): pass
            def get(self,path,**kw):
                if path.endswith('exchangeInfo'):
                    return {'symbols':[dict(MARKET,symbol=s) for s in ['AAAUSDT','BBBUSDT','CCCUSDT']]}
                if path.endswith('24hr'):
                    return [dict(symbol=s,quoteVolume='3000000',closeTime=NOW) for s in ['AAAUSDT','BBBUSDT','CCCUSDT']]
                return dict(E=NOW,bids=[['104.99','100']],asks=[['105.01','100']])
            def candles(self,symbol,tf,*args,**kw):
                if tf=='4h': return series(CFG.context_ms,reverse=symbol=='BBBUSDT')
                return mirrored if symbol=='BBBUSDT' else bars
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'s.db')
            health=scan(store,Client())
            self.assertEqual(health['assessed'],3)
            self.assertEqual(health['admitted'],2)
            self.assertEqual({p['direction'] for p in store.plans()},{'LONG','SHORT'})
            self.assertEqual(store.db.execute('SELECT COUNT(*) FROM assessments').fetchone()[0],3)
            self.assertGreater(store.db.execute('SELECT COUNT(*) FROM candles').fetchone()[0],600)
            # Exact same closed candle cannot create duplicate plans.
            self.assertEqual(scan(store,Client())['admitted'],0)


if __name__ == '__main__':
    unittest.main()
