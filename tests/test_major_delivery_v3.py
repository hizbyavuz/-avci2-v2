import copy
import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock,patch

import long_short_major_move as core
import long_short_major_move_store as store
import long_short_major_move_worker as worker
import long_short_major_delivery as delivery
import long_short_early_observer as early
import long_short_major_context_report as report
import test_major_move as fixtures
from test_major_context_entry_v2 import ready

NOW=fixtures.NOW


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.env=patch.dict(os.environ,{'LS_STATE_DIR':self.tmp.name})
        self.env.start();self.c=store.connect()

    def tearDown(self):self.c.close();self.env.stop();self.tmp.cleanup()

    def insert(self,symbol,at,state='TRACKING',delivered=True):
        p=ready();p.update(symbol=symbol,observed_ms=at,expires_ms=NOW+72*3600000)
        self.c.execute('INSERT INTO ideas VALUES(?,?,?,?,?,?,?,?)',
            (symbol,symbol,at,at if delivered else None,state,json.dumps(p),json.dumps(store.initial_progress(p)),at))
        self.c.commit()

    def test_twelve_old_open_plans_do_not_block_and_uncertain_send_consumes_budget(self):
        for i in range(12):self.insert(str(i)+'USDT',NOW-3600000)
        self.assertIsNone(worker.eligible_to_publish(self.c,ready(),NOW))
        self.insert('ONEUSDT',NOW-1000);self.insert('TWOUSDT',NOW-999)
        self.insert('THREEUSDT',NOW-998,'DELIVERY_UNCERTAIN',False)
        self.assertEqual(worker.eligible_to_publish(self.c,ready(),NOW),'NOTIFICATION_RATE_LIMIT')
        # Persistent SQL budget survives a new connection; exact rolling expiry.
        self.c.close();self.c=store.connect()
        self.assertEqual(worker.eligible_to_publish(self.c,ready(),NOW),'NOTIFICATION_RATE_LIMIT')
        self.assertIsNone(worker.eligible_to_publish(self.c,ready(),NOW+899000))

    def test_first_eligibility_survives_restart_direction_change_and_new_strategy_id(self):
        p=ready();delivery.attach_first_eligibility(self.c,p);self.c.commit()
        self.c.close();self.c=store.connect()
        later=dict(p,observed_ms=NOW+7200000,price=110.,strategy_id='new-version')
        delivery.attach_first_eligibility(self.c,later)
        self.assertEqual(later['first_eligibility']['observed_ms'],NOW)
        self.assertAlmostEqual(later['move_since_first_eligibility_pct'],(110/100.15-1)*100)
        self.assertIn('120 dk önce',core.message(later))
        short=dict(later,direction='SHORT');delivery.attach_first_eligibility(self.c,short)
        self.assertEqual(short['first_eligibility']['price'],110.)
        renewed=dict(p,observed_ms=NOW+86400000,price=120.)
        delivery.attach_first_eligibility(self.c,renewed)
        self.assertEqual(renewed['first_eligibility']['price'],120.)

    def test_v2_paper_origin_seeds_v3_without_rewriting_the_record(self):
        old=ready();old['version']='MAJOR_CONTEXT_ENTRY_V2_2026_10_16'
        store.record_research(self.c,old,'old');self.c.commit()
        before=self.c.execute('SELECT payload FROM research_ideas').fetchone()[0]
        p=dict(ready(),observed_ms=NOW+7200000,price=105.)
        delivery.attach_first_eligibility(self.c,p)
        self.assertEqual(p['first_eligibility']['price'],old['price'])
        self.assertEqual(p['first_eligibility']['source_version'],old['version'])
        self.assertEqual(before,self.c.execute('SELECT payload FROM research_ideas').fetchone()[0])

    def test_observer_cannot_publish_and_expired_candidate_never_sends(self):
        pub=Mock();p=dict(ready(),observation_only=True)
        self.assertEqual(worker.publish_candidate(self.c,None,p,'observer',pub,'token','123',NOW),'OBSERVATION_ONLY')
        p=ready();late=p['entry_valid_until_ms']
        with patch.object(worker,'quote',return_value=p['price']),patch.object(worker.time,'time',return_value=late/1000):
            self.assertEqual(worker.publish_candidate(self.c,None,p,'current',pub,'token','123',late),'CONFIRMATION_EXPIRED')
        pub.assert_not_called()

    def test_cooling_top_three_cannot_starve_fourth_ranked_candidate(self):
        k=sqlite3.connect(':memory:');k.execute('CREATE TABLE futures_closed_klines(symbol,interval,open_ms,close_ms)')
        scanner=worker.CandleScanner();scanner.refresh_at=float('inf')
        for i in range(4):
            p=dict(ready(),symbol=f'COIN{i}USDT');scanner.pending[p['symbol']]=p
            scanner.items.append((p['symbol'],1e7,p['price'],1.))
            scanner.watermarks[p['symbol']]=None
        with patch.object(worker.time,'time',return_value=NOW/1000),patch.object(worker,'publish_candidate',side_effect=['COOLDOWN']*3+['DELIVERED']) as send:
            scanner.run(self.c,k,'current',publisher=Mock())
        self.assertEqual(send.call_count,4);self.assertFalse(scanner.pending);k.close()

    def test_legacy_message_keeps_original_version_and_context(self):
        p=ready();p['version']='MAJOR_CONTEXT_ENTRY_V2_2026_10_16'
        self.assertIn('MAJOR V2',core.message(p));self.assertIn('Büyük yapı:',core.message(p))


def early_rows(side='LONG'):
    rows=fixtures.bars(300000,96)
    for r in rows:r[2:4]=[101.,99.]
    for r in rows[-13:-1]:r[1:5]=[99.8,100.,99.5,99.8]
    rows[-1][1:6]=[99.8,100.2,99.7,100.15,600.]
    if side=='SHORT':
        for r in rows:r[1:5]=[200-r[1],200-r[3],200-r[2],200-r[4]]
    return rows


class EarlyObserverTests(unittest.TestCase):
    def assess(self,side='LONG',rows=None,now=NOW,price=None):
        rows=rows if rows is not None else early_rows(side)
        return early.assess('TESTUSDT',rows,fixtures.bars(300000,96),price or rows[-1][4],1e7,now)

    def test_symmetric_early_start_without_prior_major_leg(self):
        for side in ('LONG','SHORT'):
            p=self.assess(side)
            self.assertEqual((p['stage'],p['direction']),('READY',side))
            self.assertTrue(p['observation_only']);self.assertFalse(p['trailing_enabled'])
            self.assertEqual(p['measurement_hours'],3)
            self.assertLess(abs(p['features']['return_15m_pct']),1.)

    def test_missing_stale_future_gap_unaligned_and_late_price_fail_closed(self):
        self.assertEqual(self.assess(rows=early_rows()[:-1])['reason'],'HISTORY_MISSING_5m')
        self.assertEqual(self.assess(now=NOW+300000)['reason'],'STALE_5m')
        bad=early_rows();bad[-1][6]=NOW+1
        self.assertNotEqual(self.assess(rows=bad)['stage'],'READY')
        bad=early_rows();bad[4][0]+=1
        self.assertEqual(self.assess(rows=bad)['reason'],'INVALID_5m')
        self.assertEqual(self.assess(price=102.)['reason'],'LATE_CHASE')
        bad=early_rows();bad[-1][5]=100.
        self.assertEqual(self.assess(rows=bad)['reason'],'NO_VOLUME_EXPANSION')

    def test_scanner_persists_paper_only_and_restart_never_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'LS_STATE_DIR':tmp}):
            c=store.connect();k=sqlite3.connect(':memory:')
            k.execute('CREATE TABLE futures_closed_klines(symbol,interval,open_ms,open,high,low,close,volume,close_ms,source)')
            for symbol,rows in [('TESTUSDT',early_rows()),('BTCUSDT',fixtures.bars(300000,96))]:
                k.executemany('INSERT INTO futures_closed_klines VALUES(?,?,?,?,?,?,?,?,?,?)',
                    [(symbol,'5m',*r,fixtures.SOURCE) for r in rows])
            with patch.object(early.time,'time',return_value=NOW/1000),patch.object(early,'quote',return_value=100.15):
                for _ in range(2):early.EarlyScanner().run(c,k,[('TESTUSDT',1e7,100.15,1.)])
            self.assertEqual(c.execute('SELECT COUNT(*) FROM ideas').fetchone()[0],0)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM research_ideas').fetchone()[0],1)
            p=json.loads(c.execute('SELECT payload FROM research_ideas').fetchone()[0])
            self.assertEqual(p['evaluation_hours'],[.25,.5,1,2,3])
            c.close();k.close()


class MeasurementTests(unittest.TestCase):
    def test_new_two_hour_small_milestones_and_old_contract_preserved(self):
        old=fixtures.Paths().plan();new=store.measurement_contract(copy.deepcopy(old),3)
        bars=[(60000+i*60000,100.,103.1,99.,102.,119999+i*60000,fixtures.SOURCE) for i in range(180)]
        legacy_g,legacy=store.advance_path(old,store.initial_progress(old),bars)
        g,results=store.advance_path(new,store.initial_progress(new),bars)
        self.assertNotIn(2,[x['horizon_hours'] for x in legacy])
        self.assertNotIn('3',legacy_g['milestones'])
        self.assertTrue(all(x['outcome_version']==store.LEGACY_OUTCOME_VERSION for x in legacy))
        self.assertEqual([x['horizon_hours'] for x in results],[.25,.5,1,2,3])
        self.assertIn('3',g['milestones']);self.assertNotIn('5',g['milestones'])
        self.assertEqual(g['cursor_ms'],60000+180*60000)

    def test_no_old_two_hour_gap_or_early_twentyfour_hour_fake_gap_in_report(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'LS_STATE_DIR':tmp}):
            c=store.connect();p=fixtures.Paths().plan();p.update(symbol='TESTUSDT',strategy_id='legacy')
            c.execute('INSERT INTO research_ideas VALUES(?,?,?,?,?,?,?,?)',('old','TESTUSDT',60000,60000,'TRACKING',json.dumps(p),'{}',0))
            p=store.measurement_contract(copy.deepcopy(p),3);p['strategy_id']='early'
            c.execute('INSERT INTO research_ideas VALUES(?,?,?,?,?,?,?,?)',('new','TESTUSDT',60000,60000,'DONE',json.dumps(p),'{}',0))
            result=report.report(c,now_ms=100*3600000,since_ms=0,horizons=[2,24],paper=True)
            self.assertEqual({x['strategy_id'] for x in result['horizons'][0]['groups']},{'early'})
            self.assertEqual({x['strategy_id'] for x in result['horizons'][1]['groups']},{'legacy'})
            c.close()
