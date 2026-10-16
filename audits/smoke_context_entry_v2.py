"""Full SQLite scan on archived bars. Publisher is an in-memory fake; no network."""
import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import resource
import sqlite3
import sys
import tempfile
import time
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import long_short_futures_kline_shadow as feed
import long_short_major_move_worker as worker
import long_short_major_move_store as store
import long_short_major_context_report as report


def run(data):
    now=1792171800500
    cohort=json.loads((data/'universe.json').read_text())
    calls=[];log=io.StringIO()
    with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'LS_STATE_DIR':tmp,'TELEGRAM_BOT_TOKEN':'local-test','TELEGRAM_CHAT_ID':'local-test'}):
        root=Path(tmp);k=feed.initialize(str(root/'binance_futures_kline_shadow.db'))
        u=sqlite3.connect(root/'binance_futures_universe.db')
        u.execute('CREATE TABLE futures_tickers(symbol,quote_volume,last_price,change_pct,received_ms,source)')
        native=sqlite3.connect(root/'native_derivatives_v2.db')
        native.execute('CREATE TABLE markets(id,observed_ms,payload)')
        native.execute('CREATE TABLE snapshots(symbol,received_ms,payload)')
        native.execute('INSERT INTO markets VALUES(1,?,?)',(now,json.dumps([p['symbol'] for p in cohort])))
        native.commit();native.close()
        for item in cohort:
            s=item['symbol'];price=None
            for tf in ('15m','1h','4h'):
                bars=json.loads((data/'market'/f'{s}_{tf}.json').read_text())['bars']
                rows=[]
                for b in bars:
                    if int(b[6])>=now-250:continue
                    rows.append((s,tf,int(b[0]),int(b[6]),*[float(b[j]) for j in (1,2,3,4,5)],feed.SOURCE,now,float(b[7]),int(b[8]),float(b[10])))
                k.executemany('INSERT INTO futures_closed_klines VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',rows)
                if tf=='15m':price=rows[-1][7]
            k.execute('INSERT INTO futures_last_price VALUES(?,?,?,?,?)',(s,price,now,now,feed.SOURCE))
            u.execute('INSERT INTO futures_tickers VALUES(?,?,?,?,?,?)',(s,float(item['quoteVolume']),price,0.,now,feed.SOURCE))
        k.commit();u.commit();u.close()
        c=store.connect();scanner=worker.CandleScanner()
        def publisher(*args,**kwargs):
            calls.append(dict(symbol=args[2],body=args[4],episode=kwargs['episode_key']));return {'status':'ACK'}
        with patch.object(worker.time,'time',return_value=now/1000),contextlib.redirect_stdout(log):
            started=time.perf_counter();scanner.run(c,k,'LOCAL_V2_SMOKE',publisher);first_ms=(time.perf_counter()-started)*1000
            initial_calls=len(calls)
            started=time.perf_counter()
            for _ in range(20):scanner.run(c,k,'LOCAL_V2_SMOKE',publisher)
            idle_ms=(time.perf_counter()-started)*1000/20
            assert len(calls)==initial_calls,'Repeated unchanged candle sent a duplicate'
            research=report.report(c,now,horizons=[.25,24],paper=True)
            assert research['reference_kind']=='FORWARD_PAPER_QUOTE_NOT_DELIVERED'
            actual=report.report(c,now,horizons=[.25,24])
            assert actual['reference_kind']=='TELEGRAM_ACK'
        result=dict(mode='LOCAL_SQLITE_WITH_FAKE_PUBLISHER_NO_NETWORK',universe=len(scanner.items),
                    first_scan_ms=round(first_ms,1),idle_poll_mean_ms=round(idle_ms,2),
                    max_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                    fake_deliveries=len(calls),duplicate_deliveries=0,
                    ideas=dict(c.execute('SELECT state,COUNT(*) FROM ideas GROUP BY state')),
                    research_references=c.execute('SELECT COUNT(*) FROM research_ideas').fetchone()[0],
                    research_report=research,scan_lines=[json.loads(line.split(' ',1)[1]) for line in log.getvalue().splitlines() if line.startswith('MAJOR_MOVE_SCAN ')],
                    note='Local timing only; not live Railway latency/RAM or actual Telegram delivery. 5m/OI missing is deliberate and explicitly labelled.')
        c.close();k.close()
        return result


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--data-dir',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);args=ap.parse_args()
    result=run(args.data_dir);args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in result.items() if k not in ('research_report','scan_lines')},ensure_ascii=False))
