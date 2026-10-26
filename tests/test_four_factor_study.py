import asyncio
from collections import Counter
from dataclasses import replace
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
import zipfile

import numpy as np

from ta_paper.core import CFG, MINUTE, assess, ema
from ta_paper.features import hypothesis_results, funding_percentile
from ta_paper.replay import execute
from research.four_factor_20261027.data import (START,DAY,Discovery,MinutePath,screen_reclaims,weights,read_rows)
from research.four_factor_20261027.download import Download, key, excluded_symbols
from research.four_factor_20261027.study import stress_same_event,oi_at,needed_files,process_symbol,write_gzip,read_gzip,identity
from research.four_factor_20261027.report import summary,comparison,sensitivity,top_removed
from test_ta_paper import setup,series,candle,NOW


class StudyTests(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()

    def test_runtime_core_and_strategy_are_frozen(self):
        root=Path(__file__).resolve().parents[1]
        self.assertEqual(hashlib.sha256((root/'ta_paper/core.py').read_bytes()).hexdigest(),
            '271853954efb872b0ef3121f077a0417fbe286a6c3bf794b6e3e600bd4406218')
        self.assertEqual(identity()['strategy_hash'],'f4485acc0340fb6352cde17558ada6f53459bafa486793423792c3b4e2ccdedd')

    def test_research_scope_excludes_equities_but_keeps_spx_crypto(self):
        symbols=['BTCUSDT','SPXUSDT','QQQUSDT','INTCUSDT','OPENAIUSDT','USDCUSDT','BTCDOMUSDT']
        self.assertEqual(excluded_symbols(symbols),['BTCDOMUSDT','INTCUSDT','OPENAIUSDT','QQQUSDT','USDCUSDT'])

    def test_vector_seed_and_previous_value_match_same_119_bar_core(self):
        values=np.random.default_rng(711).normal(100,3,119)
        expected=ema(values.tolist(),20)
        self.assertAlmostEqual(values@weights(119),expected[-1],places=11)
        self.assertAlmostEqual(values[:-1]@weights(118),expected[-2],places=11)

    def test_conservative_vector_screen_never_omits_core_setup(self):
        rng=np.random.default_rng(77);v=100+np.cumsum(rng.normal(0,.4,500));period=CFG.entry_ms
        rows=[]
        for i,c in enumerate(v):
            t=START+i*period;o=v[max(i-1,0)]
            rows.append([t,o,max(o,c)+.15,min(o,c)-.15,c,100,t+period-1,1e6,10,60,6e5])
        screen=screen_reclaims(np.array(rows))
        for i in range(118,len(rows)):
            now=rows[i][0]+period+60_000
            for reverse in (False,True):
                context=series(CFG.context_ms,n=119,now=now,reverse=reverse)
                p=assess('TESTUSDT',rows[i-118:i+1],context,now)
                if p['status']=='SETUP':self.assertTrue(screen[i])

    def test_screen_does_not_read_future_prices(self):
        rows=np.array([r+[1e6,10,60,6e5] for r in series(CFG.entry_ms,n=160,now=START)])
        before=screen_reclaims(rows).copy();rows[145:,1:5]*=4
        np.testing.assert_array_equal(before[:145],screen_reclaims(rows)[:145])

    def test_discovery_is_deterministic_and_timing_controls_match_day_symbol_trend(self):
        now=START+DAY+60_000
        rows=series(CFG.entry_ms,n=1800,now=now)
        level=rows[-1][4]
        rows[-2][1:5]=[level-.7,level-.4,level-1.5,level-1.1]
        rows[-1][1:5]=[level-.4,level+.2,level-.6,level+.1]
        b=np.array([r+[1e6,10,60,6e5] for r in rows]);c=np.array(series(CFG.context_ms,n=300,now=now))
        a=Discovery('TESTUSDT',b,c,c).run();z=Discovery('TESTUSDT',b,c,c).run()
        self.assertEqual(a,z);self.assertGreaterEqual(len(a['links']),1)
        events={p['id']:p for p in a['events']}
        for original,links in a['links'].items():
            if links['random_timing']:
                p=events[original];r=events[links['random_timing']]
                self.assertEqual(p['observed_ms']//DAY,r['observed_ms']//DAY)
                self.assertEqual(p['symbol'],r['symbol']);self.assertEqual(p['direction'],r['direction'])
                self.assertNotEqual(p['candle_ms'],r['candle_ms'])

    def test_minute_path_detects_gap_instead_of_using_next_bar(self):
        rows=np.array([candle(START),candle(START+2*MINUTE)])
        p=MinutePath(rows)
        self.assertNotIn(START+MINUTE,p);self.assertIn(START+2*MINUTE,p)
        self.assertIsNone(p.get(START+MINUTE))
        self.assertEqual(p[START+2*MINUTE][0],START+2*MINUTE)

    def closed_trade(self,direction='LONG'):
        p=setup(direction);p.update(id='x',arm='strategy',quote_volume=1e7)
        t=p['entry_ms'];path={NOW:candle(NOW),t:candle(t,h=105 if direction=='LONG' else 101,l=99 if direction=='LONG' else 95)}
        rates=[[t-4*60*MINUTE,.0001,8],[t+4*60*MINUTE,.0001,8]]
        result=execute(p,path,rates,{})
        self.assertEqual(result['status'],'CLOSED')
        return p,path,result

    def test_cost_stress_reprices_same_event_without_readmission_or_resizing(self):
        for side in ('LONG','SHORT'):
            p,path,r=self.closed_trade(side);before=dict(r)
            stressed=stress_same_event(r,path)
            self.assertLess(stressed,r['net_r']);self.assertEqual(before,r)
            self.assertAlmostEqual(stress_same_event(r,path,1),r['net_r'])
            # Even an extremely expensive cost scenario remains measured, not removed.
            self.assertIsInstance(stress_same_event(r,path,100),float)
            self.assertLess(stress_same_event(r,path,100),0)

    def test_day_mean_weights_sparse_and_busy_days_equally(self):
        rows=[dict(id=str(i),symbol='A',status='CLOSED',entry_ms=START,net_r=1) for i in range(10)]
        rows.append(dict(id='b',symbol='B',status='CLOSED',entry_ms=START+DAY,net_r=-1))
        s=summary(rows);self.assertEqual(s['day_mean_r'],0);self.assertAlmostEqual(s['trade_mean_r'],9/11)
        self.assertEqual(s['entry_days'],2)

    def test_missing_days_are_not_imputed_as_zero_in_paired_comparison(self):
        a=[dict(id=str(i),status='CLOSED',entry_ms=START+i*DAY,net_r_1_5=1.) for i in range(100)]
        b=[dict(p,net_r_1_5=.5) for p in a[::2]]
        c=comparison(a,b,label='missing',iterations=2000)
        self.assertEqual(c['paired_entry_days'],50);self.assertEqual(c['left_only_days'],50)
        self.assertAlmostEqual(c['mean_day_difference_r'],.5)
        self.assertAlmostEqual(c['one_sided_adjusted_lower_bound'],.5)
        self.assertEqual(c,comparison(a,b,label='missing',iterations=2000))

    def test_unresolved_are_only_in_separate_sensitivity_cancellations_excluded(self):
        rows=[dict(id='a',symbol='A',status='CLOSED',entry_ms=START,net_r=1,net_r_1_5=.5),
              dict(id='b',symbol='A',status='OPEN_AT_HORIZON',entry_ms=START),
              dict(id='c',symbol='A',status='CANCELLED',entry_ms=START)]
        self.assertEqual(summary(rows)['closed'],1);self.assertEqual(summary(rows)['day_mean_r'],1)
        self.assertEqual(sensitivity(rows)['day_mean_r'],-.25)

    def test_top3_removed_by_total_r_with_fixed_ties(self):
        rows=[dict(id=s,symbol=s,status='CLOSED',entry_ms=START,net_r=r) for s,r in zip('ABCDE',[4,3,2,1,-3])]
        top,left=top_removed(rows);self.assertEqual(top,['A','B','C']);self.assertEqual([p['symbol'] for p in left],['D','E'])

    def test_oi_is_lagged_continuous_and_future_independent(self):
        records={START+i*5*MINUTE:100+i for i in range(50)};cutoff=START+48*5*MINUTE
        a=oi_at(records,sorted(records),cutoff)
        records[START+49*5*MINUTE]=100000
        self.assertEqual(a,oi_at(records,sorted(records),cutoff));self.assertAlmostEqual(a[0],.48)
        del records[START+5*MINUTE];self.assertIsNone(oi_at(records,sorted(records),cutoff)[0])

    def test_funding_rank_excludes_future_and_insufficient_history(self):
        rows=[dict(fundingTime=START+i*480*MINUTE,fundingRate=.001) for i in range(100)]
        cutoff=START+95*480*MINUTE
        a=funding_percentile(rows,cutoff);self.assertEqual(a['percentile'],.5)
        rows[-1]['fundingRate']=100;self.assertEqual(a,funding_percentile(rows,cutoff))
        self.assertIsNone(funding_percentile(rows[-10:],cutoff)['percentile'])

    def test_four_thresholds_separate_and_missing_stays_unknown(self):
        fields=dict(taker_buy_share=.56,oi_change_4h=.1,price_change_4h=-.01,funding_percentile_30d=.95,btc_relative_3d=.02)
        self.assertEqual(hypothesis_results(fields,'LONG'),dict(taker=True,oi=False,funding=False,relative=True))
        self.assertEqual(hypothesis_results({},'LONG'),dict(taker=None,oi=None,funding=None,relative=None))

    def test_oi_files_include_previous_day_for_4h_window(self):
        p=setup();p['observed_ms']=START+10*MINUTE;p['entry_ms']=START+11*MINUTE
        keys,months,dates=needed_files(dict(symbol='TESTUSDT',events=[p]))
        self.assertIn('2025-09-30',dates);self.assertIn('2025-10-01',dates)

    def zip_data(self,keyname,text):
        p=self.root/keyname;p.parent.mkdir(parents=True,exist_ok=True)
        with zipfile.ZipFile(p,'w') as z:z.writestr('rows.csv',text)
        p.with_suffix('.sha256').write_text(hashlib.sha256(p.read_bytes()).hexdigest())
        return p

    def test_archive_preserves_taker_and_rejects_wrong_month_and_bad_checksum(self):
        raw=[START,100,102,99,101,10,START+30*MINUTE-1,1000,20,6,600]
        path=self.zip_data(key('TESTUSDT','30m','2025-10'),','.join(map(str,raw))+'\n')
        rows,issues=read_rows(self.root,'TESTUSDT','30m',['2025-10'])
        self.assertFalse(issues);self.assertEqual(rows[0,10],600)
        path.with_suffix('.sha256').write_text('0'*64)
        rows,issues=read_rows(self.root,'TESTUSDT','30m',['2025-10'])
        self.assertEqual(len(rows),0);self.assertIn('CHECKSUM_MISMATCH',issues[0]['reason'])

    def test_synthetic_complete_outcome_pipeline(self):
        symbol='TESTUSDT';p=setup();p.update(symbol=symbol,observed_ms=START+MINUTE,entry_ms=START+2*MINUTE,
            candle_ms=START-30*MINUTE,id='pipeline',arm='strategy',quote_volume=1e7,
            features=dict(taker_buy_share=.6,price_change_4h=.01,btc_relative_3d=.02,btc_regime='UP'))
        out=self.root/'out';write_gzip(out/'candidates'/(symbol+'.json.gz'),dict(symbol=symbol,events=[p],links={p['id']:{}},data_issues=[]))
        self.zip_data(key(symbol,'1m','2025-10'),'\n'.join(','.join(map(str,r)) for r in [candle(START+MINUTE),candle(START+2*MINUTE,h=105,l=99)])+'\n')
        september=f'{START-480*MINUTE},8,0.0001\n'
        october='\n'.join(f'{START+i*480*MINUTE},8,0.0001' for i in range(3))+'\n'
        self.zip_data(key(symbol,'1m','2025-09','fundingRate'),september)
        self.zip_data(key(symbol,'1m','2025-10','fundingRate'),october)
        result=process_symbol((str(self.root),str(out),symbol,False))
        data=read_gzip(out/'outcomes'/(symbol+'.json.gz'));r=data['events'][0]
        self.assertEqual(r['status'],'CLOSED');self.assertLess(r['net_r_1_5'],r['net_r'])
        self.assertIsNone(r['hypotheses']['oi']);self.assertIsNone(r['hypotheses']['funding'])
        self.assertTrue(r['hypotheses']['taker']);self.assertTrue(data['data_issues'])


class DownloaderTests(unittest.IsolatedAsyncioTestCase):
    async def test_unicode_url_checksum_and_restart_cache(self):
        with tempfile.TemporaryDirectory() as root:
            d=Download(root);raw=b'valid mocked archive bytes';sha=hashlib.sha256(raw).hexdigest()
            d.request=AsyncMock(side_effect=[(sha+'  data.zip').encode(),raw])
            k=key('币安人生USDT','30m','2025-10');p=await d.file(k)
            self.assertEqual(p.read_bytes(),raw)
            self.assertIn('%E5',d.request.call_args_list[0].args[0]);self.assertNotIn('币',d.request.call_args_list[0].args[0])
            again=Download(root);again.request=AsyncMock();self.assertEqual(await again.file(k),p);again.request.assert_not_called()

    async def test_corrupt_download_never_becomes_cached_success(self):
        with tempfile.TemporaryDirectory() as root:
            d=Download(root);d.request=AsyncMock(side_effect=[b'0'*64,b'bad'])
            with self.assertRaisesRegex(ValueError,'CHECKSUM_MISMATCH'):await d.file(key('BTCUSDT','30m','2025-10'))
            self.assertFalse((Path(root)/key('BTCUSDT','30m','2025-10')).exists())


if __name__=='__main__':unittest.main()
