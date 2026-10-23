"""Approval-gated paper/Telegram runner. Read-only Binance endpoints only.

No automatic orders. Full-history indicator cache preserves historical/live
seeds. SQLite is persisted to its own Git branch before outbound messages.
"""
import argparse
import base64
from dataclasses import asdict
from datetime import datetime,timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
from zoneinfo import ZoneInfo
import requests
from hourly_motor_engine import (Candle,Series,HOUR,DAY,MINUTE,RULES_HASH,STABLECOINS,
    daily_direction,evaluate,make_plan,universe_at,funding_percent,result)
from hourly_motor_backtest import check_funding,metrics
from hourly_motor_data import rest_funding
from trend_breakout_data import Archive,ms,iso,month
from trend_breakout_backtest import resolve_hour
from trend_breakout_live import Telegram,price_text


def now_ms():return int(time.time()*1000)


def approve(path,summary,report):
    a=json.loads(Path(path).read_text());s=json.loads(Path(summary).read_text())
    if not (a.get('approved_by_user') is True and a.get('rules_hash')==RULES_HASH and
            a.get('report_sha256')==hashlib.sha256(Path(report).read_bytes()).hexdigest() and
            s.get('rules_hash')==RULES_HASH and s.get('status')=='GEÇTİ' and all(s.get('checks',{}).values())):
        raise PermissionError('LIVE_DISABLED_REPORT_PASS_AND_USER_APPROVAL_REQUIRED')


class Market:
    def __init__(self,root):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True);self.archive=Archive(self.root/'archive')
        self.session=requests.Session();self.info=None

    def get(self,path,params):
        if path not in ('/fapi/v1/klines','/fapi/v1/exchangeInfo','/fapi/v1/ticker/price','/fapi/v1/aggTrades','/fapi/v1/fundingRate'):
            raise PermissionError('NOT_A_READ_ONLY_MARKET_ENDPOINT')
        r=self.session.get('https://fapi.binance.com'+path,params=params,timeout=30)
        if r.status_code==451:r=self.session.get('https://www.binance.com'+path,params=params,timeout=30)
        r.raise_for_status();return r.json()

    def candles(self,s,interval,start,end):
        out=[];at=start
        while at<end:
            rows=self.get('/fapi/v1/klines',dict(symbol=s,interval=interval,startTime=at,endTime=end-1,limit=1000))
            if not isinstance(rows,list):raise ValueError('INVALID_KLINE_RESPONSE')
            if not rows:break
            out.extend(Candle(int(r[0]),int(r[6])+1,*map(float,r[1:6]),float(r[7])) for r in rows if int(r[6])+1<=end)
            nxt=int(rows[-1][6])+1
            if nxt<=at:raise ValueError('KLINE_PAGINATION_STALLED')
            at=nxt
            if len(rows)<1000:break
        return out

    def history(self,s,interval,end):
        p=self.root/'history'/f'{s}-{interval}.json.gz';p.parent.mkdir(exist_ok=True)
        if p.exists():
            with gzip.open(p,'rt') as f:bars=[Candle(**b) for b in json.load(f)]
        else:
            # The same archive start/seed as the sealed backtest, not a moving short lookback.
            keys=self.archive.listing(f'data/futures/um/monthly/klines/{s}/{interval}/')['keys'];bars=[]
            for k in keys:
                if k.endswith('.zip') and ms(k[-11:-4]+'-01')<end:bars.extend(self.archive.candles(s,interval,k[-11:-4]))
            bars=[b for b in bars if b.close_ms<=end]
        start=bars[-1].close_ms if bars else ms('2020-01-01')
        bars.extend(self.candles(s,interval,start,end))
        bars=sorted({b.open_ms:b for b in bars}.values(),key=lambda b:b.open_ms)
        period=DAY if interval=='1d' else HOUR
        # Repair archive holes with original daily ZIPs; no interpolation.
        if bars:
            known={b.open_ms:b for b in bars};missing=sorted({t//DAY*DAY for t in range(bars[0].open_ms,bars[-1].close_ms,period) if t not in known})
            for d in missing:
                extra=self.archive.candles(s,interval,iso(d)[:10],daily=True)
                if interval=='1d' and not extra:
                    hh=[b for b in self.archive.candles(s,'1h',month(d)) if d<=b.open_ms<d+DAY]
                    if len(hh)==24 and all(b.open_ms==d+i*HOUR for i,b in enumerate(hh)):
                        extra=[Candle(d,d+DAY,hh[0].open,max(b.high for b in hh),min(b.low for b in hh),hh[-1].close,sum(b.volume for b in hh),sum(b.quote_volume for b in hh))]
                for b in extra:
                    if b.close_ms<=end:known.setdefault(b.open_ms,b)
            bars=sorted(known.values(),key=lambda b:b.open_ms)
        Series(bars,period)
        with gzip.open(p,'wt') as f:json.dump([asdict(b) for b in bars],f,separators=(',',':'))
        return bars

    def price(self,s):
        x=self.get('/fapi/v1/ticker/price',dict(symbol=s));stamp=now_ms()
        if float(x['price'])<=0:raise ValueError('INVALID_QUOTE')
        return float(x['price']),stamp

    def tick(self,s):
        if self.info is None:self.info={v['symbol']:v for v in self.get('/fapi/v1/exchangeInfo',{})['symbols']}
        return next((f['tickSize'] for f in self.info[s]['filters'] if f['filterType']=='PRICE_FILTER'),None)

    def universe(self,at):
        at=ms(month(at)+'-01');p=self.root/f'universe-{month(at)}.json'
        if p.exists():return json.loads(p.read_text())
        historical=Path('research/hourly_motor_v1/results/universe.json')
        if historical.exists():
            old=json.loads(historical.read_text())['months'].get(month(at))
            if old:p.write_text(json.dumps(old));return old
        symbols=[v.split('/')[-2] for v in self.archive.listing('data/futures/um/monthly/klines/',refresh=True)['prefixes']]
        all_bars={}
        for s in symbols:
            if not s.endswith('USDT') or '_' in s or s in STABLECOINS:continue
            rows=self.archive.range(s,'1d',at-30*DAY,at)
            if not rows:continue  # no published preceding-month trading history
            if rows[-1].close_ms<at:
                extra=self.candles(s,'1d',rows[-1].close_ms,at);rows.extend(extra)
            all_bars[s]=rows
        u=universe_at(all_bars,at)
        if not u:raise ValueError('UNIVERSE_UNAVAILABLE')
        p.write_text(json.dumps(u));return u

    def ticks(self,s,start,end):
        """Exact partial entry minute / deadline from observed public trades."""
        rows=[];params=dict(symbol=s,startTime=start,endTime=end,limit=1000)
        while True:
            batch=self.get('/fapi/v1/aggTrades',params)
            if not isinstance(batch,list):raise ValueError('INVALID_TRADE_RESPONSE')
            if not batch:break
            rows.extend((int(v['T']),float(v['p'])) for v in batch if start<=int(v['T'])<=end)
            if len(batch)<1000 or int(batch[-1]['T'])>=end:break
            params=dict(symbol=s,fromId=int(batch[-1]['a'])+1,limit=1000)
        return rows


class State:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True);self.db=sqlite3.connect(path)
        self.db.executescript('''PRAGMA synchronous=FULL;
        CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY,v TEXT);
        CREATE TABLE IF NOT EXISTS signals(id TEXT PRIMARY KEY,symbol TEXT,day INTEGER,status TEXT,payload TEXT);
        CREATE TABLE IF NOT EXISTS positions(id TEXT PRIMARY KEY,status TEXT,payload TEXT);
        CREATE TABLE IF NOT EXISTS outbox(id TEXT PRIMARY KEY,state TEXT,payload TEXT);
        CREATE TABLE IF NOT EXISTS errors(at INTEGER,symbol TEXT,reason TEXT);
        ''');self.db.commit()
    def meta(self,k,default=None):
        r=self.db.execute('SELECT v FROM meta WHERE k=?',(k,)).fetchone();return json.loads(r[0]) if r else default
    def set_meta(self,k,v):self.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',(k,json.dumps(v)));self.db.commit()
    def error(self,s,e):self.db.execute('INSERT INTO errors VALUES (?,?,?)',(now_ms(),s,str(e)[:200]));self.db.commit()
    def active(self):return [json.loads(r[0]) for r in self.db.execute("SELECT payload FROM positions WHERE status='OPEN'")]
    def checkpoint(self):self.db.commit()


class RemoteState:
    def __init__(self,repo,token,path):
        self.repo=repo;self.path=Path(path);self.sha=None;self.branch='hourly-motor-paper-state';self.session=requests.Session()
        self.session.headers.update(Authorization='Bearer '+token,Accept='application/vnd.github+json')
    def request(self,method,path,**kw):
        r=self.session.request(method,'https://api.github.com/repos/'+self.repo+path,timeout=30,**kw);r.raise_for_status();return r.json()
    def load(self,initialize=False):
        try:x=self.request('GET','/contents/hourly-motor.sqlite',params={'ref':self.branch})
        except requests.HTTPError as e:
            if e.response.status_code!=404 or not initialize:raise
            ref=self.request('GET','/git/ref/heads/'+os.environ.get('GITHUB_REF_NAME','main'))
            try:self.request('POST','/git/refs',json=dict(ref='refs/heads/'+self.branch,sha=ref['object']['sha']))
            except requests.HTTPError as err:
                if err.response.status_code!=422:raise
            return
        self.sha=x['sha']
        if x.get('encoding')!='base64':x=self.request('GET','/git/blobs/'+self.sha)
        body=base64.b64decode(x['content'])
        if not body.startswith(b'SQLite format 3\x00'):raise ValueError('INVALID_REMOTE_DB')
        self.path.parent.mkdir(parents=True,exist_ok=True);self.path.write_bytes(body)
    def save(self,state):
        state.checkpoint();payload=dict(message='Checkpoint Saatlik Motor paper state',branch=self.branch,content=base64.b64encode(self.path.read_bytes()).decode())
        if self.sha:payload['sha']=self.sha
        x=self.request('PUT','/contents/hourly-motor.sqlite',json=payload);self.sha=x['content']['sha']


def message(p):
    f=lambda x:price_text(x,p.get('tick_size'));long=p['side']=='LONG'
    deadline=datetime.fromtimestamp(p['deadline_ms']/1000,ZoneInfo('Europe/Istanbul')).strftime('%d/%m %H:%M:%S')
    return (f"{p['side']} | {p['symbol']} | 1–2 saat\n"
            f"Neden: günlük trend {'yukarı' if long else 'aşağı'}, 24s {'tepesi' if long else 'dibi'} {f(p['level'])} kırıldı, hacim {p['volume_ratio']:.2f} kat\n"
            f"Giriş ~{f(p['entry'])} | Stop {f(p['stop'])} | Hedef {f(p['target'])}\n"
            f"En geç {deadline} Türkiye saatinde çık\n"
            f"Sinyal: {iso(p['time'])} · Veri: futures\nOtomatik emir yok · %0,30 masraf varsayımı")


def chart(p,bars,path):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    bars=[b for b in bars if b.close_ms<=p['time']][-48:]
    plt.style.use('dark_background');fig,ax=plt.subplots(figsize=(10,6),dpi=150)
    for i,b in enumerate(bars):
        color='#45c9a3' if b.close>=b.open else '#f77387'
        ax.vlines(i,b.low,b.high,color=color,lw=1)
        ax.add_patch(Rectangle((i-.3,min(b.open,b.close)),.6,max(abs(b.close-b.open),p['atr']*.01),color=color))
    levels=[('24s kırılım','level','#70b8f0'),('Giriş','entry','white'),('STOP','stop','#f77387'),('HEDEF · 1,5R','target','#45c9a3')]
    spread=max([b.high for b in bars]+[p['target'],p['stop']])-min([b.low for b in bars]+[p['target'],p['stop']])
    last_y=-float('inf')
    for label,key,color in sorted(levels,key=lambda v:p[v[1]]):
        ax.axhline(p[key],color=color,ls='--',lw=1)
        y=max(p[key],last_y+spread*.065);last_y=y
        ax.annotate(label+' '+price_text(p[key],p.get('tick_size')),xy=(48,p[key]),xytext=(51,y),color=color,va='center',fontsize=10,arrowprops=dict(arrowstyle='-',color=color))
    ax.axvline(len(bars)-1,color='white',ls=':',alpha=.4);ax.set_xlim(-1,72)
    indices=list(range(0,len(bars),8));ax.set_xticks(indices,[iso(bars[i].open_ms)[5:16].replace('T',' ') for i in indices],rotation=20)
    kind='GEÇMİŞ TEST ÖRNEĞİ' if p.get('historical_example') else 'Paper öneri'
    ax.set_title(f"{p['symbol']} · {p['side']} · Saatlik Motor\n{kind} · Son48 saat · 1h mumlar · UTC",loc='left')
    ax.set_ylabel('USDT');ax.grid(alpha=.15);fig.tight_layout();fig.savefig(path);plt.close(fig)


def deliver(state,key,text,telegram,save,photo=None):
    if state.db.execute('SELECT 1 FROM outbox WHERE id=?',(key,)).fetchone():return
    payload=dict(text=text,photo=str(photo) if photo else None)
    state.db.execute('INSERT INTO outbox VALUES (?,?,?)',(key,'SENDING',json.dumps(payload)));save(state)
    try:sent,msgid=telegram.send(text,photo)
    except Exception as e:
        state.db.execute('UPDATE outbox SET state=? WHERE id=?',('UNKNOWN',key));state.error('',type(e).__name__);save(state);return
    payload.update(sent_ms=sent,message_id=msgid)
    state.db.execute('UPDATE outbox SET state=?,payload=? WHERE id=?',('SENT',json.dumps(payload),key));save(state)


def paper_exit(p,market,now):
    """Minute replay from actual quote time, including partial minute via trades."""
    until=min(now//MINUTE*MINUTE,p['deadline_ms']);cursor=p.get('checked_until',p['entry_ms'])
    if until<=cursor:return None,cursor
    sign=1 if p['side']=='LONG' else -1
    def tick_event(t,price):
        if sign*(price-p['stop'])<=0:return dict(outcome='STOP',exit=price,exit_ms=t,resolution='PUBLIC_TRADE')
        if sign*(price-p['target'])>=0:return dict(outcome='HEDEF',exit=p['target'],exit_ms=t,resolution='PUBLIC_TRADE')
    if cursor%MINUTE:
        boundary=min((cursor//MINUTE+1)*MINUTE,until)
        for t,price in market.ticks(p['symbol'],cursor,boundary-1):
            event=tick_event(t,price)
            if event:return event,t
        cursor=boundary
    full_end=until//MINUTE*MINUTE
    if full_end>cursor:
        minutes=market.candles(p['symbol'],'1m',cursor,full_end)
        if len(minutes)!=(full_end-cursor)//MINUTE or any(b.open_ms!=cursor+i*MINUTE for i,b in enumerate(minutes)):
            raise ValueError('LIVE_EXIT_MINUTE_DATA_GAP')
        for b in minutes:
            # Same price/stop/target resolver; ambiguous minute conservatively STOP.
            event=resolve_hour(p,b,lambda *a:[])
            if event:return event,event['exit_ms']
        cursor=full_end
    if until>cursor:
        for t,price in market.ticks(p['symbol'],cursor,until-1):
            event=tick_event(t,price)
            if event:return event,t
        cursor=until
    if now>=p['deadline_ms']:
        ticks=market.ticks(p['symbol'],p['deadline_ms'],min(now,p['deadline_ms']+MINUTE))
        if not ticks:raise ValueError('LIVE_TIMEOUT_PRICE_NOT_OBSERVED')
        t,price=ticks[0]
        return dict(outcome='TIMEOUT',exit=price,exit_ms=t,resolution='FIRST_PUBLIC_TRADE_AT_DEADLINE'),cursor
    return None,cursor


def scan(state,market,telegram,save,now):
    boundary=now//HOUR*HOUR
    # Exits precede new entries; quote-time deadline is retained across reruns.
    for p in state.active():
        try:
            event,checked=paper_exit(p,market,now);p['checked_until']=checked
            if event:
                funding=market.get('/fapi/v1/fundingRate',dict(symbol=p['symbol'],startTime=p['entry_ms']-8*HOUR,endTime=now,limit=1000))
                events=[dict(time=int(v['fundingTime'])//1000*1000,rate=float(v['fundingRate'])) for v in funding]
                try:r=result(p,event['exit'],check_funding(p,event['exit_ms'],events));status='CLOSED'
                except ValueError:r=dict(net_pct=None,net_r=None);status='FUNDING_PENDING'
                p.update(event);p.update(r);p['status']=status
                state.db.execute('UPDATE positions SET status=?,payload=? WHERE id=?',(status,json.dumps(p),p['id']));save(state)
                title='ÇIKIŞ ZAMANI' if p['outcome']=='TIMEOUT' else p['outcome']
                net=f"{p['net_pct']:+.3f}% / {p['net_r']:+.3f}R" if p['net_pct'] is not None else 'funding doğrulanması bekleniyor'
                deliver(state,'exit:'+p['id'],f"{title} | {p['symbol']}\nPaper çıkış: {p['exit']:.10g}\nNet: {net}\nVeri futures · Otomatik emir yok · %0,30 masraf varsayımı",telegram,save)
            else:state.db.execute('UPDATE positions SET payload=? WHERE id=?',(json.dumps(p),p['id']));save(state)
        except Exception as e:state.error(p['symbol'],type(e).__name__+':'+str(e)[:100]);save(state)
    # Funding pending is reconciled when the following actual settlement is published.
    for row in state.db.execute("SELECT id,payload FROM positions WHERE status='FUNDING_PENDING'").fetchall():
        p=json.loads(row[1])
        try:
            raw=market.get('/fapi/v1/fundingRate',dict(symbol=p['symbol'],startTime=p['entry_ms']-8*HOUR,endTime=now,limit=1000))
            f=[dict(time=int(v['fundingTime'])//1000*1000,rate=float(v['fundingRate'])) for v in raw]
            p.update(result(p,p['exit'],check_funding(p,p['exit_ms'],f)));p['status']='CLOSED'
            state.db.execute('UPDATE positions SET status=?,payload=? WHERE id=?',('CLOSED',json.dumps(p),p['id']));save(state)
        except ValueError:pass
    last=state.meta('last_hour',boundary-HOUR)
    btc=Series(market.history('BTCUSDT','1d',boundary//DAY*DAY),DAY);cache={};directions={}
    for at in range(last+HOUR,boundary+1,HOUR):
        failures=[]
        for row in market.universe(at):
            s=row['symbol'];key=f'{s}:{at}'
            if state.db.execute('SELECT 1 FROM signals WHERE id=?',(key,)).fetchone():continue
            try:
                if s not in cache:cache[s]=(Series(market.history(s,'1h',boundary),HOUR),Series(market.history(s,'1d',boundary//DAY*DAY),DAY))
                h,d=cache[s];day=at//DAY*DAY
                if (s,day) not in directions:directions[s,day]=daily_direction(s,d,btc,at)
                sig=evaluate(s,h,directions[s,day],at)
                if sig['status']=='SIGNAL':
                    if state.db.execute("SELECT 1 FROM signals WHERE symbol=? AND day=? AND status='PLAN'",(s,day)).fetchone():sig.update(status='SKIPPED',reason='DAILY_QUOTA')
                    elif now>=at+2*HOUR:sig.update(status='MISSED',reason='SIGNAL_2H_WINDOW_ALREADY_ELAPSED')
                    else:
                        price,stamp=market.price(s);sig=make_plan(sig,price,stamp);sig.update(id=key,tick_size=market.tick(s),price_ms=stamp)
                        idx=h.index(at)+1
                        sig['next_hour_open']=h.bars[idx].open if idx<len(h.bars) and h.bars[idx].open_ms==at else None
                        sig['entry_difference_pct']=100*(price/sig['next_hour_open']-1) if sig['next_hour_open'] else None
                state.db.execute('INSERT INTO signals VALUES (?,?,?,?,?)',(key,s,day,sig['status'],json.dumps(sig)))
                if sig['status']=='PLAN':
                    state.db.execute('INSERT INTO positions VALUES (?,?,?)',(key,'OPEN',json.dumps(sig)));save(state)
                    path=market.root/'signal.png';chart(sig,h.bars,path);deliver(state,'entry:'+key,message(sig),telegram,save,path)
            except Exception as e:failures.append(s);state.error(s,type(e).__name__+':'+str(e)[:100]);save(state)
        if failures:break  # retain hour watermark; successfully processed symbols deduplicate on retry
        state.set_meta('last_hour',at);save(state)
    # Persist next-open slippage once the corresponding hourly bar has closed.
    for row in state.db.execute("SELECT id,payload FROM signals WHERE status='PLAN'").fetchall():
        p=json.loads(row[1])
        if p.get('next_hour_open') is None and p['time']+HOUR<=boundary:
            bb=market.candles(p['symbol'],'1h',p['time'],p['time']+HOUR)
            if bb:p['next_hour_open']=bb[0].open;p['entry_difference_pct']=100*(p['entry']/bb[0].open-1);state.db.execute('UPDATE signals SET payload=? WHERE id=?',(json.dumps(p),row[0]))
    iso_week=datetime.fromtimestamp(now/1000,timezone.utc).strftime('%G-W%V')
    if state.meta('weekly_sent')!=iso_week:
        rows=[json.loads(r[0]) for r in state.db.execute("SELECT payload FROM positions WHERE status='CLOSED'")]
        rows=[r for r in rows if r['exit_ms']>=now-7*DAY];m=metrics(rows)
        text=f"SAATLİK MOTOR · HAFTALIK\nKapanan: {m['n']}"
        if m['n']:text+=f"\nOrtalama net: %{m['mean_net_pct']:+.3f} · Toplam: {m['total_net_r']:+.3f}R"
        text+='\nVeri futures · Otomatik emir yok · %0,30 masraf varsayımı'
        deliver(state,'weekly:'+iso_week,text,telegram,save);state.set_meta('weekly_sent',iso_week);save(state)


def main():
    p=argparse.ArgumentParser();p.add_argument('--approval',default='hourly_motor_approval.json');p.add_argument('--summary',default='research/hourly_motor_v1/results/summary.json');p.add_argument('--report',default='research/hourly_motor_v1/results/report.md')
    p.add_argument('--cache',default='.hourly-motor-cache');p.add_argument('--db',default='hourly-motor.sqlite');p.add_argument('--initialize-state',action='store_true');a=p.parse_args()
    approve(a.approval,a.summary,a.report)
    remote=RemoteState(os.environ['GITHUB_REPOSITORY'],os.environ['GH_TOKEN'],a.db);remote.load(a.initialize_state)
    state=State(a.db);market=Market(a.cache);telegram=Telegram(os.environ['TELEGRAM_BOT_TOKEN'],os.environ['TELEGRAM_CHAT_ID'])
    started=now_ms();scan(state,market,telegram,remote.save,started)
    # An hourly job can start seconds before a two-hour quote-time deadline.
    # Wait for nearby deadlines so it does not defer the notice an entire hour.
    while True:
        current=now_ms();near=[p['deadline_ms'] for p in state.active() if current<p['deadline_ms']<=current+10*MINUTE]
        if not near or min(near)-started>50*MINUTE:break
        due=min(near)+1000
        while now_ms()<due:time.sleep(min(30,max(.1,(due-now_ms())/1000)))
        scan(state,market,telegram,remote.save,now_ms())


if __name__=='__main__':main()
