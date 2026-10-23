"""Disabled-until-approved paper runner. No exchange order/account endpoints."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import time
from zoneinfo import ZoneInfo
import requests
from trend_breakout_live import State,GitState,Market,Telegram,now_ms,price_text
from trend_breakout_data import Archive,EXCLUDED,iso,ms,month
from abnormal_day_data import universe_at
from abnormal_day_engine import (DAY,HOUR,RULES_HASH,evaluate,make_plan,emergency_exit,timed_exit,funding_pct,result,choose)

MINUTE=60000


class FuturesMarket(Market):
    def get(self,path,params,source='futures'):
        if source!='futures':raise ValueError('FUTURES_REQUIRED_NO_SPOT_SUBSTITUTION')
        try:return super().get(path,params,source)
        except requests.HTTPError as original:
            if original.response.status_code!=451:raise
            r=self.session.get('https://www.binance.com'+path,params=params,timeout=30);r.raise_for_status()
            raw=r.json()
            if isinstance(raw,dict) and raw.get('code',0)<0:raise ValueError('FUTURES_DATA_ERROR')
            return raw

    def actual_funding(self,symbol,start,end):
        rows=[];at=start-8*HOUR
        while at<=end:
            raw=self.get('/fapi/v1/fundingRate',dict(symbol=symbol,startTime=at,endTime=end,limit=1000))
            if not isinstance(raw,list):raise ValueError('INVALID_FUNDING_DATA')
            if not raw:break
            rows.extend(dict(time=int(r['fundingTime']),rate=float(r['fundingRate'])) for r in raw)
            at=int(raw[-1]['fundingTime'])+1
            if len(raw)<1000:break
        if not rows or rows[0]['time']>start or end-rows[-1]['time']>8*HOUR+1000:raise ValueError('FUNDING_NOT_VERIFIED')
        if any(b['time']-a['time']>8*HOUR+1000 for a,b in zip(rows,rows[1:])):raise ValueError('FUNDING_GAP')
        return rows


def approve(approval,report,summary):
    a=json.loads(Path(approval).read_text());s=json.loads(Path(summary).read_text())
    if (a.get('approved_by_user') is not True or a.get('rules_hash')!=RULES_HASH or
        a.get('report_sha256')!=hashlib.sha256(Path(report).read_bytes()).hexdigest() or s.get('verdict')!='GEÇTİ'):
        raise PermissionError('ABNORMAL_LIVE_REQUIRES_PASSED_REPORT_AND_USER_APPROVAL')


def footer():return 'Veri futures · Otomatik emir yok · %0,30 masraf varsayımı'


def notify(state,key,text,telegram,save):
    if state.db.execute('SELECT 1 FROM outbox WHERE id=?',(key,)).fetchone():return
    state.db.execute('INSERT INTO outbox VALUES (?,?,?,?)',(key,'SENDING',None,json.dumps(dict(text=text))));state.db.commit();save(state)
    try:
        sent,mid=telegram.send(text)
        state.db.execute('UPDATE outbox SET state=?,sent_ms=?,payload=? WHERE id=?',('SENT',sent,json.dumps(dict(text=text,message_id=mid)),key))
    except RuntimeError as e:
        state.db.execute('UPDATE outbox SET state=? WHERE id=?',('UNKNOWN',key));state.error('TELEGRAM',str(e))
    state.db.commit();save(state)


def universe_for(state,archive,market,m):
    row=state.db.execute('SELECT payload FROM universe WHERE month=?',(m,)).fetchone()
    if row:return json.loads(row[0])
    path=Path('research/abnormal_day_v1/universe.json');known=json.loads(path.read_text()) if path.exists() else {}
    if m in known.get('months',{}):selected=known['months'][m]
    else:
        at=ms(m+'-01');prefixes=archive.listing('data/futures/um/monthly/klines/',refresh=True)['prefixes']
        symbols=[p.split('/')[-2] for p in prefixes if p.split('/')[-2].endswith('USDT') and '_' not in p.split('/')[-2] and p.split('/')[-2] not in EXCLUDED]
        def one(s):
            rows=archive.range(s,'1d',at-130*DAY,at)
            if rows and at-3*DAY<=rows[-1].close_ms<at:rows.extend(market.candles(s,'1d',rows[-1].close_ms,at,'futures'))
            return s,rows
        with ThreadPoolExecutor(max_workers=6) as pool:daily=dict(pool.map(one,symbols))
        selected=universe_at(daily,at)
        if not selected:raise ValueError('MONTHLY_UNIVERSE_UNVERIFIED')
    state.db.execute('INSERT INTO universe VALUES (?,?)',(m,json.dumps(selected)));state.db.commit();return selected


def settle(state,market,telegram,save,at):
    for p in state.active():
        if at<=p['entry_ms']:continue
        # Minute candles only disambiguate a partial entry/exit hour in paper;
        # the shared stop function allows gap fills only at UTC hour boundaries.
        until=min(at,p['deadline_ms']);start=p.get('checked_ms',p['entry_ms']//MINUTE*MINUTE)
        end=until//MINUTE*MINUTE;event=None
        try:
            bars=market.candles(p['symbol'],'1m',start,end,'futures');expected=start
            for b in bars:
                if b.open_ms!=expected:raise ValueError('PAPER_MINUTE_GAP')
                expected=b.close_ms;hit=emergency_exit(p,b)
                if b.open_ms<p['entry_ms']:
                    if hit:raise ValueError('ENTRY_MINUTE_STOP_ORDER_UNVERIFIED')
                    continue
                if hit:event=hit;break
            if not event and expected!=end:raise ValueError('PAPER_HISTORY_INCOMPLETE')
            if not event and at>=p['deadline_ms']:
                price,quoted_at=market.price(p['symbol'],'futures');event=timed_exit(p,price,quoted_at)
            if event:
                fs=market.actual_funding(p['symbol'],p['entry_ms'],event['exit_ms'])
                p.update(event,**result(p,event['exit'],funding_pct(p['side'],fs,p['entry_ms'],event['exit_ms'])),status='CLOSED')
                state.db.execute('UPDATE positions SET state=?,payload=? WHERE id=?',('CLOSED',json.dumps(p),p['id']));state.db.commit()
                label='ÇIKIŞ ZAMANI' if event['exit_reason'].startswith('TIME_') else 'ACİL STOP'
                delay=p.get('exit_delay_ms',0)/60000
                notify(state,p['id']+':exit',f"{label} | {p['symbol']} | {p['side']}\nÇıkış: {price_text(p['exit'],p.get('tick_size'))}\nNet %{p['net_return_pct']:.3f} | {p['net_r']:.3f} R\n24 saat sınırına göre gecikme: {delay:.1f} dakika\n"+footer(),telegram,save)
            else:
                p['checked_ms']=end;state.db.execute('UPDATE positions SET payload=? WHERE id=?',(json.dumps(p),p['id']));state.db.commit()
        except (ValueError,requests.RequestException) as e:
            state.error(p['symbol'],str(e));save(state);raise RuntimeError('PAPER_RESULT_UNVERIFIED') from None


def cycle(state,market,archive,telegram,save,at=None):
    at=at or now_ms();latest=at//DAY*DAY
    if state.meta('created_ms') is None:state.set_meta('created_ms',at)
    settle(state,market,telegram,save,at)
    for t in range(state.meta('last_daily',latest-DAY)+DAY,latest+1,DAY):
        pending=[]
        for row in universe_for(state,archive,market,month(t)):
            s=row['symbol'];id=f'{s}:{t}'
            if state.seen(id):continue
            bars=market.candles(s,'1d',ms('2020-01-01'),t,'futures');sig=evaluate(s,bars,t)
            if sig['reason'] in ('DATA_GAP','STALE_OR_UNCLOSED_DATA','INSUFFICIENT_HISTORY'):raise ValueError('SIGNAL_HISTORY_UNVERIFIED:'+s)
            if sig['status']!='SIGNAL':continue
            pending.append(dict(sig,id=id))
        selected,skips=choose(pending,state.active(),True)
        for p in skips:state.signal(p['id'],p)
        for sig in selected:
            price,quoted_at=market.price(sig['symbol'],'futures');p=make_plan(sig,price,quoted_at)
            p.update(id=sig['id'],tick_size=market.tick(p['symbol'],'futures'),data_source='futures')
            state.signal(p['id'],p);state.db.execute('INSERT INTO positions VALUES (?,?,?)',(p['id'],'OPEN',json.dumps(p)));state.db.commit()
            expiry=datetime.fromtimestamp(p['deadline_ms']/1000,ZoneInfo('Europe/Istanbul')).strftime('%d/%m %H:%M')
            text=(f"{p['side']} ÖNERİ | {p['symbol']} | 24 saat\nDün: %{p['r']*100:+.2f} · z={p['z']:+.2f} standart sapma\n"
                  f"Giriş referansı: {price_text(p['entry'],p['tick_size'])} | Acil stop: {price_text(p['stop'],p['tick_size'])}\n"
                  f"Çıkış: {expiry} Türkiye civarı, o anki fiyattan\nSinyal kapanışı: {iso(t)}\n"+footer())
            notify(state,p['id']+':entry',text,telegram,save)
        state.set_meta('last_daily',t);save(state)
    d=datetime.fromtimestamp(latest/1000,timezone.utc);monday=latest-d.weekday()*DAY
    if monday>state.meta('created_ms',at):
        rows=[json.loads(r[0]) for r in state.db.execute("SELECT payload FROM positions WHERE state='CLOSED'")]
        recent=[p for p in rows if monday-7*DAY<=p['exit_ms']<monday];known=[p for p in recent if p.get('net_r') is not None]
        average=sum(p['net_return_pct'] for p in known)/len(known) if known else 0.
        notify(state,f'weekly:{monday}',f"ANORMAL GÜN · HAFTALIK\nAçık {len(state.active())} | Kapanan {len(recent)}\nOrtalama net %{average:.3f} | Toplam {sum(p['net_r'] for p in known):.3f} R\n"+footer(),telegram,save)


def main():
    p=argparse.ArgumentParser();p.add_argument('--send-telegram',action='store_true');p.add_argument('--init-state',action='store_true');a=p.parse_args()
    if not a.send_telegram:raise SystemExit('LIVE_DISABLED')
    root=Path('research/abnormal_day_v1');approve(root/'live_approval.json',root/'results/report.md',root/'results/summary.json')
    remote=GitState(os.environ['GITHUB_REPOSITORY'],os.environ['GITHUB_TOKEN'],'abnormal-day-state/paper.sqlite',branch='abnormal-day-paper-state');remote.load(a.init_state)
    state=State(remote.path);market=FuturesMarket();telegram=Telegram(os.environ['TELEGRAM_BOT_TOKEN'],os.environ['TELEGRAM_CHAT_ID'])
    # A daily runner may start before yesterday's entry timestamp. Wait within
    # this job for that day's expiry, rather than silently holding for 48h.
    deadlines=[p['deadline_ms'] for p in state.active() if 0<p['deadline_ms']-now_ms()<=HOUR]
    if deadlines:
        until=max(deadlines)
        while now_ms()<until:time.sleep(min(30,(until-now_ms())/1000))
    cycle(state,market,Archive('abnormal-day-archive'),telegram,remote.save)


if __name__=='__main__':main()
