import copy
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from ta_paper.core import CFG, MINUTE, assess, closed_bars
from ta_paper.io import Store, Binance
from ta_paper.features import (capture_klines, kline_aux, relative_strength, oi_change,
    funding_percentile, record_context, hypothesis_results, tick, DAY)
from ta_paper.archive import Archive
from ta_paper.replay import window
from ta_paper.shadow import (enqueue, capture_entry, advance_path, settle_funding,
    path_summary, tick as shadow_tick, health)
from test_ta_paper import setup, candle, NOW
from test_ta_paper_scan_recovery import FakeClient


class FinalObservationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.store=Store(self.root/'bybit.sqlite3')

    def tearDown(self):
        self.store.db.close();self.tmp.cleanup()

    def test_optional_taker_does_not_change_any_assessment(self):
        client=FakeClient(n=1,setups=True)
        for fraction in (.01,.99):
            b=client.candles('S000USDT','30m');c=client.candles('S000USDT','4h')
            p=assess('TESTUSDT',b,c,client.now())
            enriched=[r+[float(r[4])*float(r[5]),10,float(r[5])*fraction,float(r[4])*float(r[5])*fraction] for r in b]
            self.assertEqual(p,assess('TESTUSDT',enriched,c,client.now()))

    def test_quote_taker_units_and_missing_are_not_inferred(self):
        r=candle(NOW)+[10000,50,60,5500]
        self.assertEqual(kline_aux(r)['taker_buy_share'],.55)
        self.assertIsNone(kline_aux(r[:7])['taker_buy_share'])
        self.assertIsNone(kline_aux(r[:8])['taker_buy_share'])
        r[10]=10001
        with self.assertRaises(ValueError):kline_aux(r)
        self.assertIsNone(kline_aux([NOW,100,100,100,100,0,NOW+MINUTE-1,0,0,0,0])['taker_buy_share'])

    def test_auxiliary_conflict_keeps_first_evidence_without_rejecting_core(self):
        r=candle(NOW)+[10000,10,60,6000]
        capture_klines(self.store.db,'XUSDT','1m',[r],NOW+2*MINUTE,'TEST')
        r[-1]=4000
        capture_klines(self.store.db,'XUSDT','1m',[r],NOW+2*MINUTE,'TEST')
        value=json.loads(self.store.db.execute('SELECT payload FROM candle_aux').fetchone()[0])
        self.assertEqual(value['taker_buy_share'],.6)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM aux_issues').fetchone()[0],1)
        self.assertEqual(self.store.plans(),[])

    def test_future_unclosed_aux_is_not_saved(self):
        r=candle(NOW)+[10000,10,60,6000]
        capture_klines(self.store.db,'XUSDT','1m',[r],NOW+MINUTE,'TEST')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM candle_aux').fetchone()[0],0)

    def test_binance_rest_caches_seven_columns_but_retains_taker(self):
        fake=FakeClient(n=1,setups=True);client=Binance(candle_store=self.store);client.now=fake.now
        rows=fake.candles('S000USDT','30m')
        rows=[r+[float(r[4])*float(r[5]),5,float(r[5])*.6,float(r[4])*float(r[5])*.6] for r in rows]
        with patch.object(client,'get',return_value=rows):result=client.candles('S000USDT','30m')
        self.assertTrue(all(len(r)==7 for r in result))
        self.assertGreater(self.store.db.execute('SELECT COUNT(*) FROM candle_aux').fetchone()[0],0)

    def test_replay_matches_live_window_and_future_values_never_leak(self):
        period=CFG.context_ms;rows=[[i*period,100,105,99,100+i*.01,1,(i+1)*period-1] for i in range(130)]
        now=129*period+3000
        historical=window(rows,[r[0] for r in rows],now,period)
        live=closed_bars(rows[-120:],period,now)
        self.assertEqual(len(historical),119);self.assertEqual(historical,live)
        rows[-1][4]=999999
        self.assertEqual(historical,window(rows,[r[0] for r in rows],now,period))

    def test_relative_strength_requires_identical_complete_endpoints(self):
        rows=[[i*CFG.context_ms,100,130,90,100+i,1,(i+1)*CFG.context_ms-1] for i in range(20)]
        btc=copy.deepcopy(rows)
        for r in btc:r[4]=100
        self.assertGreater(relative_strength(rows,btc),0)
        btc.pop(4);self.assertIsNone(relative_strength(rows,btc))

    def test_oi_uses_base_count_and_requires_continuity(self):
        rows=[dict(timestamp=i*5*MINUTE,openInterest=100+i) for i in range(49)]
        self.assertAlmostEqual(oi_change(rows,240*MINUTE)['change_4h'],.48)
        rows.append(dict(timestamp=250*MINUTE,openInterest=99999))
        self.assertAlmostEqual(oi_change(rows,240*MINUTE)['change_4h'],.48)
        rows.pop(3);self.assertIsNone(oi_change(rows,240*MINUTE)['change_4h'])

    def test_funding_uses_settled_past_midrank_and_checks_cadence(self):
        cutoff=31*DAY
        rows=[dict(fundingTime=i*480*MINUTE,fundingRate=.001) for i in range(94)]
        result=funding_percentile(rows,cutoff)
        self.assertEqual(result['status'],'OK');self.assertEqual(result['percentile'],.5)
        rows.append(dict(fundingTime=cutoff+480*MINUTE,fundingRate=999))
        self.assertEqual(funding_percentile(rows,cutoff),result)
        rows.pop(20)
        self.assertIsNone(funding_percentile(rows,cutoff)['percentile'])

    def test_hypotheses_are_separate_and_missing_is_not_a_pass(self):
        x=dict(taker_buy_share=.6,oi_change_4h=.1,price_change_4h=.02,funding_percentile_30d=.95,btc_relative_3d=.01)
        self.assertEqual(hypothesis_results(x,'LONG'),dict(taker=True,oi=True,funding=False,relative=True))
        self.assertTrue(all(v is None for v in hypothesis_results({},'SHORT').values()))

    def test_context_does_not_mutate_signal_and_restart_is_idempotent(self):
        fake=FakeClient(n=1,setups=True);now=fake.now()
        b=closed_bars(fake.candles('S000USDT','30m'),CFG.entry_ms,now)
        c=closed_bars(fake.candles('S000USDT','4h'),CFG.context_ms,now)
        p=assess('TESTUSDT',b,c,now);original=copy.deepcopy(p)
        record_context(self.store,p,b,c,'BYBIT');record_context(self.store,p,b,c,'BYBIT')
        self.assertEqual(original,p)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM feature_events').fetchone()[0],1)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM feature_jobs').fetchone()[0],1)
        self.assertEqual(self.store.plans(),[])

    def test_aux_network_budget_obeys_existing_ban_and_scan_deadline(self):
        client=SimpleNamespace(now=lambda:NOW,gate=SimpleNamespace(state=lambda:dict(remaining_seconds=100)))
        client.get=lambda *a,**k:self.fail('Forbidden Binance request')
        tick(self.store,client,'BINANCE',NOW+MINUTE)
        client.gate.state=lambda:dict(remaining_seconds=0)
        tick(self.store,client,'BINANCE',NOW+1000)

    def test_retrospective_features_keep_receipt_time_not_signal_time(self):
        fake=FakeClient(n=1,setups=True);now=fake.now()
        b=closed_bars(fake.candles('S000USDT','30m'),CFG.entry_ms,now)
        c=closed_bars(fake.candles('S000USDT','4h'),CFG.context_ms,now)
        p=assess('TESTUSDT',b,c,now);record_context(self.store,p,b,c,'BINANCE')
        cutoff=now-5*MINUTE;last=cutoff//(5*MINUTE)*5*MINUTE
        rows=[dict(timestamp=last-i*5*MINUTE,sumOpenInterest=100-i) for i in range(50)]
        client=SimpleNamespace(now=lambda:now+10_000,gate=SimpleNamespace(state=lambda:dict(remaining_seconds=0)))
        calls=[]
        def get(path,**params):calls.append(path);return rows
        client.get=get
        tick(self.store,client,'BINANCE',now+MINUTE)
        tick(self.store,client,'BINANCE',now+MINUTE)
        self.assertEqual(len(calls),1)
        event_row=self.store.db.execute("SELECT * FROM feature_events WHERE kind='OI'").fetchone()
        self.assertGreater(event_row['received_ms'],p['observed_ms'])
        payload=json.loads(event_row['payload'])
        self.assertEqual(payload['timing'],'RETROSPECTIVE_ENRICHMENT_NOT_KNOWN_AT_SIGNAL')
        self.assertEqual(payload['feature']['status'],'OK')

    def test_stream_aux_survives_restart_and_never_changes_bar_shape(self):
        from ta_paper.streams import LiveFeed
        from ta_paper.rate_limit import RequestGate
        directory=self.root/'streams';feed=LiveFeed(directory,RequestGate(),self.store)
        start=NOW//CFG.entry_ms*CFG.entry_ms
        r=[start,100,101,99,100,100,start+CFG.entry_ms-1,10000,20,60,6000]
        feed.put_bars('TESTUSDT','30m',[r],'TEST',start+CFG.entry_ms+3000)
        feed.db.close();feed=LiveFeed(directory,RequestGate(),self.store)
        feed.copy_aux(self.store.db,'TESTUSDT','30m',[r[:7]])
        saved=json.loads(self.store.db.execute('SELECT payload FROM candle_aux').fetchone()[0])
        self.assertEqual(saved['taker_buy_share'],.6)
        bar=json.loads(feed.db.execute('SELECT payload FROM bars').fetchone()[0])
        self.assertEqual(len(bar),7);feed.db.close()

    def test_archive_keeps_taker_columns_for_research(self):
        path=self.root/'candles.zip';r=candle(NOW)+[10000,20,60,6000,0]
        with zipfile.ZipFile(path,'w') as z:z.writestr('rows.csv',','.join(map(str,r))+'\n')
        ar=Archive(self.root/'cache')
        with patch.object(ar,'file',return_value=path):rows=ar.rows('TESTUSDT','1m',['2026-08'])
        self.assertEqual(len(rows[0]),11);self.assertEqual(kline_aux(rows[0])['taker_buy_share'],.6)

    def shadow_setup(self,direction='LONG'):
        p=setup(direction);p['source_venue']='BYBIT'
        market=dict(bybit_lot_filter={'qtyStep':'.001','minOrderQty':'.001','minNotionalValue':'5'},funding_interval_minutes=480)
        enqueue(self.store,p,market)
        result=json.loads(self.store.db.execute('SELECT payload FROM shadow_trials').fetchone()[0])
        stamp=[p['entry_ms']+10_000]
        def request(path,**kwargs):
            self.assertEqual(path,'/v5/market/orderbook')
            return dict(result=dict(s=p['symbol'],ts=stamp[0],b=[['99.99','1000']],a=[['100.01','1000']]))
        client=SimpleNamespace(now=lambda:stamp[0],request=request,gate=SimpleNamespace(state=lambda:dict(remaining_seconds=0)))
        return result,client,stamp

    def test_shadow_entry_is_independent_size_venue_and_not_a_portfolio_fill(self):
        for direction in ('LONG','SHORT'):
            self.store.db.execute('DELETE FROM observations');self.store.db.execute('DELETE FROM watch_candidates');self.store.db.commit()
            p,c,_=self.shadow_setup(direction)
            capture_entry(self.store,c,p)
            self.assertEqual(p['shadow_status'],'OPEN_MODEL')
            self.assertLessEqual(p['notional'],200.1)
            self.assertEqual(p['entry_mark'],'SAMPLED_BOOK_NOT_ACTUAL_FILL')
            self.assertEqual(self.store.plans(),[])
            self.store.db.execute('DELETE FROM shadow_trials');self.store.db.commit()

    def test_shadow_expired_never_invents_historical_book_fill(self):
        p,c,stamp=self.shadow_setup();stamp[0]=p['expires_ms']+1
        c.request=lambda *a,**k:self.fail('Expired job requested book')
        capture_entry(self.store,c,p)
        self.assertEqual(p['shadow_status'],'ENTRY_MISSED');self.assertNotIn('entry_mark',p)

    def test_shadow_gap_and_partial_entry_bar_stay_unresolved(self):
        p,c,stamp=self.shadow_setup();capture_entry(self.store,c,p)
        p['preentry_checked']=True;start=p['entry_ms']//MINUTE*MINUTE
        self.store.candles(p['symbol'],'1m',[candle(start,h=105,l=97)])
        advance_path(self.store,p,start+2*MINUTE)
        self.assertEqual(p['shadow_status'],'ENTRY_MINUTE_AMBIGUOUS');self.assertNotIn('net_r_modelled',p)

    def test_shadow_must_validate_preentry_path_and_cannot_jump_missing_minutes(self):
        p,c,stamp=self.shadow_setup();stamp[0]+=2*MINUTE;capture_entry(self.store,c,p)
        start=p['observed_ms']//MINUTE*MINUTE
        # Later profitable bars cannot mask the missing pre-entry interval.
        entry=p['entry_ms']//MINUTE*MINUTE
        self.store.candles(p['symbol'],'1m',[candle(entry+MINUTE,h=105,l=99)])
        advance_path(self.store,p,entry+3*MINUTE)
        self.assertEqual(p['shadow_status'],'OPEN_MODEL');self.assertNotIn('net_r_modelled',p)
        self.store.candles(p['symbol'],'1m',[candle(t,h=105 if t==start+MINUTE else 101,l=99)
                                          for t in range(start,entry,MINUTE)])
        advance_path(self.store,p,entry+3*MINUTE)
        self.assertEqual(p['shadow_status'],'CANCELLED_BEFORE_ENTRY')

    def test_shadow_postentry_stop_first_gap_open_costs_and_no_funding_zero_fill(self):
        p,c,stamp=self.shadow_setup();capture_entry(self.store,c,p)
        start=p['entry_ms']//MINUTE*MINUTE
        p['preentry_checked']=True
        self.store.candles(p['symbol'],'1m',[candle(start,h=101,l=99),candle(start+MINUTE,o=97,h=105,l=96)])
        advance_path(self.store,p,start+3*MINUTE)
        self.assertEqual(p['shadow_status'],'FUNDING_PENDING');self.assertEqual(p['reason'],'STOP')
        self.assertTrue(p['gap_stop']);self.assertTrue(p['same_minute_ambiguous'])
        self.assertLess(p['exit_price'],97);self.assertNotIn('net_r_modelled',p)
        c.request=lambda *a,**k:dict(result=dict(category='linear',list=[]))
        with self.assertRaisesRegex(ValueError,'FUNDING_GAP'):settle_funding(self.store,c,p)
        self.assertNotIn('net_r_modelled',p)

    def test_shadow_funding_actual_rates_with_proxy_marks_and_costs(self):
        p,c,stamp=self.shadow_setup();capture_entry(self.store,c,p)
        p.update(shadow_status='FUNDING_PENDING',exit_interval_start=p['entry_ms']+2*MINUTE,
                 exit_interval_end=p['entry_ms']+3*MINUTE,pnl_before_funding=2,stress_pnl_before_funding=1,
                 funding_rows=[dict(symbol=p['symbol'],fundingTime=p['entry_ms']+MINUTE,fundingRate=.001,markPrice=100)])
        settle_funding(self.store,c,p)
        self.assertEqual(p['shadow_status'],'CLOSED_MODEL')
        self.assertAlmostEqual(p['funding_cost'],p['quantity']*.1)
        self.assertLess(p['net_pnl_modelled'],2);self.assertNotIn('net_r',p)

    def test_path_reach_excludes_pre_signal_and_stops_at_gaps(self):
        p,c,stamp=self.shadow_setup();capture_entry(self.store,c,p)
        start=(p['entry_ms']//MINUTE+1)*MINUTE
        self.store.candles(p['symbol'],'1m',[candle(start,h=108,l=99),candle(start+2*MINUTE,h=120,l=90)])
        path_summary(self.store,p,start+4*MINUTE)
        result=p['path_summary'];self.assertEqual(result['continuous_observed_minutes'],1)
        self.assertIsNotNone(result['reach']['7']);self.assertIsNone(result['reach']['10'])
        self.assertFalse(result['complete']);self.assertLess(result['raw_mfe'],.09)

    def test_metrics_exact_duplicates_are_counted_conflicts_refused(self):
        ar=Archive(self.root/'archive')
        header='create_time,symbol,sum_open_interest,sum_open_interest_value,count_toptrader_long_short_ratio,sum_toptrader_long_short_ratio,count_long_short_ratio,sum_taker_long_short_vol_ratio\n'
        a='2025-10-01 00:00:00,BTCUSDT,100,10000,1,1,1,1\n'
        path=self.root/'metrics.zip'
        for content,conflict in ((a+a,False),(a+a.replace(',100,',',101,'),True)):
            with zipfile.ZipFile(path,'w') as z:z.writestr('data.csv',header+content)
            with patch.object(ar,'checked_file',return_value=path):
                if conflict:
                    with self.assertRaisesRegex(ValueError,'CONFLICTING_DUPLICATE'):ar.metrics('BTCUSDT',['2025-10-01'])
                else:
                    rows,quality=ar.metrics('BTCUSDT',['2025-10-01'])
                    self.assertEqual(len(rows),1);self.assertEqual(quality['identical_duplicates'],1)
                    self.assertFalse(quality['archive_publication_time_known'])

    def test_report_does_not_count_missed_unresolved_or_raw_reach_as_profit(self):
        from ta_paper.measurement_report import summarize_shadow,report
        values=[dict(shadow_status='CLOSED_MODEL',entry_ms=DAY,net_r_modelled=1),
                dict(shadow_status='ENTRY_MISSED'),dict(shadow_status='FUNDING_UNRESOLVED')]
        r=summarize_shadow(values)
        self.assertEqual(r['closed_modelled'],1);self.assertEqual(r['mean_net_r_modelled'],1)
        self.assertEqual(r['incomplete_paths'],3);self.assertIsNone(r['day_standard_error'])
        self.assertEqual(r['raw_reach_complete_paths']['3']['denominator'],0)
        p,c,_=self.shadow_setup();capture_entry(self.store,c,p)
        self.assertEqual(report(self.root/'bybit.sqlite3','BYBIT')['all']['closed_modelled'],0)


if __name__=='__main__':unittest.main()
