"""Regression cases for the production partial-scan / false-READY incident."""
from collections import Counter
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ta_paper.core import CFG, MINUTE
from ta_paper.io import Store, Binance, DataUnavailable
from ta_paper.runner import scan, update_health, run, STOP, SCAN_STATE_KEY
from test_ta_paper import NOW, MARKET, series
from test_ta_paper_operations import market_response


class FakeClient:
    def __init__(self, n=3, fail_symbol=None, setups=False):
        self.stamp=NOW; self.blocked=False; self.fail_symbol=fail_symbol
        self.symbols=[f'S{i:03}USDT' for i in range(n)]
        self.calls=Counter(); self.setups=setups; self.book_fail=False
        self.gate=SimpleNamespace(state=lambda:dict(remaining_seconds=60 if self.blocked else 0,
            until_ms=self.stamp+MINUTE if self.blocked else 0,reason='SHARED_IP_WEIGHT_HIGH',source='WEIGHT_HEADER'))
    def now(self):return self.stamp
    def sync_time(self):
        if self.blocked:raise DataUnavailable('EXCHANGE_COOLDOWN')
    def get(self,path,**kwargs):
        self.calls[path]+=1
        if path.endswith('exchangeInfo'):
            return {'symbols':[dict(MARKET,symbol=s) for s in self.symbols]}
        if path.endswith('24hr'):
            return [dict(symbol=s,quoteVolume='3000000',closeTime=self.stamp) for s in self.symbols]
        if self.book_fail:
            self.blocked=True;self.book_fail=False
            raise DataUnavailable('BINANCE_HTTP_429')
        return dict(E=self.stamp,bids=[['104.99','100']],asks=[['105.01','100']])
    def candles(self,symbol,tf,*args,**kwargs):
        self.calls[(symbol,tf)]+=1
        if self.blocked:raise AssertionError('network attempted during cooldown')
        if symbol==self.fail_symbol:
            self.fail_symbol=None;self.blocked=True
            raise DataUnavailable('BINANCE_HTTP_418')
        period=CFG.context_ms if tf=='4h' else CFG.entry_ms
        bars=series(period,now=self.stamp)
        shift=(self.stamp//period-NOW//period)*.04
        if shift:
            for row in bars:
                row[1:5]=[v+shift for v in row[1:5]]
        for row in bars:
            row[1:5]=[round(v,8) for v in row[1:5]]
        if self.setups and tf=='30m':
            bars[-2][1:5]=[104.3,104.6,103.5,103.9]
            bars[-1][1:5]=[104.4,105.1,104.2,105.0]
        return bars


class ScanRecoveryTests(unittest.TestCase):
    def setUp(self):
        STOP.clear();self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'test.db';self.store=Store(self.path)
        self.output=redirect_stdout(StringIO());self.output.__enter__()
    def tearDown(self):
        self.output.__exit__(None,None,None);self.store.db.close();self.tmp.cleanup();STOP.clear()

    def test_partial_scan_resumes_after_restart_without_repeating_universe_or_good_symbols(self):
        client=FakeClient(fail_symbol='S001USDT')
        h=scan(self.store,client)
        self.assertEqual((h['assessed'],h['attempted'],h['remaining']),(1,2,2))
        self.assertFalse(h['complete'])
        self.assertIsNone(self.store.setting('last_complete_scan_slot_v2'))
        self.assertEqual(client.calls[('S002USDT','30m')],0)
        self.store.db.close();self.store=Store(self.path)
        client.blocked=False;client.stamp+=MINUTE
        h=scan(self.store,client)
        self.assertTrue(h['complete']);self.assertEqual(h['assessed'],3)
        self.assertEqual(client.calls['/fapi/v1/exchangeInfo'],1)
        self.assertEqual(client.calls['/fapi/v1/ticker/24hr'],1)
        self.assertEqual(client.calls[('S000USDT','30m')],1)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM events WHERE kind='SCAN_HEALTH'").fetchone()[0],1)

    def test_blocked_scan_sends_no_requests_and_does_not_mark_complete(self):
        c=FakeClient();c.blocked=True
        h=scan(self.store,c)
        self.assertFalse(h['complete']);self.assertEqual(c.calls,Counter())
        self.assertEqual(h['attempted'],0)

    def test_deadline_expires_without_network_no_stale_signal_or_false_complete(self):
        c=FakeClient(n=1,setups=True);c.book_fail=True
        scan(self.store,c);self.assertEqual(len(self.store.plans()),0)
        prior=c.calls.copy();c.stamp+=4*MINUTE
        h=scan(self.store,c)
        self.assertEqual(h['status'],'EXPIRED');self.assertFalse(h['complete'])
        self.assertEqual(prior,c.calls);self.assertEqual(len(self.store.plans()),0)
        self.assertIsNone(self.store.setting('last_complete_scan_slot_v2'))
        row=self.store.db.execute('SELECT payload FROM observations').fetchone()
        self.assertEqual(json.loads(row[0])['admission'],'SCAN_INCOMPLETE_EXPIRED')

    def test_next_slot_gets_fresh_universe_and_expired_attempt_is_preserved(self):
        c=FakeClient(fail_symbol='S001USDT')
        scan(self.store,c);c.blocked=False;c.stamp+=CFG.entry_ms
        h=scan(self.store,c)
        self.assertTrue(h['complete']);self.assertEqual(c.calls['/fapi/v1/exchangeInfo'],2)
        statuses=[json.loads(r[0])['status'] for r in self.store.db.execute("SELECT payload FROM events WHERE kind='SCAN_HEALTH'")]
        self.assertEqual(statuses,['EXPIRED','COMPLETE'])

    def test_bounded_batch_continues_and_complete_scan_does_not_refetch_or_duplicate(self):
        c=FakeClient(n=5)
        with patch('ta_paper.runner.SCAN_BATCH_SYMBOLS',2):
            self.assertEqual(scan(self.store,c)['assessed'],2)
            self.assertEqual(scan(self.store,c)['assessed'],4)
            self.assertTrue(scan(self.store,c)['complete'])
        before=c.calls.copy();self.assertEqual(scan(self.store,c)['admitted'],0)
        self.assertEqual(c.calls,before)

    def test_deferred_book_request_is_retried_once_after_recovery_without_duplicate_plan(self):
        c=FakeClient(n=1,setups=True);c.book_fail=True
        first=scan(self.store,c)
        self.assertEqual(first['assessed'],1);self.assertFalse(first['complete'])
        self.assertEqual(len(self.store.plans()),0)
        c.stamp+=MINUTE;c.blocked=False
        second=scan(self.store,c)
        self.assertTrue(second['complete']);self.assertEqual(second['admitted'],1)
        self.store.db.close();self.store=Store(self.path)
        self.assertEqual(scan(self.store,c)['admitted'],0)
        self.assertEqual(len(self.store.plans()),1)

    def test_runtime_partial_scan_cannot_emit_ready_even_after_cooldown_ends(self):
        c=FakeClient(fail_symbol='S001USDT')
        with patch('ta_paper.runner.Binance',return_value=c),patch('ta_paper.runner.collect'):
            args=SimpleNamespace(state_root=self.tmp.name,report=False,telegram=False,once=True)
            self.assertEqual(run(args),2)
        db=Store(Path(self.tmp.name)/CFG.version.lower()/'research.sqlite3')
        try:
            self.assertIsNone(db.setting('last_complete_scan_slot_v2'))
            messages=[r[0] for r in db.db.execute('SELECT text FROM outbox')]
            self.assertFalse(any('Tarama tamamlandı' in m for m in messages))
        finally:db.db.close()

    def test_repeated_timer_changes_do_not_spam_notifications(self):
        state=dict(remaining_seconds=60,until_ms=NOW+MINUTE,source='WEIGHT_HEADER')
        for i in range(10):
            update_health(self.store,'SHARED_IP_WEIGHT_HIGH',dict(state,until_ms=NOW+(i+1)*MINUTE),NOW+i*MINUTE)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM outbox').fetchone()[0],1)
        update_health(self.store,'READY',dict(state,remaining_seconds=0),NOW+10*MINUTE,{'assessed':314,'universe':314})
        messages=[r[0] for r in self.store.db.execute('SELECT text FROM outbox')]
        self.assertEqual(len(messages),2);self.assertIn('314/314',messages[-1])

    def test_preemptive_weight_guard_respects_existing_exchange_ban_and_no_network(self):
        c=Binance();c.observed_weight=990;c.observed_weight_window=c.now()//MINUTE
        with patch('urllib.request.build_opener') as network:
            with self.assertRaises(DataUnavailable):c.get('/fapi/v1/ticker/24hr')
            network.assert_not_called()
        self.assertEqual(c.gate.state()['reason'],'SHARED_IP_WEIGHT_HIGH')
        until=int(time.time()*1000)+600_000
        c.gate.block(until,'BINANCE_HTTP_418','EXCHANGE_RETRY_TIME')
        with patch('urllib.request.build_opener') as network:
            with self.assertRaises(DataUnavailable):c.get('/fapi/v1/ticker/24hr')
            network.assert_not_called()
        self.assertEqual(c.gate.state()['until_ms'],until)

    def test_usage_counters_are_distinct_from_shared_ip_header(self):
        c=Binance();resp=market_response({'serverTime':int(time.time()*1000)})
        resp.__enter__.return_value.headers['X-MBX-USED-WEIGHT-1M']='999'
        with patch('urllib.request.build_opener') as network:
            network.return_value.open.return_value=resp
            c.get('/fapi/v1/time')
        usage=c.usage()
        self.assertEqual(usage['local_requests_since_start'],1)
        self.assertEqual(usage['reported_ip_weight_1m'],999)


if __name__=='__main__':unittest.main()
