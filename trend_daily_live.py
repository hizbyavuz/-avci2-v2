"""Approval-gated daily paper/Telegram runner. No exchange orders."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import requests
from trend_breakout_live import State,GitState,Market,Telegram,now_ms,price_text
from trend_breakout_data import Archive,EXCLUDED,ms,iso,month
from trend_breakout_engine import DAY,MINUTE
from trend_daily_data import universe_at
from trend_daily_engine import (RULES_HASH,DailySeries,evaluate,make_plan,update_stop,
                                stop_exit,choose,funding_pct,result,excursions)


class DailyMarket(Market):
    def get(self,path,params,source='futures'):
        try:return super().get(path,params,source)
        except requests.HTTPError as original:
            if source!='futures' or original.response.status_code!=451:raise
            r=self.session.get('https://www.binance.com'+path,params=params,timeout=30)
            if not r.ok:raise original
            try:x=r.json()
            except ValueError:raise original
            if isinstance(x,dict) and 'code' in x and x['code']<0:raise original
            return x

    def actual_funding(self,symbol,start,end):
        rows=[];at=start
        while at<end:
            raw=self.get('/fapi/v1/fundingRate',dict(symbol=symbol,startTime=at,endTime=end,limit=1000))
            if not isinstance(raw,list):raise ValueError('INVALID_FUNDING_DATA')
            if not raw:break
            rows.extend(dict(time=int(f['fundingTime']),rate=float(f['fundingRate'])) for f in raw)
            nxt=int(raw[-1]['fundingTime'])+1
            if nxt<=at:raise ValueError('FUNDING_PAGINATION_STALLED')
            at=nxt
            if len(raw)<1000:break
        return rows


def approve(path,report,summary):
    a=json.loads(Path(path).read_text());s=json.loads(Path(summary).read_text())
    if (a.get('approved_by_user') is not True or a.get('rules_hash')!=RULES_HASH or
        a.get('report_sha256')!=hashlib.sha256(Path(report).read_bytes()).hexdigest() or
        s.get('verdict')!='GEÇTİ'):raise PermissionError('DAILY_LIVE_REQUIRES_PASSED_REPORT_AND_USER_APPROVAL')


def footer(source):return f'Veri {source} · Otomatik emir yok · %0,30 masraf varsayımı'


def notify(state,key,text,telegram,save,photo=None):
    if state.db.execute('SELECT 1 FROM outbox WHERE id=?',(key,)).fetchone():return
    state.db.execute('INSERT INTO outbox VALUES (?,?,?,?)',(key,'SENDING',None,json.dumps(dict(text=text))))
    state.db.commit();save(state)
    try:
        sent,id=telegram.send(text,photo)
        state.db.execute('UPDATE outbox SET state=?,sent_ms=?,payload=? WHERE id=?',('SENT',sent,json.dumps(dict(text=text,message_id=id)),key))
    except RuntimeError as e:
        state.db.execute('UPDATE outbox SET state=? WHERE id=?',('UNKNOWN',key));state.error('TELEGRAM',str(e))
    state.db.commit();save(state)


def daily_chart(p,bars,path):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    full=[b for b in bars if b.close_ms<=p['time']];start=max(20,len(full)-60);history=full[start:]
    fig,ax=plt.subplots(figsize=(11,7),dpi=140);fig.patch.set_facecolor('#111722');ax.set_facecolor('#111722')
    highs=[];lows=[]
    for k,b in enumerate(history):
        color='#27c99e' if b.close>=b.open else '#ef637d';ax.vlines(k,b.low,b.high,color=color,lw=.8)
        ax.add_patch(Rectangle((k-.3,min(b.open,b.close)),.6,max(abs(b.close-b.open),p['n']*.005),color=color))
        prev=full[start+k-20:start+k];highs.append(max(x.high for x in prev));lows.append(min(x.low for x in prev))
    ax.plot(highs,color='#7ab9f0',lw=1,label='Önceki 20 gün');ax.plot(lows,color='#7ab9f0',lw=1)
    ax.fill_between(range(len(history)),lows,highs,color='#7ab9f0',alpha=.08)
    for title,price,color in [('GİRİŞ',p['entry'],'white'),('STOP · 2N',p['initial_stop'],'#ff667c')]:
        ax.axhline(price,color=color,ls='--');ax.text(len(history)+1,price,title+' '+price_text(price,p.get('tick_size')),color=color,va='center',weight='bold')
    ax.set_xlim(-1,len(history)+20);ticks=list(range(0,len(history),10));ax.set_xticks(ticks,[iso(history[i].open_ms)[:10] for i in ticks],rotation=20,color='white')
    ax.tick_params(colors='white');ax.grid(alpha=.15);ax.set_ylabel('USDT',color='white')
    ax.set_title(f"{p['side']} · {p['symbol']} · Günlük\nHedef yok · iz süren stop · paper",color='white',loc='left',fontsize=16)
    fig.text(.08,.02,footer(p['data_source']),color='white',fontsize=9);fig.tight_layout(rect=(0,.05,1,1));fig.savefig(path,facecolor=fig.get_facecolor());plt.close(fig)


def universe_for(state,archive,market,m):
    row=state.db.execute('SELECT payload FROM universe WHERE month=?',(m,)).fetchone()
    if row:return json.loads(row[0])
    snapshot=Path('research/trend_daily_v1/universe.json')
    known=json.loads(snapshot.read_text()) if snapshot.exists() else {}
    if m in known.get('months',{}):selected=known['months'][m]
    else:
        at=ms(m+'-01');prefixes=archive.listing('data/futures/um/monthly/klines/',refresh=True)['prefixes']
        symbols=[p.split('/')[-2] for p in prefixes if p.split('/')[-2].endswith('USDT') and '_' not in p.split('/')[-2] and p.split('/')[-2] not in EXCLUDED]
        def one(s):
            rows=archive.range(s,'1d',at-130*DAY,at)
            if rows and rows[-1].close_ms<at and rows[-1].close_ms>=at-3*DAY:
                extra=market.candles(s,'1d',rows[-1].close_ms,at,'futures')
                if market.sources[s]!='futures':raise ValueError('SPOT_VOLUME_NOT_ALLOWED_FOR_PERPETUAL_UNIVERSE')
                rows.extend(extra)
            return s,rows
        with ThreadPoolExecutor(max_workers=6) as pool:daily=dict(pool.map(one,symbols))
        selected=universe_at(daily,at)
        if not selected:raise RuntimeError('MONTHLY_UNIVERSE_UNVERIFIED')
    state.db.execute('INSERT INTO universe VALUES (?,?)',(m,json.dumps(selected)));state.db.commit();return selected


def monitor(state,market,until,telegram,save):
    for p in state.active():
        if p['entry_ms']>=until:continue
        try:
            start=p.get('checked_ms',p['entry_ms']//MINUTE*MINUTE)
            bars=market.candles(p['symbol'],'1m',start,until,p['data_source'])
            if market.sources[p['symbol']]!=p['data_source']:raise ValueError('POSITION_SOURCE_CHANGED')
            expected=start;exit_event=None
            for b in bars:
                if b.open_ms!=expected:raise ValueError('MINUTE_DATA_GAP')
                expected=b.close_ms
                hit=stop_exit(p,b)
                if b.open_ms<p['entry_ms']:
                    if hit:raise ValueError('UNVERIFIED_ENTRY_MINUTE')
                    continue
                if hit:
                    exit_event=dict(hit,exit_ms=hit['exit_ms'] if hit['exit_ms'] is not None else b.close_ms-1)
                    p.update(excursions(p,[],hit['exit']));break
                p.update(excursions(p,[b]))
            if not exit_event and expected!=until:raise ValueError('INCOMPLETE_MINUTE_DATA')
            if exit_event:
                try:
                    fs=market.actual_funding(p['symbol'],p['entry_ms'],exit_event['exit_ms'])
                    p.update(result(p,exit_event['exit'],funding_pct(p['side'],fs,p['entry_ms'],exit_event['exit_ms'])))
                except (requests.RequestException,ValueError):p['net_r']=None
                p.update(exit_event,status='CLOSED');state.db.execute('UPDATE positions SET state=?,payload=? WHERE id=?',('CLOSED',json.dumps(p),p['id']))
                nr='doğrulanmadı' if p.get('net_r') is None else f"{p['net_r']:.3f} R"
                state.db.commit();notify(state,p['id']+':exit',f"STOP ÇIKIŞI | {p['symbol']} | {p['side']}\nÇıkış: {price_text(p['exit'],p.get('tick_size'))} | Net: {nr}\n"+footer(p['data_source']),telegram,save)
            else:
                p['checked_ms']=until;state.db.execute('UPDATE positions SET payload=? WHERE id=?',(json.dumps(p),p['id']));state.db.commit()
        except (requests.RequestException,ValueError) as e:
            state.error(p['symbol'],type(e).__name__+':'+str(e)[:80]);raise RuntimeError('OPEN_POSITION_MONITOR_UNVERIFIED') from None


def cycle(state,market,archive,telegram,save,at=None):
    at=at or now_ms();latest=at//DAY*DAY
    state.db.execute('CREATE TABLE IF NOT EXISTS stop_updates(id TEXT, time_ms INTEGER, payload TEXT, PRIMARY KEY(id,time_ms))');state.db.commit()
    if state.meta('created_ms') is None:state.set_meta('created_ms',at)
    seed=state.meta('daily_seed')
    if seed is None:
        snapshot=Path('research/trend_daily_v1/universe.json')
        seed=json.loads(snapshot.read_text())['start'] if snapshot.exists() else ms('2020-01-01')
        state.set_meta('daily_seed',seed)
    cache={}
    for t in range(state.meta('last_daily',latest-DAY)+DAY,latest+1,DAY):
        monitor(state,market,t,telegram,save)
        occupied_at_signal={p['symbol'] for p in state.active() if p['entry_ms']<t}
        for p in state.active():
            if p['entry_ms']>=t:continue
            bars=market.candles(p['symbol'],'1d',seed,t,p['data_source'])
            if market.sources[p['symbol']]!=p['data_source']:raise RuntimeError('POSITION_SOURCE_CHANGED')
            change=update_stop(p,bars,t);p['stop']=change['stop']
            state.db.execute('INSERT OR IGNORE INTO stop_updates VALUES (?,?,?)',(p['id'],t,json.dumps(change)))
            state.db.execute('UPDATE positions SET payload=? WHERE id=?',(json.dumps(p),p['id']))
        state.db.commit();save(state)
        if t==latest:monitor(state,market,at//MINUTE*MINUTE,telegram,save)
        pending=[]
        for row in universe_for(state,archive,market,month(t)):
            s=row['symbol'];id=f'{s}:{t}'
            if state.seen(id):continue
            bars=market.candles(s,'1d',seed,t)
            sig=evaluate(s,bars,t)
            if sig['reason'] in ('DATA_GAP','STALE_OR_UNCLOSED_DATA','INSUFFICIENT_HISTORY'):raise RuntimeError('DAILY_HISTORY_UNVERIFIED:'+s)
            if sig['status']!='SIGNAL':continue
            if s in occupied_at_signal:
                state.signal(id,dict(sig,status='SKIPPED',reason='SAME_SYMBOL_OPEN'));continue
            source=market.sources[s];price,qt=market.price(s,source)
            p=make_plan(sig,price,qt,source);p.update(id=id,price_ms=qt,tick_size=market.tick(s,source));cache[s]=bars;pending.append(p)
        selected,skipped=choose(pending,state.active(),True)
        for p in skipped:state.signal(p['id'],p)
        for old in selected:
            price,qt=market.price(old['symbol'],old['data_source']);p=make_plan(dict(old,status='SIGNAL'),price,qt,old['data_source']);p.update(id=old['id'],price_ms=qt,tick_size=old['tick_size'])
            path=Path('trend-daily-charts')/(p['symbol']+'-'+str(t)+'.png');path.parent.mkdir(exist_ok=True);daily_chart(p,cache[p['symbol']],path)
            state.signal(p['id'],p);state.db.execute('INSERT INTO positions VALUES (?,?,?)',(p['id'],'OPEN',json.dumps(p)));state.db.commit()
            text=(f"{p['side']} ÖNERİ | {p['symbol']} | Günlük\nGiriş referansı: {price_text(p['entry'],p['tick_size'])} | Başlangıç stop: {price_text(p['stop'],p['tick_size'])} (2N)\n"
                  f"Hedef yok, iz süren stop\nNeden: 20 günlük {'tepe' if p['side']=='LONG' else 'dip'} {price_text(p['level'],p['tick_size'])} kırıldı\n"
                  f"Sinyal: {iso(t)} · Fiyat: {iso(qt)}\n"+footer(p['data_source']))
            notify(state,p['id']+':entry',text,telegram,save,path)
        state.set_meta('last_daily',t);save(state)
    monitor(state,market,at//MINUTE*MINUTE,telegram,save)
    rows=[]
    for p in state.active():
        price,qt=market.price(p['symbol'],p['data_source'])
        try:
            fs=market.actual_funding(p['symbol'],p['entry_ms'],qt);r=result(p,price,funding_pct(p['side'],fs,p['entry_ms'],qt))['net_r'];value=f'{r:.3f} R'
        except (requests.RequestException,ValueError):value='R doğrulanmadı'
        rows.append(f"{p['symbol']} {p['side']} · Stop {price_text(p['stop'],p.get('tick_size'))} · {value} · {p['data_source']}")
    notify(state,f'daily:{latest}','GÜNLÜK AÇIK POZİSYONLAR\n'+('\n'.join(rows) or 'Açık pozisyon yok.')+'\nR: tahmini çıkış masrafı dahil.\n'+footer('futures/spot (satırlarda)'),telegram,save)
    d=datetime.fromtimestamp(latest/1000,timezone.utc);monday=(latest//DAY-d.weekday())*DAY
    if monday>state.meta('created_ms',at):
        closed=[json.loads(r[0]) for r in state.db.execute("SELECT payload FROM positions WHERE state='CLOSED'")]
        closed=[p for p in closed if monday-7*DAY<=p['exit_ms']<monday];known=[p for p in closed if p.get('net_r') is not None]
        notify(state,f'weekly:{monday}',f"GÜNLÜK TREND · HAFTALIK\nAçık: {len(rows)} | Kapanan: {len(closed)} | Ölçülen: {len(known)}\nToplam net R: {sum(p['net_r'] for p in known):.3f}\n"+footer('futures/spot'),telegram,save)


def main():
    p=argparse.ArgumentParser();p.add_argument('--send-telegram',action='store_true');p.add_argument('--init-state',action='store_true')
    p.add_argument('--approval',default='research/trend_daily_v1/live_approval.json');p.add_argument('--report',default='research/trend_daily_v1/results/report.md');p.add_argument('--summary',default='research/trend_daily_v1/results/summary.json')
    a=p.parse_args()
    if not a.send_telegram:raise SystemExit('LIVE_DISABLED: requires report approval and explicit --send-telegram')
    approve(a.approval,a.report,a.summary)
    remote=GitState(os.environ['GITHUB_REPOSITORY'],os.environ['GITHUB_TOKEN'],'trend-daily-state/paper.sqlite',branch='trend-daily-paper-state');remote.load(a.init_state)
    state=State(remote.path);market=DailyMarket();archive=Archive('trend-daily-archive')
    telegram=Telegram(os.environ['TELEGRAM_BOT_TOKEN'],os.environ['TELEGRAM_CHAT_ID'])
    cycle(state,market,archive,telegram,remote.save)


if __name__=='__main__':main()
