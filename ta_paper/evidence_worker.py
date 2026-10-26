"""Low-priority Bybit evidence sidecar; source databases are read-only.

One public WebSocket, no REST, account endpoints, Binance access or messages.
Losing this process never changes or stops candidate selection.
"""
import argparse
from collections import OrderedDict
import fcntl
import json
import os
from pathlib import Path
import resource
import shutil
import signal
import threading
import time

from .evidence import Evidence, MINUTE, DAY, REVISION, dumps, number, readonly

URL = 'wss://stream.bybit.com/v5/public/linear'
MAX_SYMBOLS = 200
STOP = threading.Event()
TICK_FIELDS = ('lastPrice','markPrice','indexPrice','openInterest','openInterestValue',
               'fundingRate','nextFundingTime','bid1Price','bid1Size','ask1Price','ask1Size')


class StreamRateLimit(RuntimeError):pass


def clock():return int(time.time()*1000)


def log(kind, **fields):
    fields.setdefault('revision',REVISION)
    print(kind+' '+dumps(fields),flush=True)


def cooldown(path, now):
    if not path.exists():return False
    try:
        value=json.loads(path.read_text())
        return max(int(value['until_ms']),int(value.get('user_not_before_ms',0)))>now
    except (ValueError,KeyError,TypeError,OSError):return True


class Feed:
    def __init__(self, evidence):
        self.evidence=evidence;self.ready={};self.tickers={};self.sampled={}
        self.flows={};self.finalized={};self.seen=OrderedDict();self.messages=0

    def connected(self, symbols, now):
        for symbol in symbols:self.ready[symbol]=now

    def reset(self, now):
        for row in self.flows.values():row['complete']=False
        self.flush(now,force=True)
        self.ready.clear();self.tickers.clear();self.seen.clear()

    def handle(self, payload, now):
        topic=payload.get('topic','')
        if not topic:return
        symbol=topic.rsplit('.',1)[-1]
        if symbol not in self.ready:return
        event=int(payload.get('ts',0))
        if event>now+1000 or now-event>5000:raise ValueError('STREAM_CLOCK_OR_LATENCY')
        self.messages+=1
        if topic.startswith('tickers.'):
            data=payload['data']
            if data.get('symbol')!=symbol:raise ValueError('TICKER_SYMBOL_MISMATCH')
            if payload.get('type')=='snapshot':self.tickers[symbol]={}
            state=self.tickers.setdefault(symbol,{})
            for key in TICK_FIELDS:
                if key in data and data[key] not in ('',None):state[key]=number(data[key])
            if 'openInterest' in data:state['oi_received_ms']=now
            state.update(event_ms=event,received_ms=now)
            bucket=event//MINUTE
            if self.sampled.get(symbol)!=bucket and 'lastPrice' in state:
                self.evidence.sample(symbol,event,now,dict(state))
                self.sampled[symbol]=bucket
        elif topic.startswith('publicTrade.'):
            for trade in payload['data']:
                at=int(trade['T']);minute=at//MINUTE*MINUTE
                if trade['s']!=symbol or trade['S'] not in ('Buy','Sell') or at>now+1000 or now-at>5000:
                    raise ValueError('TRADE_TIME_OR_SYMBOL_INVALID')
                key=(symbol,str(trade['i']))
                if key in self.seen:continue
                self.seen[key]=now
                # Bounded dedup cache: overflow is a data-health failure, never a
                # silently double-counted flow. Multiple messages may share seq.
                while self.seen and next(iter(self.seen.values()))<now-10_000:self.seen.popitem(last=False)
                if len(self.seen)>200_000:raise ValueError('TRADE_DEDUP_CAPACITY')
                if minute<=self.finalized.get(symbol,-1):
                    self.evidence.db.execute('UPDATE flows SET complete=0 WHERE symbol=? AND ms=?',(symbol,minute))
                    continue
                row=self.flows.setdefault((symbol,minute),dict(buy=0.,sell=0.,count=0,
                                            complete=self.ready[symbol]<=minute))
                price,qty=number(trade['p']),number(trade['v'])
                if price<=0 or qty<=0:raise ValueError('INVALID_TRADE_VALUE')
                row['buy' if trade['S']=='Buy' else 'sell']+=price*qty
                row['count']+=1
        elif topic.startswith('kline.1.'):
            for bar in payload['data']:
                if bar.get('confirm') is not True:continue
                if str(bar['interval'])!='1':raise ValueError('WRONG_EVIDENCE_INTERVAL')
                row=[int(bar['start']),*[bar[k] for k in ('open','high','low','close','volume')]]
                if int(bar['end'])!=row[0]+MINUTE-1:raise ValueError('INVALID_KLINE_END')
                self.evidence.bar(symbol,row,now)

    def flush(self, now, force=False):
        latest=(now-2000)//MINUTE*MINUTE-MINUTE
        # An uninterrupted subscribed minute with no trades is recorded as zero
        # flow; its ratio remains undefined, not an invented neutral 50%.
        for symbol,start in self.ready.items():
            first=max((start//MINUTE)*MINUTE,self.finalized.get(symbol,start//MINUTE*MINUTE-MINUTE)+MINUTE)
            for minute in range(first,min(latest,first+5*MINUTE)+MINUTE,MINUTE):
                self.flows.setdefault((symbol,minute),dict(buy=0.,sell=0.,count=0,complete=start<=minute))
        for (symbol,minute),row in list(self.flows.items()):
            if not force and minute>latest:continue
            self.evidence.db.execute('INSERT INTO flows VALUES(?,?,?,?,?,?,?) ON CONFLICT(symbol,ms) '
                'DO UPDATE SET complete=0', (symbol,minute,row['buy'],row['sell'],row['count'],
                                            int(row['complete'] and not force),now))
            self.finalized[symbol]=max(minute,self.finalized.get(symbol,-1))
            del self.flows[(symbol,minute)]
        self.evidence.db.commit()


def wanted(source):
    row=source.execute("SELECT value FROM settings WHERE key='bybit_watch_scan_v1'").fetchone()
    if not row:return [],0
    state=json.loads(row[0]);markets=state.get('markets',{})
    symbols=sorted(markets,key=lambda s:(s!='BTCUSDT',-state.get('tickers',{}).get(s,{}).get('quoteVolume',0),s))
    return symbols[:MAX_SYMBOLS],len(symbols)


def run(root, stop=STOP):
    from websockets.sync.client import connect
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    evidence=Evidence(root/'observations.sqlite3')
    source_path=root/'research.sqlite3';gate=root/'bybit_cooldown.json';own_gate=root/'evidence_cooldown.json'
    feed=Feed(evidence);backoff=60;last_summary=0;last_poll=0;capture_verified=False
    with open(root/'evidence_worker.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        log('TA_EVIDENCE_BOOT',mode='OBSERVATION_ONLY',binance_requests=False,rest_requests=False,
            max_symbols=MAX_SYMBOLS,net_performance_verified=False)
        while not stop.is_set():
            if not source_path.exists() or cooldown(gate,clock()) or cooldown(own_gate,clock()):
                stop.wait(5);continue
            if shutil.disk_usage(root).free<512*1024*1024:
                log('TA_EVIDENCE_PAUSED',reason='LESS_THAN_512_MIB_FREE');stop.wait(60);continue
            try:
                with readonly(source_path) as source:symbols,total=wanted(source)
                if not symbols:stop.wait(5);continue
                with connect(URL,open_timeout=10,close_timeout=2,ping_interval=20,ping_timeout=20,
                             max_size=2_000_000,max_queue=64,compression=None,proxy=None) as ws:
                    pending={};subscribed=set();last_ping=0;last_flush=0;last_wire=clock();pending_since=clock()
                    def subscribe(names):
                        for offset in range(0,len(names),20):
                            batch=names[offset:offset+20];request='evidence-'+str(len(pending))+'-'+str(clock())
                            args=[topic for s in batch for topic in ('tickers.'+s,'publicTrade.'+s,'kline.1.'+s)]
                            pending[request]=batch
                            for s in batch:feed.ready.setdefault(s,2**63-1)
                            ws.send(dumps(dict(op='subscribe',args=args,req_id=request)))
                    subscribe(symbols)
                    log('TA_EVIDENCE_COVERAGE',selected=len(symbols),universe=total,limited=total>MAX_SYMBOLS)
                    while not stop.is_set():
                        now=clock()
                        if now-last_ping>=20_000:
                            ws.send('{"op":"ping"}');last_ping=now
                        try:
                            message=json.loads(ws.recv(timeout=1));last_wire=clock()
                            if message.get('retCode',message.get('ret_code')) in (10006,10003,10403,10429):
                                raise StreamRateLimit('WEBSOCKET_RATE_LIMIT')
                            if message.get('op')=='subscribe':
                                if message.get('success') is not True:raise ValueError('SUBSCRIPTION_REJECTED')
                                names=pending.pop(message.get('req_id'),[])
                                feed.connected(names,clock());subscribed.update(names)
                            else:feed.handle(message,clock())
                        except TimeoutError:pass
                        now=clock()
                        if now-last_wire>45_000:raise ValueError('STREAM_HEARTBEAT_STALE')
                        if pending and now-pending_since>30_000:raise ValueError('SUBSCRIPTION_ACK_TIMEOUT')
                        if now-last_flush>=1000:
                            feed.flush(now);last_flush=now
                        if now-last_poll>=MINUTE:
                            if shutil.disk_usage(root).free<512*1024*1024:raise ValueError('DISK_GUARD')
                            with readonly(source_path) as source:
                                evidence.register(source,now)
                                names,total=wanted(source)
                            additional=[s for s in names if s not in subscribed and not any(s in v for v in pending.values())]
                            if additional and len(subscribed)+len(additional)<=MAX_SYMBOLS and not cooldown(gate,now):
                                pending_since=now;subscribe(additional)
                            evidence.evaluate(now);last_poll=now
                            if not capture_verified:
                                summary=evidence.summary()
                                if summary['samples'] and summary['bars']:
                                    log('TA_EVIDENCE_CAPTURE',**summary,
                                        peak_rss_mb=round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,2))
                                    capture_verified=True
                        if now-last_summary>=5*MINUTE:
                            log('TA_EVIDENCE_HEARTBEAT',**evidence.summary(),messages=feed.messages,
                                connected_symbols=len(subscribed),universe=total,
                                peak_rss_mb=round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,2),
                                free_disk_mib=shutil.disk_usage(root).free//1024//1024)
                            last_summary=now
                        if now-last_wire<5000 and len(subscribed)==len(symbols):backoff=60
            except Exception as exc:
                # Local observation errors cannot leak into the signal worker.
                try:
                    feed.reset(clock());evidence.issue(clock(),type(exc).__name__)
                except Exception:pass
                response=getattr(exc,'response',None)
                code=getattr(response,'status_code',None)
                if code in (403,418,429,451) or isinstance(exc,StreamRateLimit):
                    delay=86400 if code in (418,451) else 600
                    try:delay=max(delay,int(getattr(response,'headers',{}).get('Retry-After',0)))
                    except (ValueError,TypeError):pass
                    temp=own_gate.with_suffix('.tmp');temp.write_text(dumps(dict(until_ms=clock()+delay*1000)))
                    temp.replace(own_gate)
                reason=str(exc) if isinstance(exc,(ValueError,StreamRateLimit)) and str(exc).replace('_','').isalnum() else type(exc).__name__
                log('TA_EVIDENCE_WAIT',reason=reason,retry_seconds=backoff)
                stop.wait(backoff);backoff=min(backoff*2,900)
        feed.reset(clock());evidence.db.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',required=True)
    args=parser.parse_args()
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:STOP.set())
    os.nice(10)
    resource.setrlimit(resource.RLIMIT_AS,(512*1024*1024,512*1024*1024))
    run(args.root)


if __name__=='__main__':main()
