import copy
import itertools
import json
import math
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch, Mock

from long_short_chart_first import (CONFIG, PERIODS, STRATEGY_ID, assess, daily_from_4h,
                                    message, pivots, supporting_data)
from long_short_chart_first_worker import live_stage, path_outcome, record, connect, advance

NOW=1792065908000


def sample(short=False):
    frames={}
    for tf,n,slope,amp in [('5m',120,.015,.3),('15m',120,.025,.8),('1h',120,.06,2),('4h',220,.12,3)]:
        period=PERIODS[tf];end=NOW//period*period
        closes=[100+slope*(i-n+1)+amp*math.sin(i*math.pi/6) for i in range(n)]
        rows=[]
        for i,cl in enumerate(closes):
            op=closes[i-1] if i else cl;start=end-(n-i)*period
            rows.append([start,op,max(op,cl)+.08,min(op,cl)-.08,cl,100,start+period-1])
        frames[tf]=rows
    # A six-candle compression and fresh close through its top. The prior
    # larger-timeframe swings are far enough away for a cost-adjusted plan.
    for tf,delta in [('15m',10),('1h',20)]:
        for r in frames[tf]:
            for j in (1,2,3,4):r[j]+=delta
    for i,r in enumerate(frames['5m'][-7:-1]):
        r[1:5]=[100.48,100.60,100.40,100.49 if i%2 else 100.47]
    r=frames['5m'][-1];r[1:5]=[100.49,100.68,100.46,100.63]
    price=100.64
    if short:
        frames={tf:[[r[0],200-r[1],200-r[3],200-r[2],200-r[4],r[5],r[6]] for r in rows]
                for tf,rows in frames.items()}
        price=200-price
    return frames,price


class ChartFirstTests(unittest.TestCase):
    def test_long_and_short_use_identical_mirrored_logic(self):
        for short,side in [(False,'LONG'),(True,'SHORT')]:
            frames,price=sample(short)
            p=assess('TESTUSDT',frames,price,50e6,NOW)
            self.assertEqual((p['direction'],p['stage'],p['reason']),(side,'READY','OK'),p)
            self.assertEqual(set(p['context']),{'5m','15m','1h','4h','1d'})
            self.assertIn('GİRİŞ BÖLGESİ',message(p))
            self.assertTrue(message(p).startswith('🔴' if short else '🟢'))

    def test_every_auxiliary_field_can_be_missing_or_opposed_without_changing_chart(self):
        frames,price=sample()
        pairs=[('oi_change_1h','oi_fresh',-12),('taker_ratio','taker_fresh',.1),
               ('funding_pct','funding_fresh',.2),('long_short_ratio','ls_fresh',10)]
        original=assess('TESTUSDT',frames,price,50e6,NOW)
        for mask in itertools.product([False,True],repeat=4):
            e={}
            for (key,fresh,value),present in zip(pairs,mask):e.update({key:value,fresh:present})
            p=assess('TESTUSDT',frames,price,50e6,NOW,e)
            self.assertEqual([(p[k]) for k in ('direction','stage','trigger','stop','obstacle')],
                             [(original[k]) for k in ('direction','stage','trigger','stop','obstacle')])

    def test_stale_fields_are_unknown_not_neutral(self):
        self.assertTrue(all(v is None for v in supporting_data({'taker_ratio':1,'taker_fresh':False}).values()))

    def test_small_five_minute_drop_does_not_become_short_in_hourly_uptrend(self):
        frames,price=sample();opposite,_=sample(True);frames['5m']=opposite['5m']
        p=assess('TESTUSDT',frames,price,50e6,NOW)
        self.assertNotEqual(p['direction'],'SHORT')

    def test_daily_is_exact_complete_utc_aggregation(self):
        frames,_=sample();rows=frames['4h'];days=daily_from_4h(rows)
        for day in days:
            parts=[r for r in rows if day[0]<=r[0]<=day[6]]
            self.assertEqual(len(parts),6)
            self.assertEqual(day[1],parts[0][1]);self.assertEqual(day[4],parts[-1][4])
            self.assertEqual(day[2],max(r[2] for r in parts));self.assertEqual(day[5],sum(r[5] for r in parts))
        damaged=[r for r in rows if r[0]!=days[-1][0]]
        self.assertNotIn(days[-1][0],[r[0] for r in daily_from_4h(damaged)])

    def test_partial_current_day_is_not_used(self):
        frames,_=sample();self.assertLess(daily_from_4h(frames['4h'])[-1][6],NOW//86400000*86400000)

    def test_chart_gap_stale_and_invalid_quotes_fail(self):
        frames,price=sample()
        bad=copy.deepcopy(frames);bad['1h'].pop(20)
        self.assertEqual(assess('XUSDT',bad,price,50e6,NOW)['reason'],'CHART_GAP_1h')
        self.assertEqual(assess('XUSDT',frames,price,50e6,NOW+600000)['reason'],'CHART_STALE_5m')
        for price in (None,float('nan'),-1):
            self.assertEqual(assess('XUSDT',frames,price,50e6,NOW)['reason'],'FRESH_PRICE_UNAVAILABLE')

    def test_late_entry_does_not_generate_a_message(self):
        frames,price=sample();p=assess('XUSDT',frames,price+2,50e6,NOW)
        self.assertEqual(p['reason'],'ENTRY_ALREADY_EXTENDED')

    def test_countertrend_needs_hourly_break_not_just_five_minute_strength(self):
        frames,price=sample()
        opposite,_=sample(True);frames['4h']=opposite['4h']
        p=assess('XUSDT',frames,price,50e6,NOW)
        self.assertEqual(p['reason'],'REVERSAL_HOURLY_FLOW_UNAVAILABLE')

    def test_displayed_entry_region_still_has_room_for_costs(self):
        for short in (False,True):
            frames,price=sample(short);p=assess('XUSDT',frames,price,50e6,NOW)
            sign=-1 if short else 1
            self.assertGreaterEqual(sign*(p['obstacle']-p['entry_limit'])/p['entry_limit']*100+1e-10,
                                    CONFIG['round_trip_cost_pct'])

    def test_chart_notifications_do_not_merge_old_opposite_calls(self):
        import long_short_message_card as card
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,
             {'LS_MESSAGE_CARD_DB':directory+'/cards.db','LS_CHART_FIRST_V1':'1','LS_TELEGRAM_EXPECTED_CHAT_ID':'123'}):
            post=Mock(return_value=Mock(json=lambda:{'ok':True,'result':{'message_id':42,'chat':{'id':123}}}))
            self.assertEqual(card.publish('1:token','123','XUSDT','CORE','old','SHORT',post=post)['status'],'LEGACY_REFERENCE_ONLY')
            post.assert_not_called()
            for key,text in [('e:WATCH','watch'),('e:READY','ready'),('e:READY','ended')]:
                card.publish('1:token','123','XUSDT','CHART',text,'LONG',episode_key=key,post=post)
            self.assertEqual([x.args[0].rsplit('/',1)[-1] for x in post.call_args_list],
                             ['sendMessage','sendMessage','editMessageText'])
            self.assertEqual(post.call_args.kwargs['json']['text'],'ended')

    def test_low_liquidity_remains_explicit(self):
        frames,price=sample()
        self.assertEqual(assess('XUSDT',frames,price,1e6,NOW)['reason'],'LIQUIDITY_FLOOR')

    def test_locked_plan_does_not_move_to_a_farther_target(self):
        p=dict(direction='LONG',trigger=100,stop=98,obstacle=102,entry_limit=101,
               created_ms=NOW,stage='WATCH')
        self.assertEqual(live_stage(p,102.1,100.2,NOW-8000,NOW),'OBSTACLE_REACHED')
        self.assertEqual(live_stage(p,101.2,100.2,NOW-8000,NOW),'EXPIRED')
        self.assertEqual(live_stage(p,100.2,100.1,NOW-8000,NOW),'READY')
        self.assertEqual(live_stage(p,100.2,100.1,NOW-90000,NOW),'WATCH')

    def test_short_stop_and_obstacle_are_mirrored(self):
        p=dict(direction='SHORT',trigger=100,stop=102,obstacle=98,entry_limit=99,
               created_ms=NOW,stage='READY')
        self.assertEqual(live_stage(p,102.1,100,NOW-8000,NOW),'BROKEN')
        self.assertEqual(live_stage(p,97.9,100,NOW-8000,NOW),'OBSTACLE_REACHED')

    def test_same_minute_path_is_stop_first_and_gap_is_unresolved(self):
        p=dict(price=100,direction='LONG',stop=98,obstacle=102)
        bars=[[60000,100,103,97,102,1,119999]]
        result=path_outcome(p,bars,60000,1)
        self.assertEqual(result['first_level'],'INVALIDATION');self.assertTrue(result['same_minute_ambiguous'])
        with self.assertRaises(ValueError):path_outcome(p,bars,60000,2)

    def test_rejected_snapshots_are_retained_and_duplicate_poll_is_not_another_case(self):
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'LS_STATE_DIR':directory}):
            c=connect();frames,price=sample();p=assess('XUSDT',frames,price+2,50e6,NOW)
            record(c,p);record(c,p);c.commit()
            self.assertEqual(c.execute('SELECT COUNT(*) FROM assessments').fetchone()[0],1)
            self.assertEqual(c.execute('SELECT reason FROM assessments').fetchone()[0],'ENTRY_ALREADY_EXTENDED')
            self.assertEqual(c.execute('SELECT id FROM manifests').fetchone()[0],STRATEGY_ID)
            c.close()

    def test_pending_ready_is_revalidated_and_unseen_expiry_does_not_notify(self):
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'LS_STATE_DIR':directory}),patch('time.time',return_value=NOW/1000):
            c=connect();k=sqlite3.connect(':memory:')
            k.executescript('''CREATE TABLE futures_last_price(symbol,price,event_ms,received_ms,source);
              CREATE TABLE futures_closed_klines(symbol,interval,close,close_ms,open_ms);''')
            k.execute('INSERT INTO futures_last_price VALUES(?,?,?,?,?)',('XUSDT',101.5,NOW-100,NOW-100,'BINANCE_UM_FUTURES_WEBSOCKET'))
            k.execute('INSERT INTO futures_closed_klines VALUES(?,?,?,?,?)',('XUSDT','5m',100.2,NOW-8000,NOW-308000))
            p=dict(strategy_id=STRATEGY_ID,stage='READY',created_ms=NOW-1000,price=100.2,
                   symbol='XUSDT',direction='LONG',trigger=100,stop=98,obstacle=103,entry_limit=101)
            c.execute('INSERT INTO episodes VALUES(?,?,?,?,?,?,?,?)',('e','XUSDT','LONG',NOW-1000,'READY',None,None,json.dumps(p)));c.commit()
            publish=Mock()
            advance(c,k,{},NOW,publish,'token','123')
            publish.assert_not_called()
            self.assertEqual(c.execute('SELECT stage FROM episodes').fetchone()[0],'EXPIRED')
            self.assertEqual(c.execute('SELECT COUNT(*) FROM assessments').fetchone()[0],0)
            c.close();k.close()

    def test_ready_refreshes_support_but_keeps_original_levels(self):
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'LS_STATE_DIR':directory}),patch('time.time',return_value=NOW/1000):
            c=connect();k=sqlite3.connect(':memory:')
            k.executescript('''CREATE TABLE futures_last_price(symbol,price,event_ms,received_ms,source);
              CREATE TABLE futures_closed_klines(symbol,interval,close,close_ms,open_ms);''')
            frames,price=sample();p=assess('XUSDT',frames,price,50e6,NOW)
            p.update(stage='WATCH',created_ms=NOW-60000)
            k.execute('INSERT INTO futures_last_price VALUES(?,?,?,?,?)',('XUSDT',price,NOW-100,NOW-100,'BINANCE_UM_FUTURES_WEBSOCKET'))
            k.execute('INSERT INTO futures_closed_klines VALUES(?,?,?,?,?)',('XUSDT','5m',price,NOW-8000,NOW-308000))
            c.execute('INSERT INTO episodes VALUES(?,?,?,?,?,?,?,?)',('e','XUSDT','LONG',NOW-60000,'WATCH','WATCH',None,json.dumps(p)));c.commit()
            decision=dict(p,stage='READY',stop=99,support={'oi_change_1h':8})
            publisher=Mock(return_value={'status':'ACK'})
            advance(c,k,{'XUSDT':decision},NOW,publisher,'token','123')
            saved=json.loads(c.execute('SELECT payload FROM episodes').fetchone()[0])
            self.assertEqual(saved['stop'],p['stop']);self.assertEqual(saved['support']['oi_change_1h'],8)
            self.assertEqual(c.execute('SELECT delivery_stage FROM episodes').fetchone()[0],'READY')
            self.assertEqual(c.execute('SELECT stage FROM assessments').fetchone()[0],'DELIVERED_READY')
            c.close();k.close()


if __name__=='__main__':unittest.main()
