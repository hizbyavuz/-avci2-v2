"""Paper + Telegram runner. No exchange credentials or order endpoints.

Default is dry run. Sending requires a report-bound, explicit approval file.
Live state is isolated SQLite; a durable checkpoint is written BEFORE every
Telegram attempt. An uncertain delivery is never silently retried.
"""
import argparse
import base64
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import time
import requests
from trend_breakout_engine import HOUR, DAY, MINUTE, RULES_HASH, Candle, Series, evaluate, make_plan, choose_plans
from trend_breakout_data import Archive, collect_universe, iso, ms, month
from trend_breakout_backtest import resolve_hour, touch, gap_exit, funding_cost, metrics


def now_ms(): return int(time.time()*1000)


def price_text(value, tick=None):
    if tick:
        unit=Decimal(str(tick)); rounded=(Decimal(str(value))/unit).quantize(Decimal('1'),rounding=ROUND_HALF_UP)*unit
        return format(rounded,'f')
    return f'{value:.10g}'


def message(plan):
    f=lambda x:price_text(x,plan.get('tick_size'))
    long=plan['side']=='LONG'
    return (f"{plan['side']} ÖNERİ | {plan['symbol']} | 1h\n"
            f"Giriş referansı: {f(plan['entry'])} (mesaj öncesi son fiyat)\n"
            f"Stop: {f(plan['stop'])} | Hedef (2R): {f(plan['target'])}\n"
            f"Neden: 4h trend {'yukarı' if long else 'aşağı'}, 5 günlük {'tepe' if long else 'dip'} "
            f"{f(plan['level'])} kırıldı, hacim ortalamanın {plan['volume_ratio']:.2f} katı\n"
            f"Sinyal kapanışı: {iso(plan['time'])}\n"
            f"Fiyat zamanı: {iso(plan['price_ms'])}\n"
            f"Veri: {plan['data_source']} · Otomatik emir yok · %0,30 masraf varsayımı\n"
            f"{'Futures erişimi 451: spot fiyatı kullanıldı. Futures işlem fiyatı farklı olabilir. ' if plan['data_source']=='spot' else ''}"
            f"Paper kayıt; kârlılık garantisi yok.")


def chart(plan, bars, path):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    history=[b for b in bars if b.close_ms<=plan['time']][-121:]
    if not history: raise ValueError('NO_CHART_DATA')
    plt.style.use('dark_background')
    fig,ax=plt.subplots(figsize=(10,7),dpi=150)
    fig.patch.set_facecolor('#111722');ax.set_facecolor('#111722')
    for i,b in enumerate(history):
        color='#42cfac' if b.close>=b.open else '#ff667c'
        ax.vlines(i,b.low,b.high,color=color,lw=.8)
        ax.add_patch(Rectangle((i-.32,min(b.open,b.close)),.64,max(abs(b.close-b.open),plan['atr']*.008),facecolor=color))
    ax.axhspan(plan['band_low'],plan['band_high'],color='#6caeea',alpha=.12,label='Önceki 5 günlük bant')
    x=len(history)-1; ax.axvline(x,color='white',alpha=.35,ls=':')
    ax.annotate('KIRILIM MUMU',(x,history[-1].high),xytext=(-100,25),textcoords='offset points',
                arrowprops=dict(arrowstyle='->',color='white'),fontsize=9)
    lines=[('GİRİŞ',plan['entry'],'#e6ebf2'),('SL',plan['stop'],'#ff667c'),('TP · 2R',plan['target'],'#42cfac')]
    for label,value,color in lines:
        ax.axhline(value,color=color,ls='--',lw=1.2)
        ax.text(len(history)+3,value,label+'  '+price_text(value,plan.get('tick_size')),color=color,va='center',fontsize=10,weight='bold',bbox=dict(facecolor='#111722',edgecolor='none',pad=3,alpha=.9))
    ax.fill_between([x+1,len(history)+30],plan['entry'],plan['target'],color='#42cfac',alpha=.18)
    ax.fill_between([x+1,len(history)+30],plan['entry'],plan['stop'],color='#ff667c',alpha=.18)
    ax.set_xlim(-1,len(history)+38);ax.set_ylabel('Fiyat · USDT');ax.grid(alpha=.12)
    indices=list(range(0,len(history),24));ax.set_xticks(indices,[iso(history[i].open_ms)[5:16].replace('T',' ') for i in indices],rotation=20)
    kind='GEÇMİŞ VERİ · ÇİZİM ÖRNEĞİ' if plan.get('historical_example') else 'Giriş / Stop / Hedef — paper plan'
    ax.set_title(f"{plan['symbol']} · {plan['side']} · 1h\n{kind}",loc='left',pad=18,fontsize=16)
    fig.text(.08,.03,f"Veri: {plan['data_source']} · {iso(plan['time'])} UTC · Otomatik emir yok · %0,30 masraf",fontsize=9)
    fig.subplots_adjust(left=.09,right=.98,top=.86,bottom=.16)
    fig.savefig(path,facecolor=fig.get_facecolor());plt.close(fig)


class State:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(self.path)
        self.db.execute('PRAGMA journal_mode=DELETE')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS signals(id TEXT PRIMARY KEY,symbol TEXT,close_ms INTEGER,status TEXT,payload TEXT);
        CREATE TABLE IF NOT EXISTS positions(id TEXT PRIMARY KEY,state TEXT,payload TEXT);
        CREATE TABLE IF NOT EXISTS outbox(id TEXT PRIMARY KEY,state TEXT,sent_ms INTEGER,payload TEXT);
        CREATE TABLE IF NOT EXISTS errors(time_ms INTEGER,symbol TEXT,reason TEXT);
        CREATE TABLE IF NOT EXISTS universe(month TEXT PRIMARY KEY,payload TEXT);
        CREATE TABLE IF NOT EXISTS pending(id TEXT PRIMARY KEY,symbol TEXT,close_ms INTEGER);
        ''');self.db.commit()

    def meta(self,key,default=None):
        row=self.db.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_meta(self,key,value):
        self.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',(key,json.dumps(value)));self.db.commit()

    def error(self,symbol,reason):
        self.db.execute('INSERT INTO errors VALUES (?,?,?)',(now_ms(),symbol,reason));self.db.commit()

    def seen(self,id):return self.db.execute('SELECT 1 FROM signals WHERE id=?',(id,)).fetchone() is not None

    def signal(self,id,p):
        self.db.execute('INSERT INTO signals VALUES (?,?,?,?,?)',(id,p['symbol'],p['time'],p['status'],json.dumps(p)));self.db.commit()

    def active(self):
        return [json.loads(x[0]) for x in self.db.execute("SELECT payload FROM positions WHERE state IN ('OPEN','UNVERIFIED')")]

    def checkpoint(self):self.db.commit()


class GitState:
    """Small paper ledger only, on a separate branch. Candle data never enters git."""
    def __init__(self,repo,token,path,branch='trend-breakout-paper-state'):
        self.repo=repo;self.path=Path(path);self.branch=branch;self.sha=None
        self.session=requests.Session()
        self.session.headers.update(Authorization='Bearer '+token,Accept='application/vnd.github+json')
        self.base='https://api.github.com/repos/'+repo

    def request(self,method,path,**kwargs):
        r=self.session.request(method,self.base+path,timeout=30,**kwargs)
        r.raise_for_status();return r.json() if r.content else {}

    def load(self,initialize=False):
        try:
            x=self.request('GET','/contents/trend-breakout-paper.sqlite',params={'ref':self.branch})
        except requests.HTTPError as e:
            if e.response.status_code!=404 or not initialize:raise
            # Initialization is a separate deliberate workflow input, after report approval.
            ref=self.request('GET','/git/ref/heads/'+os.environ.get('GITHUB_REF_NAME','main'))
            try:self.request('POST','/git/refs',json={'ref':'refs/heads/'+self.branch,'sha':ref['object']['sha']})
            except requests.HTTPError as err:
                if err.response.status_code!=422:raise
            return
        self.sha=x['sha']
        if x.get('encoding')!='base64':
            x=self.request('GET','/git/blobs/'+self.sha)
        body=base64.b64decode(x['content'])
        if not body.startswith(b'SQLite format 3\x00'):raise ValueError('INVALID_REMOTE_STATE')
        self.path.parent.mkdir(parents=True,exist_ok=True);self.path.write_bytes(body)

    def save(self,state):
        state.checkpoint()
        body=self.path.read_bytes()
        payload=dict(message='Checkpoint Trend Breakout paper ledger',branch=self.branch,
                     content=base64.b64encode(body).decode())
        if self.sha:payload['sha']=self.sha
        x=self.request('PUT','/contents/trend-breakout-paper.sqlite',json=payload)
        self.sha=x['content']['sha']


class Market:
    """Read-only REST with explicit 451 fallback. Never requests account/order APIs."""
    def __init__(self):self.session=requests.Session();self.sources={};self.symbol_info={}

    def get(self,path,params,source='futures'):
        root='https://fapi.binance.com' if source=='futures' else 'https://data-api.binance.vision'
        r=self.session.get(root+path,params=params,timeout=25)
        if r.status_code in (418,429):raise RuntimeError('BINANCE_RATE_LIMIT: retry next run')
        r.raise_for_status();return r.json()

    def candles(self,symbol,interval,start,end,source=None):
        source=source or self.sources.get(symbol,'futures')
        path='/fapi/v1/klines' if source=='futures' else '/api/v3/klines'
        rows=[];at=start
        while at<end:
            try:raw=self.get(path,dict(symbol=symbol,interval=interval,startTime=at,endTime=end-1,limit=1000),source)
            except requests.HTTPError as e:
                if source=='futures' and e.response.status_code==451:
                    self.sources[symbol]='spot'
                    return self.candles(symbol,interval,start,end,'spot')
                raise
            if not raw:break
            batch=[Candle(int(r[0]),int(r[6])+1,*map(float,r[1:6]),float(r[7])) for r in raw if int(r[6])+1<=end]
            rows.extend(batch)
            nxt=int(raw[-1][6])+1
            if nxt<=at:raise RuntimeError('REST_PAGINATION_STALLED')
            at=nxt
            if len(raw)<1000:break
        self.sources[symbol]=source
        return rows

    def price(self,symbol,source):
        path='/fapi/v1/ticker/price' if source=='futures' else '/api/v3/ticker/price'
        x=self.get(path,dict(symbol=symbol),source)
        return float(x['price']),now_ms()

    def tick(self,symbol,source):
        if source not in self.symbol_info:
            path='/fapi/v1/exchangeInfo' if source=='futures' else '/api/v3/exchangeInfo'
            x=self.get(path,{},source)
            self.symbol_info[source]={s['symbol']:s for s in x['symbols']}
        s=self.symbol_info[source].get(symbol,{})
        return next((f['tickSize'] for f in s.get('filters',[]) if f['filterType']=='PRICE_FILTER'),None)

    def funding(self,symbol,start,end):
        rows=[];at=start
        try:
            while at<end:
                raw=self.get('/fapi/v1/fundingRate',dict(symbol=symbol,startTime=at,endTime=end,limit=1000))
                if not raw:break
                rows.extend(dict(time=int(r['fundingTime']),rate=float(r['fundingRate']),interval_hours=8) for r in raw)
                nxt=int(raw[-1]['fundingTime'])+1
                if nxt<=at:raise RuntimeError('FUNDING_PAGINATION_STALLED')
                at=nxt
                if len(raw)<1000:break
            return rows, sorted({month(r['time']) for r in rows})
        except (requests.RequestException,RuntimeError):return [],[]


def approval(path,report):
    x=json.loads(Path(path).read_text())
    if x.get('approved_by_user') is not True or x.get('rules_hash')!=RULES_HASH:
        raise PermissionError('LIVE_NOT_APPROVED')
    digest=hashlib.sha256(Path(report).read_bytes()).hexdigest()
    if x.get('report_sha256')!=digest:raise PermissionError('APPROVAL_REPORT_MISMATCH')
    return x


class Telegram:
    def __init__(self,token,chat):self.token=token;self.chat=chat

    def send(self,text,photo=None):
        endpoint='sendPhoto' if photo else 'sendMessage'
        url='https://api.telegram.org/bot'+self.token+'/'+endpoint
        try:
            if photo:
                with open(photo,'rb') as f:r=requests.post(url,data={'chat_id':self.chat,'caption':text},files={'photo':f},timeout=30)
            else:r=requests.post(url,json={'chat_id':self.chat,'text':text},timeout=30)
            if not r.ok:raise RuntimeError('TELEGRAM_HTTP_'+str(r.status_code))
            x=r.json()
            if not x.get('ok'):raise RuntimeError('TELEGRAM_API_REJECTED')
            return x['result']['date']*1000,x['result']['message_id']
        except requests.RequestException as e:
            # requests errors can contain the bot-token URL. Never log the URL.
            raise RuntimeError('TELEGRAM_DELIVERY_UNKNOWN_'+type(e).__name__) from None


def deliver(state,id,plan,photo,telegram,durable_save):
    if state.db.execute('SELECT 1 FROM outbox WHERE id=?',(id,)).fetchone():return
    state.db.execute('INSERT INTO outbox VALUES (?,?,?,?)',(id,'SENDING',None,json.dumps(plan)))
    state.db.execute('INSERT OR REPLACE INTO positions VALUES (?,?,?)',(id,'OPEN',json.dumps(dict(plan,id=id))))
    state.db.commit();durable_save(state)  # If persistence fails, no outbound message.
    try:
        sent,message_id=telegram.send(message(plan),photo)
    except RuntimeError as e:
        state.db.execute('UPDATE outbox SET state=? WHERE id=?',('UNKNOWN',id))
        state.error(plan['symbol'],str(e));durable_save(state);return
    plan=dict(plan,id=id,message_sent_ms=sent,telegram_message_id=message_id)
    state.db.execute('UPDATE outbox SET state=?,sent_ms=?,payload=? WHERE id=?',('SENT',sent,json.dumps(plan),id))
    state.db.execute('UPDATE positions SET payload=? WHERE id=?',(json.dumps(plan),id))
    state.db.execute('UPDATE signals SET payload=? WHERE id=?',(json.dumps(plan),id))
    state.db.commit();durable_save(state)


def paper_result(plan,minutes,at,funding=(),coverage=()):
    """Minute paper monitor, excluding price action before the quote timestamp.

    A touch during the partially observed entry minute cannot be ordered from
    OHLC: report UNVERIFIED instead of inventing a fill. Full-minute ambiguity
    remains conservative STOP. Open/incomplete is distinct from data failure.
    """
    floor=plan['entry_ms']//MINUTE*MINUTE;deadline=plan['entry_ms']+168*HOUR
    rows=[b for b in minutes if floor<=b.open_ms and b.close_ms<=at]
    result=None;favorable=[];adverse=[];expected=floor
    sign=1 if plan['side']=='LONG' else -1
    for b in rows:
        if b.open_ms!=expected:return dict(outcome='UNVERIFIED_DATA_GAP',net_r=None,exit_ms=None)
        expected=b.close_ms
        if b.open_ms<plan['entry_ms']:
            if any(touch(plan,b)):return dict(outcome='UNVERIFIED_ENTRY_MINUTE',net_r=None,exit_ms=None)
            continue
        if b.open_ms<plan['entry_ms']+24*HOUR:
            favorable.append(100*sign*((b.high if sign==1 else b.low)/plan['entry']-1))
            adverse.append(-100*sign*((b.low if sign==1 else b.high)/plan['entry']-1))
        if result is not None:continue
        if b.close_ms>deadline:
            break
        gap=gap_exit(plan,b);sl,tp=touch(plan,b)
        if gap:result=dict(outcome=gap[0],exit=gap[1],exit_ms=gap[2],resolution='1M_OPEN_GAP')
        elif sl or tp:result=dict(outcome='STOP' if sl else 'HEDEF',exit=plan['stop'] if sl else plan['target'],exit_ms=b.close_ms-1,resolution='1M_BOTH_STOP' if sl and tp else 'LIVE_1M')
    if result is None and at>=deadline:
        eligible=[b for b in rows if b.close_ms<=deadline]
        if not eligible or deadline-eligible[-1].close_ms>=MINUTE:return dict(outcome='UNVERIFIED_DATA_GAP',net_r=None,exit_ms=None)
        b=eligible[-1];result=dict(outcome='TIMEOUT',exit=b.close,exit_ms=b.close_ms-1,resolution='TIMEOUT_LAST_COMPLETE_MINUTE')
    if result is None:return None
    result.update(funding_cost(plan,result['exit_ms'],funding,coverage,[]))
    gross=100*sign*(result['exit']/plan['entry']-1)
    result.update(gross_return_pct=gross,cost_pct=.30,net_r=(gross-.30-result['funding_pct'])/plan['risk_pct'],
                  stress_net_r=(gross-.60-result['funding_pct'])/plan['risk_pct'])
    if at>=plan['entry_ms']+24*HOUR and len(favorable)>=1439:
        mfe=max(0.,max(favorable));mae=max(0.,max(adverse))
        result.update(mfe_pct=mfe,mae_pct=mae,mfe_r=mfe/plan['risk_pct'],mae_r=mae/plan['risk_pct'],direction_correct=mfe>mae)
    else:result.update(mfe_pct=None,mae_pct=None,mfe_r=None,mae_r=None,direction_correct=None)
    return result


def update_positions(state,market,at):
    rows=state.db.execute("SELECT id,state,payload FROM positions WHERE state='OPEN' OR (state='CLOSED' AND json_extract(payload,'$.direction_correct') IS NULL)").fetchall()
    for id,status,payload in rows:
        p=json.loads(payload)
        try:
            end=min(at,max(p['entry_ms']+24*HOUR,p.get('exit_ms') or at))
            bars=market.candles(p['symbol'],'1m',p['entry_ms']//MINUTE*MINUTE,end,p['data_source'])
            # A source switch during an open trade invalidates its replay; never mix instruments.
            if market.sources[p['symbol']]!=p['data_source']:raise RuntimeError('OPEN_POSITION_SOURCE_CHANGED')
            funding,coverage=market.funding(p['symbol'],p['entry_ms'],at)
            result=paper_result(p,bars,at,funding,coverage)
            if result:
                updated=dict(p,**result)
                state.db.execute('UPDATE positions SET state=?,payload=? WHERE id=?',('CLOSED' if result.get('net_r') is not None else 'UNVERIFIED',json.dumps(updated),id))
        except (requests.RequestException,RuntimeError,ValueError) as e:state.error(p['symbol'],type(e).__name__+': PAPER_DATA_ERROR')
    state.db.commit()


def weekly(state,telegram,at,save):
    d=datetime.fromtimestamp(at/1000,timezone.utc)
    monday=(at//DAY-d.weekday())*DAY
    key=iso(monday)[:10]
    if state.meta('weekly_sent')==key:return
    rows=[json.loads(r[0]) for r in state.db.execute("SELECT payload FROM positions WHERE state='CLOSED'")]
    rows=[r for r in rows if monday-7*DAY<=r['exit_ms']<monday]
    if monday <= state.meta('created_ms',at):return
    lines=['Trend Kırılım Motoru · Haftalık paper özeti',iso(monday-7*DAY)+' — '+iso(monday)]
    for side in ('ALL','LONG','SHORT'):
        m=metrics(rows if side=='ALL' else [r for r in rows if r['side']==side])
        fmt=lambda x:'doğrulanmadı' if x is None else f'{x:.3f}'
        lines.append(f"{side}: {m['trades']} işlem · ort. {fmt(m['mean_net_r'])} R · toplam {fmt(m['total_net_r'])} R · hedef önce %{fmt(m['target_first_pct'])} · yön %{fmt(m['direction_accuracy_pct'])} ({m['direction_samples']} ölçüm)")
    # At-most-once on uncertain Telegram delivery, same durable discipline as signals.
    state.set_meta('weekly_sent',key);save(state)
    try:telegram.send('\n'.join(lines))
    except RuntimeError as e:state.error('WEEKLY',str(e));save(state)


def cycle(state,market,universe_provider,telegram=None,save=lambda s:s.checkpoint(),at=None,chart_dir='trend-breakout-charts'):
    at=at or now_ms();latest=at//HOUR*HOUR
    start=state.meta('last_hour',latest-HOUR)+HOUR
    if state.meta('created_ms') is None:state.set_meta('created_ms',at)
    seed=state.meta('history_seed')
    if seed is None:
        seed=ms(month(latest)+'-01')-60*DAY;state.set_meta('history_seed',seed)
    update_positions(state,market,at//MINUTE*MINUTE)
    cache={};failed_symbols=set()
    pending={r[0] for r in state.db.execute('SELECT DISTINCT close_ms FROM pending')}
    for t in sorted(set(range(start,latest+1,HOUR))|pending):
        universe=universe_provider(month(t))
        if len(universe)!=50:raise RuntimeError('UNVERIFIED_MONTHLY_UNIVERSE')
        candidates=[];failed=False
        for row in universe:
            symbol=row['symbol'];id=f'{symbol}:{t}'
            if state.seen(id):continue
            if t<start and not state.db.execute('SELECT 1 FROM pending WHERE id=?',(id,)).fetchone():continue
            try:
                if symbol in failed_symbols:
                    state.db.execute('INSERT OR REPLACE INTO pending VALUES (?,?,?)',(id,symbol,t));failed=True;continue
                if symbol not in cache:
                    h=market.candles(symbol,'1h',seed,latest)
                    source=market.sources[symbol]
                    q=market.candles(symbol,'4h',seed,latest,source)
                    if market.sources[symbol]!=source:
                        h=market.candles(symbol,'1h',seed,latest,'spot');source='spot'
                    cache[symbol]=(Series(h,HOUR),Series(q,4*HOUR),source)
                h,q,source=cache[symbol];sig=evaluate(symbol,h,q,t)
                if sig['reason'] in ('DATA_GAP','STALE_OR_UNCLOSED_DATA','INSUFFICIENT_HISTORY'):
                    state.error(symbol,sig['reason']);failed=True
                    state.db.execute('INSERT OR REPLACE INTO pending VALUES (?,?,?)',(id,symbol,t));continue
                state.db.execute('DELETE FROM pending WHERE id=?',(id,))
                if sig['status']!='SIGNAL':continue
                price,quote_time=market.price(symbol,source)
                p=make_plan(sig,price,quote_time,source)
                p.update(id=id,price_ms=quote_time,tick_size=market.tick(symbol,source))
                i=h.index(t)
                next_bar=h.bars[i+1] if i+1<len(h.bars) and h.bars[i+1].open_ms==t else None
                p.update(next_hour_open=next_bar.open if next_bar else None,
                         entry_vs_next_open_pct=100*(price/next_bar.open-1) if next_bar else None)
                if p['status']=='PLAN':candidates.append(p)
                else:state.signal(id,p)
            except (requests.RequestException,ValueError,RuntimeError) as e:
                state.error(symbol,type(e).__name__+': MARKET_DATA_ERROR');failed=True
                failed_symbols.add(symbol)
                state.db.execute('INSERT OR REPLACE INTO pending VALUES (?,?,?)',(id,symbol,t))
        # Failed symbols remain pending; available signals still use volume priority.
        # No invented data and no global outage caused by one unavailable instrument.
        if failed:
            state.error('BATCH',f'PARTIAL_SCAN:{t}');save(state)
        for p in sorted(candidates,key=lambda x:(-x['quote_volume_24h'],x['symbol'])):
            if telegram:
                # Refresh immediately before chart/message, after the potentially long scan.
                price,quoted=market.price(p['symbol'],p['data_source'])
                old=p;p=make_plan(dict(p,status='SIGNAL'),price,quoted,p['data_source'])
                p.update(price_ms=quoted,id=old['id'],tick_size=old.get('tick_size'))
                if p.get('next_hour_open'):
                    p['entry_vs_next_open_pct']=100*(price/p['next_hour_open']-1)
                if p['status']!='PLAN':state.signal(p['id'],p);continue
            accepted,skipped=choose_plans([p],state.active(),True)
            if skipped:
                state.signal(p['id'],skipped[0]);continue
            if telegram:
                dest=Path(chart_dir)/f"{p['symbol']}-{t}.png";dest.parent.mkdir(parents=True,exist_ok=True)
                chart(p,cache[p['symbol']][0].bars,dest)
                state.signal(p['id'],p)
                deliver(state,p['id'],p,dest,telegram,save)
            else:
                state.signal(p['id'],p)
                state.db.execute('INSERT INTO positions VALUES (?,?,?)',(p['id'],'OPEN',json.dumps(p)));state.db.commit()
        state.set_meta('last_hour',max(t,state.meta('last_hour',t)));save(state)
    # Fill the next-bar-open reference once the bar becomes available; never use it for entry.
    for id,payload in state.db.execute("SELECT id,payload FROM signals WHERE json_extract(payload,'$.entry') IS NOT NULL AND json_extract(payload,'$.next_hour_open') IS NULL").fetchall():
        p=json.loads(payload)
        try:
            bars=market.candles(p['symbol'],'1h',p['time'],p['time']+HOUR,p['data_source']) if latest>=p['time']+HOUR else []
            if bars:
                p['next_hour_open']=bars[0].open;p['entry_vs_next_open_pct']=100*(p['entry']/bars[0].open-1)
                state.db.execute('UPDATE signals SET payload=? WHERE id=?',(json.dumps(p),id))
        except requests.RequestException:state.error(p['symbol'],'NEXT_OPEN_REFERENCE_UNAVAILABLE')
    state.db.commit();save(state)
    if telegram:weekly(state,telegram,latest,save)


def main():
    p=argparse.ArgumentParser();p.add_argument('--db',default='trend-breakout-state/paper.sqlite')
    p.add_argument('--cache',default='trend-breakout-cache');p.add_argument('--send-telegram',action='store_true')
    p.add_argument('--approval',default='research/trend_breakout_v1/live_approval.json')
    p.add_argument('--report',default='research/trend_breakout_v1/results/report.md')
    p.add_argument('--git-state',action='store_true');p.add_argument('--init-state',action='store_true')
    a=p.parse_args()
    if a.send_telegram:approval(a.approval,a.report)
    remote=None
    if a.git_state:
        remote=GitState(os.environ['GITHUB_REPOSITORY'],os.environ['GITHUB_TOKEN'],a.db);remote.load(a.init_state)
    elif a.send_telegram:raise RuntimeError('DURABLE_STATE_REQUIRED_FOR_TELEGRAM')
    state=State(a.db);market=Market();archive=Archive(a.cache)
    telegram=Telegram(os.environ['TELEGRAM_BOT_TOKEN'],os.environ['TELEGRAM_CHAT_ID']) if a.send_telegram else None
    def universe_provider(m):
        existing=state.db.execute('SELECT payload FROM universe WHERE month=?',(m,)).fetchone()
        if existing:return json.loads(existing[0])
        source=archive.root/'universe.json'
        data=json.loads(source.read_text()) if source.exists() else {}
        if m not in data.get('months',{}):data=collect_universe(archive,ms(m+'-01'),ms(m+'-01')+DAY,workers=8)
        if data.get('errors'):raise RuntimeError('UNIVERSE_DOWNLOAD_ERRORS')
        rows=data['months'][m]
        state.db.execute('INSERT INTO universe VALUES (?,?)',(m,json.dumps(rows)));state.db.commit()
        return rows
    cycle(state,market,universe_provider,telegram,remote.save if remote else lambda s:s.checkpoint())


if __name__=='__main__':main()
