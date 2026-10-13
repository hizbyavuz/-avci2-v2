import copy
import tempfile
import unittest
import json
from unittest.mock import patch, MagicMock
from pathlib import Path
from long_short_futures_history_worker import validated_rows, ENDPOINT, repair
from long_short_futures_kline_shadow import initialize, upsert_rows, REST_SOURCE


class HistoryRepairTests(unittest.TestCase):
    def payload(self):
        return dict(source=REST_SOURCE,endpoint=ENDPOINT,symbol='BTCUSDT',interval='5m',
                    bars=[[300000,'10','12','9','11','3',599999,'33',5,'1','11','0']])

    def test_closed_and_forming_separated(self):
        p=self.payload()
        self.assertEqual(validated_rows(p,'BTCUSDT','5m',now_ms=700000)[0][9],REST_SOURCE)
        self.assertEqual(validated_rows(p,'BTCUSDT','5m',now_ms=500000),[])

    def test_wrong_market_or_identity_rejected(self):
        for key,value in [('source','BINANCE_SPOT'),('endpoint','https://api.binance.com/api/v3/klines'),
                          ('symbol','ETHUSDT'),('interval','1m')]:
            p=self.payload();p[key]=value
            with self.assertRaises(ValueError):validated_rows(p,'BTCUSDT','5m',700000)

    def test_bad_fields_and_timestamps_rejected(self):
        for index,value in [(0,300001),(6,599998),(2,'8'),(5,'nan'),(10,'34')]:
            p=self.payload();p['bars'][0][index]=value
            with self.assertRaises(ValueError):validated_rows(p,'BTCUSDT','5m',700000)

    def test_gap_repaired_idempotently(self):
        with tempfile.TemporaryDirectory() as d:
            c=initialize(str(Path(d)/'history.db'))
            rows=validated_rows(self.payload(),'BTCUSDT','5m',700000)
            self.assertEqual(upsert_rows(c,rows)['inserted'],1)
            self.assertEqual(upsert_rows(c,rows)['duplicate'],1)
            self.assertEqual(c.execute('SELECT count(*) FROM futures_closed_klines').fetchone()[0],1)
            c.close()

    def test_duplicate_or_short_row_rejected(self):
        p=self.payload();p['bars'].append(copy.deepcopy(p['bars'][0]))
        with self.assertRaises(ValueError):validated_rows(p,'BTCUSDT','5m',700000)

    def test_candle_closing_in_transit_is_not_imported(self):
        response=MagicMock()
        response.__enter__.return_value.read.return_value=json.dumps(self.payload()).encode()
        with tempfile.TemporaryDirectory() as d, patch('long_short_futures_history_worker.urlopen',return_value=response), \
                patch('long_short_futures_history_worker.time.time',return_value=500):
            result=repair(str(Path(d)/'history.db'),'http://test.railway.internal:8080','BTCUSDT','5m')
            self.assertEqual(result['inserted'],0)
        p=self.payload();p['bars'][0]=p['bars'][0][:7]
        with self.assertRaises(ValueError):validated_rows(p,'BTCUSDT','5m',700000)
