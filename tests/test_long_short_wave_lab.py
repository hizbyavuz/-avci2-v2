"""Behavioral tests with synthetic data; these do not measure trading edge."""
import copy
import json
import math
from pathlib import Path
import sqlite3
import tempfile
import unittest

import long_short_wave_lab as w
import long_short_wave_runner as r


SOURCE = "BINANCE_UM_FUTURES_WEBSOCKET"


def bar(t, price=100., step=60_000, high=None, low=None, volume=10.):
    return {"open_ms": t, "close_ms": t+step-1, "open": price,
            "high": price+0.1 if high is None else high,
            "low": price-0.1 if low is None else low, "close": price,
            "volume": volume, "source": SOURCE, "received_ms": t+step}


def full_history():
    return [bar(i*300_000, 100+i*.01+math.sin(i/18), step=300_000)
            for i in range(1200)]


def pullback_features():
    h = [bar(i*3_600_000, p) for i, p in enumerate(
        [100, 102, 104, 102, 101, 103, 106, 104, 103, 105, 108, 106, 105, 107, 110, 108, 107, 109])]
    vals = [110.+math.sin(i) for i in range(36)] + [111.,112.,113.,114.,115.,114.,113.,112.,112.1,112.2,112.3,112.4]
    f = [bar(i*300_000, p, step=300_000) for i, p in enumerate([112.1]*35+[112.2,112.3,112.4,112.5,112.8])]
    return {"health": "OK", "timeframes": {5:f,15:[bar(i*900_000,p) for i,p in enumerate(vals)],60:h,240:h},
            "atr_15m": 1.5, "volume_ratio": 1.3, "trend_1h":"UP", "trend_4h":"UP", "return_1h_pct":2.}


def compression_features():
    feat = pullback_features()
    vals = [100+3*math.sin(i) for i in range(36)]
    box = [bar(i*900_000, 100.5, high=101., low=99.+i*.08) for i in range(12)]
    feat["timeframes"][15] = [bar(i*900_000,p) for i,p in enumerate(vals)]+box+[bar(48*900_000,100.8)]
    feat["timeframes"][5][-1] = bar(39*300_000,100.8,step=300_000)
    feat["atr_15m"] = 1.
    return feat


def event(**kw):
    return {"signal_ms": 0, "direction":"LONG", "stop":98., "target":104.,
            "trigger":100., "atr":2., "return_1h_pct":50., **kw}


class CandleSafetyTests(unittest.TestCase):
    def test_complete_history_and_closed_higher_frames(self):
        rows=full_history()
        result=w.prepare(rows, rows[-1]["close_ms"], rows[-1]["close_ms"]+1)
        self.assertEqual(result["health"],"OK")
        for minutes, tf in result["timeframes"].items():
            self.assertLessEqual(tf[-1]["close_ms"],rows[-1]["close_ms"])
            self.assertEqual(tf[-1]["close_ms"]+1, ((rows[-1]["close_ms"]+1)//(minutes*60000))*(minutes*60000))

    def test_gap_future_stale_nan_and_wrong_venue_fail_closed(self):
        rows=full_history(); end=rows[-1]["close_ms"]
        cases=[]
        cases.append(rows[:500]+rows[501:])
        future=copy.deepcopy(rows); future[-1]["close_ms"]+=300000;cases.append(future)
        bad=copy.deepcopy(rows);bad[500]["close"]=float("nan");cases.append(bad)
        other=copy.deepcopy(rows);other[500]["source"]="GATE_FUTURES";cases.append(other)
        duplicate=rows[:500]+[rows[499]]+rows[500:];cases.append(duplicate)
        for data in cases:
            self.assertEqual(w.prepare(data,end,end+1)["health"],"INVALID")
        self.assertEqual(w.prepare(rows,end,end+180000)["reason"],"SCAN_DELAY")

    def test_unconfirmed_pivot_not_used(self):
        rows=[bar(i*60000,p) for i,p in enumerate([100,101,102,101,100,99,98,100,105])]
        highs,_=w.pivots(rows)
        self.assertNotIn(105.1, highs)
        self.assertIn(102.1, highs)


class StrategyTests(unittest.TestCase):
    def test_pullback_long_and_mirrored_short(self):
        feat=pullback_features()
        p=next(x for x in w.proposals("TESTUSDT",feat) if x["strategy"]=="TREND_PULLBACK" and x["direction"]=="LONG")
        self.assertTrue(p["qualified_watch"],p["failed_checks"])
        short=copy.deepcopy(feat)
        short["timeframes"]={m:[{**b,"open":300-b["open"],"close":300-b["close"],"high":300-b["low"],"low":300-b["high"]} for b in rows] for m,rows in feat["timeframes"].items()}
        short.update(trend_1h="DOWN",trend_4h="DOWN")
        q=next(x for x in w.proposals("TESTUSDT",short) if x["strategy"]=="TREND_PULLBACK" and x["direction"]=="SHORT")
        self.assertTrue(q["qualified_watch"],q["failed_checks"])
        self.assertLess(q["target"],q["trigger"])
        self.assertGreater(q["stop"],q["trigger"])

    def test_compression_needs_directional_pressure(self):
        feat=compression_features()
        select=lambda f:next(p for p in w.proposals("TESTUSDT",f) if p["direction"]=="LONG" and p["strategy"]=="COMPRESSION_BREAKOUT")
        self.assertTrue(select(feat)["qualified_watch"], select(feat)["failed_checks"])
        for b in feat["timeframes"][15][-13:-1]: b["low"]=99.
        self.assertFalse(select(feat)["qualified_watch"])

    def test_extension_and_low_activity_do_not_qualify(self):
        feat=pullback_features();feat["volume_ratio"]=.1
        self.assertFalse(any(p["qualified_watch"] for p in w.proposals("TUSDT",feat)))
        feat=pullback_features();feat["timeframes"][5][-1]["close"]=150.
        p=next(x for x in w.proposals("TUSDT",feat) if x["strategy"]=="TREND_PULLBACK" and x["direction"]=="LONG")
        self.assertIn("not_extended",p["failed_checks"])

    def test_breakout_requires_subsequent_close_and_handles_lost_level(self):
        e=event(state="WATCH",last_bar_ms=299999)
        b=[bar(300000,100.5,step=300000)]
        self.assertEqual(w.transition(e,b,600001,1.4)[0],"BREAKOUT")
        e.update(state="BREAKOUT",last_bar_ms=599999)
        self.assertEqual(w.transition(e,[bar(600000,100.6,step=300000)],900001,.8)[0],"CONFIRMED")
        self.assertEqual(w.transition(e,[bar(600000,99.5,step=300000)],900001,1.4)[0],"WATCH")
        self.assertEqual(w.transition(e,[bar(600000,103.,step=300000)],900001,1.4)[0],"LATE")

    def test_stop_wick_gap_and_ttl(self):
        e=event(state="WATCH",last_bar_ms=299999)
        self.assertEqual(w.transition(e,[bar(300000,100.,step=300000,low=97.)],600001,1.4)[0],"INVALIDATED")
        self.assertEqual(w.transition(e,[bar(600000,100.,step=300000)],900001,1.4)[1],"PATH_UNAVAILABLE")
        self.assertEqual(w.transition(e,[bar(300000,100.,step=300000)],7*3600000,1.4)[0],"EXPIRED")


class OutcomeTests(unittest.TestCase):
    def path(self): return [bar((i+1)*60000) for i in range(60)]
    def test_first_barrier_stop_even_if_later_moon(self):
        path=self.path();path[0].update(high=105.,low=97.)
        path[-1].update(high=150.)
        x=w.paper_outcome(event(),path,60,4000000)
        self.assertEqual(x["first_barrier"],"STOP")
        self.assertGreater(x["raw_horizon_mfe_pct"],40)
        self.assertIsNone(x["reach_before_exit_minutes"]["3"])
        self.assertIsNone(x["net_pct"])
        self.assertLess(x["modeled_net_excluding_funding_pct"],-2.)

    def test_gap_stop_uses_first_available_open(self):
        path=self.path();path[1]=bar(120000,95.)
        x=w.paper_outcome(event(),path,60,4000000,0.)
        self.assertEqual(x["first_barrier"],"STOP")
        self.assertLess(x["exit_price_model"],95.)
        self.assertLess(x["net_pct"],-5.)

    def test_short_gap_and_target(self):
        path=self.path();path[1]=bar(120000,105.)
        e=event(direction="SHORT",stop=102.,target=96.)
        x=w.paper_outcome(e,path,60,4000000,0.)
        self.assertEqual(x["first_barrier"],"STOP")
        self.assertGreater(x["exit_price_model"],105.)
        path=self.path();path[1].update(low=95.)
        y=w.paper_outcome(e,path,60,4000000,0.)
        self.assertEqual(y["first_barrier"],"TARGET")
        self.assertGreater(y["net_pct"],3.)

    def test_missing_path_is_not_a_loss_and_incomplete_is_pending(self):
        path=self.path()
        self.assertEqual(w.paper_outcome(event(),path[:-1],60,4000000)["status"],"DATA_FAILURE")
        self.assertEqual(w.paper_outcome(event(),path,60,100000)["status"],"PENDING")

    def test_pre_signal_gain_not_in_return_and_no_target_no_trade(self):
        x=w.paper_outcome(event(),self.path(),60,4000000,0.)
        self.assertLess(x["net_pct"],0.)
        self.assertEqual(x["pre_signal_return_1h_pct"],50.)
        x=w.paper_outcome(event(target=None),self.path(),60,4000000)
        self.assertEqual(x["status"],"OBSERVATION_ONLY")
        self.assertNotIn("net_pct",x)

    def test_latency_entry_does_not_use_signal_candle(self):
        path=self.path();path[0].update(high=110.)
        # 5s signal + 60s delay => earliest 1m open 120s, not 60s.
        path.append(bar(3660000))
        x=w.paper_outcome(event(signal_ms=5000),path,60,4000000,0.)
        self.assertEqual(x["entry_due_ms"],120000)
        self.assertEqual(x["first_barrier"],"TIMEOUT")
        self.assertLess(x["raw_horizon_mfe_pct"],1.)


class RunnerTests(unittest.TestCase):
    def make_db(self, path, rows):
        c=sqlite3.connect(path)
        c.execute("CREATE TABLE futures_closed_klines(symbol TEXT,interval TEXT,open_ms INTEGER,close_ms INTEGER,open REAL,high REAL,low REAL,close REAL,volume REAL,source TEXT,received_ms INTEGER)")
        c.executemany("INSERT INTO futures_closed_klines VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      [("TESTUSDT","5m",*[b[k] for k in ("open_ms","close_ms","open","high","low","close","volume","source","received_ms")]) for b in rows])
        c.commit();c.close()

    def test_readonly_full_scan_snapshots_and_idempotence(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"source.db";rows=full_history();self.make_db(path,rows)
            before=path.read_bytes()
            src=r.readonly(path);out=sqlite3.connect(":memory:");r.initialize(out)
            now=rows[-1]["close_ms"]+1
            scan=r.scan(src,out,now,"TEST")
            self.assertEqual(scan["healthy"],1)
            self.assertGreater(scan["new_events"],0)
            self.assertEqual(out.execute("SELECT COUNT(*) FROM wave_snapshots").fetchone()[0],1)
            self.assertEqual(out.execute("SELECT COUNT(*) FROM wave_raw_bars").fetchone()[0],len(rows))
            self.assertEqual(r.scan(src,out,now,"TEST")["status"],"ALREADY_RECORDED")
            self.assertFalse(r.report(out)["edge_proven"])
            with self.assertRaises(sqlite3.OperationalError): src.execute("DELETE FROM futures_closed_klines")
            src.close();out.close()
            self.assertEqual(before,path.read_bytes())

    def test_label_report_keeps_missing_outcomes_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"source.db";self.make_db(path,[])
            c=sqlite3.connect(path)
            minute_bars=[bar((i+1)*60000) for i in range(60)]
            minute_bars[1].update(low=97.,high=105.)
            c.executemany("INSERT INTO futures_closed_klines VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                [("TESTUSDT","1m",*[b[k] for k in ("open_ms","close_ms","open","high","low","close","volume","source","received_ms")]) for b in minute_bars])
            c.commit();c.close()
            src=r.readonly(path);out=sqlite3.connect(":memory:");r.initialize(out)
            p={**event(),"symbol":"TESTUSDT","strategy":"TREND_PULLBACK","signal_price":100.}
            r.new_event(out,"test",p,0,-1,"FORWARD");out.commit()
            self.assertEqual(r.label(src,out,8000000),2)
            self.assertEqual(r.label(src,out,8000000),0)
            groups=list(r.report(out)["groups"].values())
            self.assertEqual(sum(x["stops"] for x in groups),1)
            self.assertEqual(sum(x["data_failures"] for x in groups),1)
            self.assertEqual(sum(x["net_known"] for x in groups),0)
            src.close();out.close()

    def test_missing_universe_symbol_and_late_received_bar_logged(self):
        with tempfile.TemporaryDirectory() as d:
            rows=full_history();now=rows[-1]["close_ms"]+1
            rows[-1]["received_ms"]=now+1000
            path=Path(d)/"source.db";self.make_db(path,rows)
            src=r.readonly(path);out=sqlite3.connect(":memory:");r.initialize(out)
            universe=sqlite3.connect(":memory:")
            universe.execute("CREATE TABLE futures_tickers(symbol TEXT,source TEXT,received_ms INTEGER)")
            universe.executemany("INSERT INTO futures_tickers VALUES(?,?,?)",[(s,SOURCE,now) for s in ("TESTUSDT","MISSINGUSDT")])
            x=r.scan(src,out,now,"TEST",universe)
            self.assertEqual(x["symbols"],2);self.assertEqual(x["healthy"],0)
            self.assertEqual(x["new_events"],0)
            self.assertIn("LATEST_CLOSED_BAR_MISSING",x["errors"])
            self.assertIn("NO_BARS",x["errors"])
            replay=r.scan(src,out,now,"TEST",replay=True)
            self.assertEqual(replay["healthy"],1)
            self.assertEqual(out.execute("SELECT COUNT(DISTINCT mode) FROM wave_scans").fetchone()[0],2)
            src.close();out.close();universe.close()


if __name__=="__main__": unittest.main()
