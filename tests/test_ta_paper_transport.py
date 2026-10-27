"""Regression coverage for missing weight headers and transport cooldowns."""
from contextlib import redirect_stdout
from io import BytesIO, StringIO
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from ta_paper.core import MINUTE
from ta_paper.io import Binance, DataUnavailable
from ta_paper.rate_limit import RequestGate
from test_ta_paper_operations import market_response


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.client=Binance(Path(self.tmp.name)/'cooldown.json')
        self.clock=[int(time.time()*1000)//MINUTE*MINUTE+10000]
        self.client.now=lambda:self.clock[0]
        self.capture=redirect_stdout(StringIO());self.capture.__enter__()

    def tearDown(self):
        self.capture.__exit__(None,None,None);self.tmp.cleanup()

    def response(self, weight=None):
        response=market_response({})
        response.__enter__.return_value.headers={}
        if weight is not None:
            response.__enter__.return_value.headers['X-MBX-USED-WEIGHT-1M']=weight
        return response

    def test_missing_invalid_and_lower_headers_never_erase_same_minute_usage(self):
        with patch('urllib.request.build_opener') as net,patch.object(self.client.gate,'acquire'):
            net.return_value.open.side_effect=[self.response(w) for w in ('800',None,'bad','799')]
            for _ in range(4):self.client.get('/fapi/v1/time')
        usage=self.client.usage()
        self.assertEqual(usage['reported_ip_weight_1m'],800)
        self.assertEqual(usage['guarded_ip_weight_1m'],803)
        self.assertEqual(usage['local_requests_since_start'],4)

    def test_valid_header_in_new_minute_starts_new_window(self):
        self.client._observe_weight({'X-MBX-USED-WEIGHT-1M':'800'})
        self.clock[0]+=MINUTE
        self.client._observe_weight({'X-MBX-USED-WEIGHT-1M':'2'})
        self.assertEqual(self.client.observed_weight,2)
        self.assertEqual(self.client.ip_weight_estimate,2)

    def test_missing_headers_still_consume_protective_budget(self):
        self.client._observe_weight({'X-MBX-USED-WEIGHT-1M':'999'})
        with patch('urllib.request.build_opener') as net:
            net.return_value.open.return_value=self.response()
            self.client.get('/fapi/v1/time')
            with self.assertRaises(DataUnavailable):self.client.get('/fapi/v1/time')
            self.assertEqual(net.return_value.open.call_count,1)
        self.assertEqual(self.client.observed_weight,999)
        self.assertEqual(self.client.ip_weight_estimate,1000)

    def test_http_error_records_usage_and_restart_keeps_exchange_deadline(self):
        until=int(time.time()*1000)+3600000
        error=HTTPError('https://fapi.binance.com/fapi/v1/time',418,'blocked',
            {'X-MBX-USED-WEIGHT-1M':'1700'},BytesIO(json.dumps({'msg':f'IP banned until {until}'}).encode()))
        with patch('urllib.request.build_opener') as net:
            net.return_value.open.side_effect=error
            with self.assertRaisesRegex(DataUnavailable,'BINANCE_HTTP_418'):
                self.client.get('/fapi/v1/time')
            with self.assertRaises(DataUnavailable):self.client.get('/fapi/v1/time')
            self.assertEqual(net.return_value.open.call_count,1)
        self.assertEqual(self.client.observed_weight,1700)
        gate=RequestGate(self.client.gate.path)
        self.assertEqual(gate.state()['until_ms'],until+2000)
        self.assertEqual(gate.state()['source'],'EXCHANGE_RETRY_TIME')


if __name__=='__main__':unittest.main()
