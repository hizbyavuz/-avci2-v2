import tempfile
import unittest
from unittest.mock import Mock,patch
from urllib.error import HTTPError
import long_short_futures_history_worker as w


class HistorySymbolIsolationTests(unittest.TestCase):
    def run_pass(self,code):
        stop=Mock()
        stop.is_set.return_value=False
        stop.wait.side_effect=lambda delay: setattr(stop.is_set,'return_value',True) if delay==60 else None
        seen=[]
        def repair(db,base,symbol,interval):
            seen.append((symbol,interval))
            if symbol=='BADUSDT':
                raise HTTPError(base,code,'rejected',None,None)
        with tempfile.TemporaryDirectory() as root,patch.dict('os.environ',{
            'LS_FUTURES_HISTORY_RELAY_URL':'http://relay.railway.internal',
            'LS_STATE_DIR':root,'LS_NATIVE_CHAIN_V2':'0'}), \
            patch.object(w.threading,'Event',return_value=stop), \
            patch.object(w.signal,'signal'),patch.object(w,'log'), \
            patch.object(w,'requested_symbols',return_value=['BADUSDT','GOODUSDT']), \
            patch.object(w,'INTERVALS',('1m','5m')),patch.object(w,'repair',side_effect=repair):
            self.assertEqual(w.main(),0)
        return seen

    def test_bad_symbol_does_not_starve_other_symbols_or_intervals(self):
        self.assertEqual(self.run_pass(400),[('BADUSDT','1m'),('GOODUSDT','1m'),('GOODUSDT','5m')])

    def test_rate_limit_still_stops_entire_pass(self):
        self.assertEqual(self.run_pass(429),[('BADUSDT','1m')])
