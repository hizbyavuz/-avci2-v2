from contextlib import redirect_stdout
from datetime import datetime, timezone
from email.message import Message
from io import BytesIO, StringIO
import json
from pathlib import Path
import sqlite3
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch, MagicMock
import urllib.error

from PIL import Image
from ta_paper.chart import preview, render_plan
from ta_paper.core import CFG, MINUTE
from ta_paper.io import Binance, DataUnavailable, Store, Telegram, photo_body
from ta_paper.rate_limit import CooldownError, RequestGate, retry_deadline
from ta_paper.runner import STOP, notify, run
from test_ta_paper import filled, series, NOW


def response(data):
    result=MagicMock()
    result.__enter__.return_value=BytesIO(json.dumps(data).encode())
    return result


class RateLimitTests(unittest.TestCase):
    def test_server_header_and_ban_body_use_later_deadline(self):
        now=1_800_000_000_000
        until,source=retry_deadline(418,{'Retry-After':'120'},
                                  f'IP banned until {now+600_000}.',now)
        self.assertEqual(until,now+602_000)
        self.assertEqual(source,'EXCHANGE_RETRY_TIME')
        date=datetime.fromtimestamp((now+180_000)/1000,timezone.utc).strftime('%a, %d %b %Y %H:%M:%S GMT')
        self.assertEqual(retry_deadline(429,{'Retry-After':date},'',now)[0],now+182_000)

    def test_unknown_418_is_not_retried_every_minute(self):
        for value in ('', 'bad', 'NaN', '-4'):
            self.assertEqual(retry_deadline(418,{'Retry-After':value},'',1000)[0],1000+72*3600*1000)

    def test_cooldown_survives_restart_and_never_shortens(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'cooldown.json'
            gate=RequestGate(path)
            until=int(time.time()*1000)+600_000
            gate.block(until,'BINANCE_HTTP_418','EXCHANGE_RETRY_TIME')
            gate.block(until-300_000,'SHARED_IP_WEIGHT_HIGH','WEIGHT_HEADER')
            restored=RequestGate(path)
            self.assertEqual(restored.state()['until_ms'],until)
            with self.assertRaises(CooldownError): restored.acquire(1)

    def test_http_418_persists_without_exposing_error_body_and_blocks_network(self):
        with tempfile.TemporaryDirectory() as d:
            now=int(time.time()*1000)
            headers=Message(); headers['Retry-After']='120'
            exc=urllib.error.HTTPError('https://fapi.binance.com/fapi/v1/time',418,'blocked',headers,
                                      BytesIO(f'IP secret-address banned until {now+300_000}'.encode()))
            with patch('urllib.request.build_opener') as make,redirect_stdout(StringIO()) as output:
                make.return_value.open.side_effect=exc
                client=Binance(Path(d)/'cooldown.json')
                with self.assertRaisesRegex(DataUnavailable,'418'): client.get('/fapi/v1/time')
                restarted=Binance(Path(d)/'cooldown.json')
                with self.assertRaisesRegex(DataUnavailable,'COOLDOWN'): restarted.get('/fapi/v1/time')
                self.assertEqual(make.return_value.open.call_count,1)
                self.assertNotIn('secret-address',output.getvalue())

    def test_four_hour_cache_expires_at_next_closed_context(self):
        client=Binance()
        raw=series(CFG.context_ms)
        with patch.object(client,'now',return_value=NOW) as clock,patch.object(client,'get',return_value=raw) as get:
            client.candles('TESTUSDT','4h')
            clock.return_value=NOW+30*MINUTE
            client.candles('TESTUSDT','4h')
            self.assertEqual(get.call_count,1)
            clock.return_value=NOW+CFG.context_ms
            client.candles('TESTUSDT','4h')
            self.assertEqual(get.call_count,2)

    def test_legacy_migration_does_not_reactivate_a_known_banned_ip(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'s.db')
            now=int(time.time()*1000)
            store.event('DATA_HEALTH_INVALID','runtime',{'reason':'BINANCE_HTTP_418'},now)
            store.event('DATA_HEALTH_INVALID','runtime',{'reason':'EXCHANGE_COOLDOWN'},now+1)
            result=store.legacy_cooldown()
            self.assertEqual(result['until_ms'],now+72*3600*1000)
            self.assertEqual(result['source'],'LEGACY_UNKNOWN_UPPER_BOUND')
            store.event('SCAN_HEALTH','scan',{'assessed':20},now+2)
            self.assertIsNone(store.legacy_cooldown())


class ChartAndDeliveryTests(unittest.TestCase):
    def test_both_directions_render_and_future_candles_do_not_change_card(self):
        for direction in ('LONG','SHORT'):
            p=filled(direction)
            rows=series(CFG.entry_ms)
            result=render_plan(p,rows,n=7)
            im=Image.open(BytesIO(result))
            self.assertEqual(im.size,(1440,1660))
            self.assertLess(len(result),10_000_000)
            future=[p['candle_ms']+CFG.entry_ms,1,99999,.1,10000,100,NOW+CFG.entry_ms]
            self.assertEqual(render_plan(p,rows+[future],n=7),result)
            p['stop']=p['entry']
            with self.assertRaisesRegex(ValueError,'LEVELS'):render_plan(p,rows)

    def test_existing_outbox_is_migrated_without_losing_pending_message(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'s.db'
            db=sqlite3.connect(path)
            db.execute('CREATE TABLE outbox(key TEXT PRIMARY KEY,ms INTEGER,text TEXT,state TEXT,message_id TEXT)')
            db.execute("INSERT INTO outbox VALUES('old',1,'saved','PENDING',NULL)")
            db.commit();db.close()
            store=Store(path)
            self.assertEqual(store.db.execute('SELECT text FROM outbox').fetchone()[0],'saved')
            self.assertIn('photo',{r[1] for r in store.db.execute('PRAGMA table_info(outbox)')})

    def test_photo_delivery_is_acknowledged_once_without_logging_token(self):
        with tempfile.TemporaryDirectory() as d:
            now=int(time.time()*1000)
            store=Store(Path(d)/'s.db')
            png=preview(now)
            store.queue('system:demo','YAPAY VERİ - GERÇEK SİNYAL DEĞİL',now,photo=png)
            telegram=Telegram(d);telegram.token='SECRET_TEST_TOKEN';telegram.chat='123'
            with patch('urllib.request.build_opener') as make,redirect_stdout(StringIO()) as output:
                make.return_value.open.return_value=response({'ok':True,'result':{'chat':{'id':123},'message_id':42}})
                self.assertEqual(telegram.flush(store,now),1)
                self.assertEqual(telegram.flush(store,now+1),0)
                req=make.return_value.open.call_args.args[0]
                self.assertTrue(req.full_url.endswith('/sendPhoto'))
                self.assertIn(png,req.data)
                self.assertIn(b'name="caption"',req.data)
                self.assertNotIn('SECRET_TEST_TOKEN',output.getvalue())
                self.assertIn('TA_PAPER_TELEGRAM_ACK',output.getvalue())
                self.assertEqual(store.db.execute('SELECT state FROM outbox').fetchone()[0],'DELIVERED')

    def test_ambiguous_delivery_is_not_resent_and_429_wait_is_respected(self):
        with tempfile.TemporaryDirectory() as d:
            now=int(time.time()*1000)
            store=Store(Path(d)/'s.db');store.queue('one','test',now)
            telegram=Telegram(d);telegram.token='TEST';telegram.chat='123'
            with patch('urllib.request.build_opener') as make,redirect_stdout(StringIO()):
                make.return_value.open.side_effect=TimeoutError('sensitive url must not print')
                telegram.flush(store,now);telegram.flush(store,now+1)
                self.assertEqual(make.return_value.open.call_count,1)
                self.assertEqual(store.db.execute('SELECT state FROM outbox').fetchone()[0],'DELIVERY_UNKNOWN')
                store.queue('two','test',now)
                make.return_value.open.side_effect=urllib.error.HTTPError('https://api.telegram.org',429,'rate',{},BytesIO(b'{"parameters":{"retry_after":120}}'))
                telegram.flush(store,now);telegram.flush(store,now+60_000)
                self.assertEqual(make.return_value.open.call_count,2)
                self.assertEqual(store.db.execute("SELECT state FROM outbox WHERE key='two'").fetchone()[0],'PENDING')

    def test_system_photo_and_health_are_sent_while_exchange_is_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            now=int(time.time()*1000)
            folder=Path(d)/'ta_paper_v1_20261026';folder.mkdir()
            store=Store(folder/'research.sqlite3')
            store.event('DATA_HEALTH_INVALID','runtime',{'reason':'BINANCE_HTTP_418'},now)
            store.queue('a-trade-plan','DO NOT SEND DURING OUTAGE',now)
            store.db.close()
            STOP.clear()
            with patch.dict('os.environ',{'TELEGRAM_BOT_TOKEN':'TEST','TELEGRAM_CHAT_ID':'123'}),patch('urllib.request.build_opener') as make,redirect_stdout(StringIO()) as output:
                make.return_value.open.return_value=response({'ok':True,'result':{'chat':{'id':123},'message_id':1}})
                self.assertEqual(run(SimpleNamespace(state_root=d,telegram=True,report=False,once=True)),2)
                requests=[c.args[0] for c in make.return_value.open.call_args_list]
                self.assertEqual(len(requests),2)
                self.assertTrue(all(r.full_url.startswith('https://api.telegram.org/') for r in requests))
                self.assertTrue(any(r.full_url.endswith('/sendPhoto') for r in requests))
                self.assertIn('LEGACY_UNKNOWN_UPPER_BOUND',output.getvalue())
            saved=Store(folder/'research.sqlite3')
            self.assertEqual(saved.db.execute("SELECT state FROM outbox WHERE key='a-trade-plan'").fetchone()[0],'PENDING')
            self.assertEqual(len(saved.plans()),0)


if __name__=='__main__': unittest.main()
