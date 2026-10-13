import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from long_short_message_outcomes import evaluate, collect, text_reference


class ReceiptOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root/'binance_futures_kline_shadow.db'
        with sqlite3.connect(self.db) as c:
            c.execute('CREATE TABLE futures_closed_klines(symbol,interval,open_ms,close_ms,high,low,close,source)')
            c.executemany('INSERT INTO futures_closed_klines VALUES(?,?,?,?,?,?,?,?)', [
                ('ABCUSDT','1m',900000,959999,105,95,102,'BINANCE_UM_FUTURES_WEBSOCKET'),
                ('ABCUSDT','1m',960000,1019999,110,100,108,'BINANCE_UM_FUTURES_WEBSOCKET')])

    def tearDown(self):
        self.tmp.cleanup()

    def message(self, side='LONG'):
        return {'symbol':'ABCUSDT','price':100,'direction':side,'sent_epoch':70}

    def test_no_lookahead_and_short_uses_same_denominator(self):
        long = evaluate(self.message(),self.db,10000)['15']
        short = evaluate(self.message('SHORT'),self.db,10000)['15']
        self.assertEqual(long['end_price'],102)
        self.assertEqual(long['direction_pct'],2)
        self.assertEqual(short['direction_pct'],-2)
        self.assertAlmostEqual(long['endpoint_lag_seconds'],10.001)

    def test_missing_horizon_endpoint_never_uses_stale_last_price(self):
        r = evaluate(self.message(),self.db,10000)
        self.assertEqual(r['30']['status'],'MISSING_ENDPOINT')

    def test_pending_not_counted_as_loss(self):
        self.assertEqual(evaluate(self.message(),self.db,800)['15']['status'],'PENDING_HORIZON')

    def test_bad_price_or_unverified_source_stays_unresolved(self):
        for value in (None,0,float('nan')):
            m=self.message();m['price']=value
            self.assertEqual(evaluate(m,self.db,10000)['15']['status'],'MISSING_REFERENCE')
        with sqlite3.connect(self.db) as c:
            c.execute("UPDATE futures_closed_klines SET source='SPOT'")
        self.assertEqual(evaluate(self.message(),self.db,10000)['15']['status'],'UNVERIFIED_ENDPOINT')

    def test_parser_never_substitutes_level_or_target(self):
        self.assertIsNone(text_reference('Kırılım: 100\nHedef: 105'))
        self.assertEqual(text_reference('Son ölçülen fiyat: 0.14461 | Kırılım: 0.1448'),0.14461)

    def test_old_card_without_original_text_is_missing_not_current_card(self):
        with sqlite3.connect(self.root/'long_short_message_cards.db') as c:
            c.execute('CREATE TABLE deliveries(id,symbol,method,status,time_utc,message_id,detail)')
            c.execute("INSERT INTO deliveries VALUES(1,'ABCUSDT','sendMessage','ACK','1970-01-01T00:01:10+00:00',4,NULL)")
        rows,issues=collect(self.root,0,1000)
        self.assertEqual(len(rows),1)
        self.assertIsNone(rows[0]['price'])
        self.assertEqual(rows[0]['reference'],'missing_historical_text')

    def test_immutable_receipt_uses_actual_ack_and_own_source_price(self):
        with sqlite3.connect(self.root/'long_short_message_cards.db') as c:
            c.execute('CREATE TABLE deliveries(id,symbol,method,status,time_utc,message_id,detail)')
            detail={'source':'EARLY','direction':'SHORT','text':'Şu an fiyat: 200',
                    'source_text':'🔴 SHORT\nSon ölçülen fiyat: 100',
                    'accepted_at_utc':'1970-01-01T00:01:12+00:00'}
            c.execute("INSERT INTO deliveries VALUES(1,'ABCUSDT','editMessageText','ACK','1970-01-01T00:01:10+00:00',4,?)",(json.dumps(detail),))
        rows,issues=collect(self.root,0,1000)
        self.assertEqual(rows[0]['price'],100)
        self.assertEqual(rows[0]['sent_epoch'],72)


if __name__=='__main__':unittest.main()
