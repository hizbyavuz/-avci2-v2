import copy
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import long_short_breakout_observer as ob
import long_short_futures_kline_shadow as feed
import long_short_analyst as analyst


def snapshot(direction='LONG'):
    def bars(step):
        out=[]
        for i in range(30):
            start=i*step
            out.append({'open_ms':start,'close_ms':start+step-1,
                'open':99.9,'high':100.0,'low':99.6,'close':99.9,
                'volume':1,'quote':100,'buy_quote':50,'source':feed.SOURCE})
        return out
    one,five=bars(60000),bars(300000)
    # Prior compression box, with meaningful historical room beyond it.
    for b in five[:10]: b.update(high=102,low=98)
    one[-1].update(open=99.9,close=100.04,high=100.06,quote=150,buy_quote=100)
    s={'1m':one,'5m':five,'price':100.04,'price_event_ms':1800000}
    if direction=='SHORT':
        # Reflect all prices about 99.8 to test the same structural rules.
        for series in (s['1m'],s['5m']):
            for b in series:
                b['open'],b['close']=199.6-b['open'],199.6-b['close']
                b['high'],b['low']=199.6-b['low'],199.6-b['high']
                b['buy_quote']=b['quote']-b['buy_quote']
        s['price']=199.6-s['price']
    return s


class ObserverTests(unittest.TestCase):
    def test_symmetric_early_long_and_short_without_core_admission(self):
        for d in ('LONG','SHORT'):
            state,f=ob.evaluate(d,snapshot(d),now_ms=10000000)
            self.assertEqual(state,'EARLY',f)
            self.assertTrue(f['observation_only'])
            self.assertEqual(f['config_hash'],ob.CONFIG_HASH)

    def test_volume_baseline_excludes_signal_candle(self):
        self.assertAlmostEqual(ob.features(snapshot())['volume_mult'],1.5)

    def test_reference_excludes_evaluated_5m_candle(self):
        s=snapshot();s['5m'][-1]['high']=120
        self.assertEqual(ob.features(s)['local_high'],100)

    def test_weak_volume_or_wrong_flow_never_early(self):
        for quote,buy in ((100,70),(150,20)):
            s=snapshot();s['1m'][-1].update(quote=quote,buy_quote=buy)
            self.assertNotEqual(ob.evaluate('LONG',s,now_ms=10000000)[0],'EARLY')

    def test_extended_moves_mark_chase(self):
        for d in ('LONG','SHORT'):
            s=snapshot(d);s['price']+=1 if d=='LONG' else -1
            self.assertEqual(ob.evaluate(d,s,now_ms=10000000)[0],'CHASE')

    def test_near_obstacle_is_not_early(self):
        s=snapshot()
        for b in s['5m'][:10]: b['high']=100.1
        self.assertEqual(ob.evaluate('LONG',s,now_ms=10000000)[0],'CHASE')

    def test_missing_resistance_is_labeled_unknown_without_invented_target(self):
        s=snapshot()
        for b in s['5m'][:10]:b['high']=100
        state,f=ob.evaluate('LONG',s,now_ms=10000000)
        self.assertEqual(state,'EARLY');self.assertIsNone(f['known_room_pct'])
        self.assertNotIn('target1',f)

    def test_setup_timeout_uses_milliseconds(self):
        s=snapshot()
        old={'level':100,'invalidation':99.6,'started_ms':0,'early_ms':1000,'state':'EARLY'}
        self.assertNotEqual(ob.evaluate('LONG',s,old,now_ms=120000)[0],'EXPIRED')
        self.assertEqual(ob.evaluate('LONG',s,old,now_ms=10800000)[0],'EXPIRED')

    def test_preparing_episode_can_be_evaluated_before_first_early(self):
        old={'level':100,'invalidation':99.6,'started_ms':10000000,'early_ms':None,'state':'PREPARING'}
        self.assertEqual(ob.evaluate('LONG',snapshot(),old,now_ms=10000015)[0],'EARLY')

    def test_confirmation_requires_a_new_closed_bar_and_quality(self):
        s=snapshot();last=s['5m'][-1]
        last.update(open=99.9,low=99.88,high=100.12,close=100.1,quote=150)
        old={'level':100,'invalidation':99.6,'started_ms':8000000,
             'early_ms':last['close_ms']-300000,'state':'EARLY'}
        self.assertEqual(ob.evaluate('LONG',s,old,now_ms=10000000)[0],'CONFIRMED')
        old['early_ms']=last['close_ms']+1
        self.assertNotEqual(ob.evaluate('LONG',s,old,now_ms=10000000)[0],'CONFIRMED')

    def test_breakout_return_needs_two_post_signal_closes(self):
        s=snapshot();old={'level':100,'invalidation':99.6,'started_ms':1000000,'early_ms':1000000,'state':'EARLY'}
        for b in s['1m'][-2:]:b['close']=99.9
        self.assertEqual(ob.evaluate('LONG',s,old,now_ms=2000000)[0],'BROKEN')

    def test_episode_records_only_first_early_price_and_post_signal_changes(self):
        with tempfile.TemporaryDirectory() as root:
            c=ob.initialize_state(str(Path(root)/'state.db'))
            first=ob.observe(c,'TESTUSDT',snapshot(),10000000)
            self.assertEqual(len(first),1)
            s=snapshot();s['price']=100.06
            ob.observe(c,'TESTUSDT',s,10015000)
            row=c.execute("SELECT * FROM episodes WHERE direction='LONG'").fetchone()
            self.assertEqual(row['early_price'],100.04)
            self.assertEqual(row['early_ms'],10000000)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM transitions WHERE new_state='EARLY'").fetchone()[0],1)
            c.close()

    def test_all_eligible_universe_is_not_the_rotating_thirty_coin_cap(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ,{'LS_STATE_DIR':root,'LS_NATIVE_CHAIN_V2':'0'}), \
                patch.object(analyst.time,'time',return_value=1000):
            with sqlite3.connect(str(Path(root)/'binance_futures_universe.db')) as c:
                c.execute('CREATE TABLE futures_tickers(symbol,quote_volume,last_price,change_pct,received_ms,source)')
                c.executemany('INSERT INTO futures_tickers VALUES(?,?,?,?,?,?)',
                    [(f'C{i:03d}USDT',1e9,1,0,1000000,feed.SOURCE) for i in range(187)])
            self.assertEqual(len(analyst.native_futures_websocket_universe(quiet=True,all_eligible=True)),187)
            self.assertEqual(len(analyst.native_futures_websocket_universe(quiet=True)),analyst.MAX_SYMBOLS)

    def test_messages_separate_candle_confirmation_from_trade_entry(self):
        _,f=ob.evaluate('LONG',snapshot(),now_ms=10000000)
        msg=ob.format_update({'symbol':'TESTUSDT','direction':'LONG','state':'CONFIRMED',
                            'features':f,'early_ms':10000000,'early_price':100.04},10015000)
        self.assertIn('onaylı işlem girişi değildir',msg)
        self.assertIn('ana motorun giriş onayı bekleniyor',msg)
        self.assertNotIn('SL:',msg);self.assertNotIn('TP1:',msg)

    def test_fresh_breakout_prioritizes_existing_scan_slots_without_unverified_symbols(self):
        with tempfile.TemporaryDirectory() as root, patch.object(analyst.time,'time',return_value=10000):
            c=ob.initialize_state(str(Path(root)/'long_short_breakout_observer.db'))
            c.executemany('''INSERT INTO episodes(symbol,direction,state,early_ms,config_version)
                VALUES(?,?,?,?,?)''',[
                ('FRESHUSDT','LONG','EARLY',9999000,ob.CONFIG['version']),
                ('UNKNOWNUSDT','LONG','EARLY',9999999,ob.CONFIG['version']),
                ('OLDUSDT','SHORT','EARLY',9000000,ob.CONFIG['version'])]);c.commit();c.close()
            eligible=[(s,1e9,1,0) for s in ['FRESHUSDT','OLDUSDT','AUSDT','BUSDT']]
            window=eligible[2:]
            result=analyst.prioritize_early_observations(eligible,window,2,root,quiet=True)
            self.assertEqual([r[0] for r in result],['FRESHUSDT','AUSDT'])
            self.assertEqual(len(result),2)

    def test_full_feed_partitions_do_not_silently_drop_or_repeat_symbols(self):
        symbols=[f'C{i:03d}USDT' for i in range(745)]
        chunks=ob.partition_symbols(symbols)
        self.assertEqual([len(c) for c in chunks],[400,345])
        self.assertEqual(set(chunks[0]+chunks[1]),set(symbols))
        self.assertFalse(set(chunks[0])&set(chunks[1]))
        with self.assertRaisesRegex(RuntimeError,'CAPACITY'):
            ob.partition_symbols([f'C{i:03d}USDT' for i in range(801)])

    def test_valid_zero_range_does_not_fabricate_atr_or_emit_early(self):
        s=snapshot()
        for b in s['1m']:b.update(open=100,high=100,low=100,close=100)
        status,f=ob.evaluate('LONG',s,now_ms=10000000)
        self.assertNotEqual(status,'EARLY')
        self.assertIsNone(f['extension_atr'])
        json.dumps(f,allow_nan=False)


class DataHealthTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.c=feed.initialize(str(Path(self.temp.name)/'candles.db'))
        self.now=9000250
        for iv,step in (('1m',60000),('5m',300000)):
            rows=[]
            for start in range(9000000-30*step,9000000,step):
                rows.append(('TESTUSDT',iv,start,start+step-1,100,101,99,100,1,
                             feed.SOURCE,9000000,100,1,50))
            feed.upsert_rows(self.c,rows)
        self.c.execute('INSERT INTO futures_last_price VALUES(?,?,?,?,?)',('TESTUSDT',100,9000000,9000000,feed.SOURCE));self.c.commit()

    def tearDown(self):self.c.close();self.temp.cleanup()

    def test_fresh_verified_closed_snapshot_passes(self):
        self.assertEqual(len(ob.read_snapshot(self.c,'TESTUSDT',self.now)['1m']),30)

    def test_missing_stale_wrong_venue_and_future_price_fail_closed(self):
        for event,source in ((8800000,feed.SOURCE),(9000251,feed.SOURCE),(9000000,'BINANCE_SPOT')):
            self.c.execute('UPDATE futures_last_price SET event_ms=?,source=?',(event,source));self.c.commit()
            with self.assertRaisesRegex(ValueError,'LIVE_PRICE_UNAVAILABLE'):ob.read_snapshot(self.c,'TESTUSDT',self.now)

    def test_gapped_candles_are_not_replaced_by_last_price(self):
        self.c.execute("DELETE FROM futures_closed_klines WHERE interval='1m' AND open_ms=8400000");self.c.commit()
        with self.assertRaises(ValueError):ob.read_snapshot(self.c,'TESTUSDT',self.now)

    def test_forming_candle_is_excluded(self):
        feed.upsert_rows(self.c,[('TESTUSDT','1m',9000000,9059999,100,999,99,100,1,feed.SOURCE,9000000,100,1,50)])
        self.c.commit()
        snap=ob.read_snapshot(self.c,'TESTUSDT',self.now)
        self.assertLess(snap['1m'][-1]['close_ms'],self.now)
        self.assertEqual(max(b['high'] for b in snap['1m']),101)

    def test_invalid_taker_volume_rejected(self):
        self.c.execute("UPDATE futures_closed_klines SET taker_buy_quote=200 WHERE interval='1m'");self.c.commit()
        with self.assertRaises(ValueError):ob.read_snapshot(self.c,'TESTUSDT',self.now)


class OutcomeTests(unittest.TestCase):
    def test_pre_signal_and_partial_signal_minute_never_count(self):
        with tempfile.TemporaryDirectory() as root:
            c=ob.initialize_state(str(Path(root)/'state.db'))
            candles=feed.initialize(str(Path(root)/'candles.db'))
            c.execute('''INSERT INTO episodes(symbol,direction,state,level,invalidation,started_ms,early_ms,
                early_price,first_anomaly_ms,first_anomaly_price,config_hash) VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                ('TESTUSDT','LONG','EARLY',100,99,0,15000,100,0,90,ob.CONFIG_HASH));c.commit()
            bars=[('TESTUSDT','1m',0,59999,100,150,90,100,1,feed.SOURCE,60000,100,1,50)]
            bars += [('TESTUSDT','1m',t,t+59999,100,103,99,102,1,feed.SOURCE,t+60000,100,1,50)
                     for t in range(60000,960000,60000)]
            feed.upsert_rows(candles,bars);candles.commit()
            ob.label_outcomes(c,candles,960250)
            row=c.execute('SELECT * FROM outcomes WHERE horizon_seconds=900').fetchone()
            self.assertEqual(row['state'],'RESOLVED')
            self.assertAlmostEqual(row['mfe_pct'],3)
            self.assertAlmostEqual(row['mae_pct'],-1)
            hits=json.loads(row['reach_json'])
            self.assertTrue(hits['3']['reached']);self.assertFalse(hits['5']['reached'])
            c.close();candles.close()

    def test_missing_path_remains_unresolved(self):
        with tempfile.TemporaryDirectory() as root:
            c=ob.initialize_state(str(Path(root)/'state.db')); candles=feed.initialize(str(Path(root)/'candles.db'))
            c.execute('INSERT INTO episodes(symbol,direction,early_ms,early_price) VALUES(?,?,?,?)',('TESTUSDT','SHORT',0,100));c.commit()
            ob.label_outcomes(c,candles,10800250)
            self.assertEqual({r[0] for r in c.execute('SELECT state FROM outcomes')},{'DATA_PENDING'})
            c.close();candles.close()


class DeliveryTests(unittest.TestCase):
    def test_failed_delivery_retries_without_new_transition(self):
        with tempfile.TemporaryDirectory() as root:
            c=ob.initialize_state(str(Path(root)/'state.db'));ob.observe(c,'TESTUSDT',snapshot(),10000000)
            with patch('long_short_message_card.publish',side_effect=RuntimeError('temporary')):
                ob.deliver_pending(c,'token','chat',10000015)
            with patch('long_short_message_card.publish',return_value={'status':'ACK'}) as send:
                ob.deliver_pending(c,'token','chat',10000030)
                self.assertEqual(send.call_count,1)
            self.assertEqual(c.execute("SELECT delivery_status FROM transitions WHERE new_state='EARLY'").fetchone()[0],'ACK')
            c.close()
