"""One paper engine; never imports or starts a legacy motor."""
import argparse
from collections import Counter
from contextlib import ExitStack
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time

from .core import (CFG, MINUTE, assess, closed_bars, descriptive_stats, exit_on_bar,
                   funding_cost, risk_room, sizing)
from .io import Binance, DataUnavailable, Store, Telegram, book_check, tradable
from .observations import (collect, context_fields, funnel_report, record_setup, setup_id,
                           GAP_GRACE, GAP_RETRY)

STOP = threading.Event()
OPERATIONS_REVISION = 'SCAN_RESUME_20261026_2'
CHART_REVISION = 'CHARTS_20261026_1'
SCAN_STATE_KEY = 'scan_progress_v2'
SCAN_BATCH_SECONDS = 45
SCAN_BATCH_SYMBOLS = 64
RECHECK_REQUEST = 'USER_RECHECK_20261026_1'
STATE_FOLDER = CFG.version.lower()


def log(kind, **fields):
    print(kind+' '+json.dumps(fields, ensure_ascii=False, allow_nan=False), flush=True)


def plan_message(p, cfg=CFG):
    icon = '🟢' if p['direction'] == 'LONG' else '🔴'
    prefix = 'TEST PLANI' if p['status'] == 'PENDING' else 'SANAL GİRİŞ'
    at = datetime.fromtimestamp(p['entry_ms']/1000, timezone.utc).astimezone(
        __import__('zoneinfo').ZoneInfo('Europe/Istanbul')).strftime('%d.%m %H:%M')
    return (f"{icon} {p['direction']} | {p['symbol']} | {prefix}\n"
            f"30 dk geri çekilme sonrası dönüş · 4 saat yönü uyumlu\n"
            f"Referans giriş: {p['entry']:.8g} · Stop: {p['stop']:.8g}\n"
            f"Hedef: {p['target']:.8g} · Net ödül/risk: {p['net_rr']:.2f}\n"
            f"ÖRNEK TEST HESABI: başlangıç {cfg.paper_initial_equity:.0f} USDT\n"
            f"Pozisyon: {p['notional']:.2f} USDT · Miktar: {p['quantity']:.8g}\n"
            f"Örnek {cfg.indicative_leverage}x teminat: {p['indicative_margin']:.2f} USDT\n"
            f"Planlanan kayıp: {p['planned_loss']:.2f} · Kötü kayma: {p['stress_loss']:.2f} USDT\n"
            f"Hedefte net: {p['target_net']:.2f} USDT (funding hariç)\n"
            f"Sanal giriş zamanı: {at} · Fiyat kaçarsa iptal\n"
            "Risk: geri çekilme trend dönüşüne ilerleyebilir.\n"
            "Ölçülmüş başarı: DOĞRULANMADI. Gerçek emir yok.\n"
            "3x yalnız teminat örneği; hesap/likidasyon uygunluğu doğrulanmadı.")


def account(store, now, cfg=CFG):
    plans = store.plans()
    closed = [p for p in plans if p['status'] == 'CLOSED']
    day = now//86_400_000
    daily_pnl = sum(p['net_pnl'] for p in closed if p['exit_interval_end']//86_400_000 == day)
    realized_equity = cfg.paper_initial_equity+sum(p['net_pnl'] for p in closed)
    unrealized = sum(min(0, p.get('unrealized', 0)) for p in plans if p['status'] == 'OPEN')
    return realized_equity+unrealized, realized_equity-daily_pnl, daily_pnl


def notify(store, p, now):
    if p['status'] in ('PENDING', 'OPEN'):
        text = plan_message(p)
    elif p['status'] == 'CLOSED':
        text = (f"{'🟢' if p['direction']=='LONG' else '🔴'} {p['symbol']} · TEST SONUCU\n"
                f"{p['exit_reason']} · Net {p['net_pnl']:+.2f} USDT · {p['net_r']:+.2f}R\n"
                f"Kötü kayma senaryosu: {p['stress_net_r']:+.2f}R\n"
                "Sanal sonuç; ücret/kayma modeli ve gerçekleşmiş funding dahil.\n"
                "Ölçülmüş başarı: DOĞRULANMADI.")
    else:
        text = f"⚪ {p['symbol']} · TEST PLANI İPTAL\n{p.get('cancel_reason','Koşul bozuldu')}\nGerçek emir verilmedi."
    n = descriptive_stats(store.plans())['n']
    photo = None
    try:
        from .chart import render_plan
        photo = render_plan(p, store.chart_bars(p), n=n)
    except Exception as exc:
        # A failed chart must not hide a cancellation or closure notification.
        store.event('CHART_ERROR', p['id'], {'type': type(exc).__name__})
        log('TA_PAPER_CHART_ERROR', type=type(exc).__name__)
        text += '\nGrafik oluşturulamadı; seviyeler metinde.'
    store.queue(p['id']+':'+p['status'], text+f"\nKapanmış test sayısı: N={n}", now, photo=photo)


def health_message(reason, cooldown):
    text = '⚪ TA PAPER · Veri bekleniyor\n'+reason+'\nYeni işlem planı üretilmiyor. Gerçek emir yok.'
    if cooldown.get('remaining_seconds',0):
        until = datetime.fromtimestamp(cooldown['until_ms']/1000,timezone.utc).astimezone(
            __import__('zoneinfo').ZoneInfo('Europe/Istanbul')).strftime('%d.%m.%Y %H:%M:%S')
        if cooldown['source'] in ('LEGACY_UNKNOWN_UPPER_BOUND','CONSERVATIVE_FALLBACK'):
            text += '\nSunucunun kesin bitiş saati bilinmiyor; ihtiyatlı yeniden deneme sınırı: '+until+' TSİ.'
        else:
            text += '\nEn erken yeniden deneme: '+until+' TSİ.'
    return text+'\nBaşarı: DOĞRULANMADI.'


def update_health(store, reason, cooldown, now, progress=None):
    progress=progress or {}
    status='READY' if reason=='READY' else 'WAITING'
    universe=progress.get('universe',len(progress.get('markets',{})))
    assessed=progress.get('assessed',len(progress.get('done',{})))
    identity=reason+':'+str(cooldown.get('until_ms',0))+':'+str(assessed)
    if store.setting('runtime_health_identity_v2')!=identity:
        store.event('RUNTIME_HEALTH',reason,dict(cooldown,reason=reason,assessed=assessed,universe=universe),now)
        store.setting('runtime_health_identity_v2',identity)
    previous=store.setting('notification_health_class_v2')
    last=int(store.setting('notification_health_sent_ms_v2') or 0)
    # Exact timer changes remain in logs; a busy IP must not generate minute-by-minute alerts.
    if previous==status and (status=='READY' or now-last<15*MINUTE):
        return
    if status=='READY':
        text=(f'⚪ TA PAPER · Tarama tamamlandı\nDeğerlendirilen: {assessed}/{universe} coin.\n'
              'Grafikli test bildirimleri açık. Uygun koşul yoksa işlem planı gönderilmez.\n'
              'Gerçek emir yok; başarı DOĞRULANMADI.')
    else:
        explanations={
            'SHARED_IP_WEIGHT_HIGH':'IP istek yükü yüksek; koruma beklemesi uygulanıyor.',
            'BINANCE_HTTP_418':'Binance geçici IP erişim engeli sürüyor.',
            'BINANCE_HTTP_429':'Binance istek sınırı nedeniyle bekleniyor.',
            'SCAN_INCOMPLETE':'Tarama tamamlanmadı; kalan coinlerden devam edilecek.',
            'SCAN_EXPIRED':'Tarama giriş süresi içinde tamamlanamadı; bu dönem eksik kaydedildi.'}
        text=health_message(explanations.get(reason,reason),cooldown)
        text+=f'\nDeğerlendirilen: {assessed}/{universe if universe else "henüz belirlenmedi"} coin.'
        text+='\nEksik veri, fırsat yok anlamına gelmez.'
    store.queue('system:health:'+str(now),text,now)
    store.setting('notification_health_class_v2',status)
    store.setting('notification_health_sent_ms_v2',now)


def continuous_path(client, symbol, start, now, cfg=CFG):
    """Use a verified prefix, repair a missing start once; never jump a hole."""
    raw=client.candles(symbol,'1m',limit=1000,startTime=start,endTime=now-cfg.publication_delay_ms)
    rows=[]
    for row in raw:
        if int(row[6])+cfg.publication_delay_ms>=now:
            continue
        rows.extend(closed_bars([row],MINUTE,now,minimum=1,require_current=False,cfg=cfg))
    if not rows or rows[0][0]>start:
        end=min(now-cfg.publication_delay_ms,rows[0][0]-1 if rows else start+1000*MINUTE-1)
        patch=client.candles(symbol,'1m',limit=1000,startTime=start,endTime=end)
        for row in patch:
            if int(row[6])+cfg.publication_delay_ms<now:
                rows.extend(closed_bars([row],MINUTE,now,minimum=1,require_current=False,cfg=cfg))
    by_time={}
    for row in rows:
        if row[0]<start:
            raise DataUnavailable('PATH_RESPONSE_BEFORE_REQUEST')
        if row[0] in by_time and by_time[row[0]]!=row:
            raise DataUnavailable('CONFLICTING_PATH_CANDLE')
        by_time[row[0]]=row
    out=[];cursor=start
    while cursor in by_time:
        out.append(by_time[cursor]);cursor+=MINUTE
    if not out:
        raise DataUnavailable('ENTRY_OR_OUTCOME_PATH_GAP')
    return out


def hold_data(store,p,reason,now):
    previous=p.get('status')
    resume=p.get('resume_status','OPEN') if previous in ('DATA_HOLD','UNRESOLVED_DATA') else previous
    since=p.get('data_hold_since_ms',now)
    unresolved=now-since>=GAP_GRACE
    p.update(resume_status=resume,status='UNRESOLVED_DATA' if unresolved else 'DATA_HOLD',
             data_issue=reason,data_hold_since_ms=since,
             next_recovery_ms=now+(15*MINUTE if unresolved else GAP_RETRY))
    store.save(p)
    store.event('DATA_ISSUE',p['id'],{'reason':reason,'status':p['status'],'since_ms':since},now)
    if unresolved and not p.get('unresolved_notice_sent'):
        store.queue('system:data:'+p['id'],
                    f"⚪ {p['symbol']} · Sanal işlem sonucu belirsiz\n"
                    "Eksik veri tamamlanamadı; kâr/zarar veya stop sonucu uydurulmadı. "
                    "Yeni portföy riski bekletiliyor; bağımsız aday kaydı ve 72 saatlik gözlem sürüyor. "
                    "Gerçek emir yok; başarı DOĞRULANMADI.",now)
        p['unresolved_notice_sent']=True
        store.save(p)


def advance(store, client, cfg=CFG):
    now = client.now()
    for p in store.plans(active=True):
        try:
            if p['status'] in ('DATA_HOLD','UNRESOLVED_DATA'):
                if now<p.get('next_recovery_ms',0):
                    continue
                # A missing path cannot be silently resumed beyond the gap.
                p['status'] = p.get('resume_status', 'OPEN')
            if p['status'] == 'FUNDING_PENDING':
                if now < p['exit_interval_end']+2*MINUTE:
                    continue
                funding = client.funding(p['symbol'], p['entry_ms'], p['exit_interval_end'])
                store.snapshot('FUNDING:'+p['id'], funding, now)
                cost, uncertain = funding_cost(p, funding, p['exit_interval_end'])
                p.update(status='CLOSED', funding_cost=cost, funding_boundary_uncertain=uncertain,
                         net_pnl=p['pnl_before_funding']-cost,
                         stress_net_pnl=p['stress_pnl_before_funding']-cost)
                p['net_r'] = p['net_pnl']/p['planned_loss']
                p['stress_net_r'] = p['stress_net_pnl']/p['planned_loss']
                store.save(p)
                notify(store, p, now)
                continue
            if p['status'] == 'PENDING' and now > p['expires_ms']:
                p.update(status='CANCELLED', cancel_reason='Giriş verisi zamanında doğrulanamadı')
                store.save(p)
                notify(store, p, now)
                continue
            start = (p['observed_ms']//MINUTE*MINUTE if p['status'] == 'PENDING'
                     else p.get('checked_through_ms', p['entry_ms']-1)+1)
            if now < start+MINUTE+cfg.publication_delay_ms:
                continue
            bars = continuous_path(client,p['symbol'],start,now,cfg)
            store.candles(p['symbol'], '1m', bars)
            if p['status'] == 'PENDING':
                before = [b for b in bars if b[0] < p['entry_ms']]
                # Include the signal's partial minute only as a pessimistic
                # cancellation check, never as post-entry profit/loss.
                crossed = any((b[3] <= p['stop'] or b[2] >= p['target']) if p['direction']=='LONG'
                              else (b[2] >= p['stop'] or b[3] <= p['target']) for b in before)
                if crossed:
                    p.update(status='CANCELLED', cancel_reason='Giriş öncesi seviye görüldü; dakika içi sıra belirsiz')
                    store.save(p)
                    notify(store, p, now)
                    continue
                bars = [b for b in bars if b[0] >= p['entry_ms']]
                if not bars:
                    continue
                try:
                    eq, day_start, day_pnl = account(store, now)
                    other = [x for x in store.plans(active=True) if x['id'] != p['id']]
                    room = risk_room(eq, day_start, day_pnl, other, p['direction'], cfg)
                    # Quantity cannot grow after the plan was sent. Recompute
                    # downward only if opening gap consumes the risk budget.
                    fill = sizing(p, p['market'], eq, min(room, p['planned_loss']), bars[0][1], cfg)
                    if fill['quantity'] > p['quantity']:
                        ratio = p['quantity']/fill['quantity']
                        for key in ('quantity','notional','planned_loss','stress_loss','target_net','indicative_margin'):
                            fill[key] *= ratio
                    p.update(fill, status='OPEN', actual_entry_reference=bars[0][1])
                except ValueError as exc:
                    p.update(status='CANCELLED', cancel_reason=str(exc))
                    store.save(p)
                    notify(store, p, now)
                    continue
                store.save(p)
                notify(store, p, now)
            for bar in bars:
                outcome = exit_on_bar(p, bar, cfg)
                if outcome:
                    p.update(outcome, status='FUNDING_PENDING', exit_reason=outcome['reason'])
                    p['checked_through_ms'] = bar[6]
                    break
                sign = 1 if p['direction'] == 'LONG' else -1
                favorable = sign*((bar[2] if sign == 1 else bar[3])/p['entry']-1)
                adverse = sign*((bar[3] if sign == 1 else bar[2])/p['entry']-1)
                p.update(checked_through_ms=bar[6],
                         mfe_fraction=max(p.get('mfe_fraction', 0), favorable),
                         mae_fraction=min(p.get('mae_fraction', 0), adverse),
                         unrealized=sign*p['quantity']*(bar[4]-p['entry']))
            expected_end=((now-cfg.publication_delay_ms)//MINUTE)*MINUTE-1
            if p['status']=='OPEN' and p.get('checked_through_ms',0)<expected_end:
                hold_data(store,p,'PATH_NOT_CAUGHT_UP',now)
            else:
                if p.pop('data_hold_since_ms',None) is not None:
                    store.event('DATA_RECOVERED',p['id'],{'status':p['status']},now)
                p.pop('next_recovery_ms',None)
                store.save(p)
        except (DataUnavailable, ValueError, KeyError, TypeError) as exc:
            reason = str(exc)[:120] if isinstance(exc, (ValueError, DataUnavailable)) else type(exc).__name__
            hold_data(store,p,reason,now)


def _cooldown(client):
    return client.gate.state() if hasattr(client,'gate') else {'remaining_seconds':0}


def _save_scan(store,state):
    store.setting(SCAN_STATE_KEY,json.dumps(state,sort_keys=True,allow_nan=False))


def _scan_health(store,client,state,started,admitted=0):
    done=state['done']; universe=len(state['markets'])
    if state['status']=='EXPIRED':
        for symbol,p in done.items():
            if p['status']=='SETUP' and symbol not in state['handled']:
                record_setup(store,dict(p),client.now(),state['markets'][symbol],'SCAN_INCOMPLETE_EXPIRED')
                state['handled'][symbol]='SCAN_INCOMPLETE_EXPIRED'
    counts=Counter(p['reason'] for p in done.values())
    counts.update({'ADMISSION:'+v: list(state['handled'].values()).count(v)
                   for v in set(state['handled'].values()) if v!='ACCEPTED'})
    coverage=len(done)/universe if universe else 0.0
    health=dict(scan_slot=state['slot'],status=state['status'],
                complete=state['status']=='COMPLETE',universe=universe,
                attempted=len(state['attempted']),request_attempts=state['request_attempts'],
                assessed=len(done),remaining=universe-len(done),
                coverage_fraction=coverage,coverage_below_95=coverage<.95,
                setups=sum(p['status']=='SETUP' for p in done.values()),admitted=admitted,
                admitted_total=list(state['handled'].values()).count('ACCEPTED'),
                reasons=dict(counts),data_errors=state['errors'],
                deadline_ms=state['deadline_ms'],elapsed_seconds=round(time.monotonic()-started,2),
                config_hash=CFG.fingerprint,automatic_orders=False,
                operations_revision=OPERATIONS_REVISION)
    terminal=state['status'] in ('COMPLETE','EXPIRED')
    if not terminal or not state.get('terminal_logged'):
        # Progress updates are not extra independent scans in evaluation reports.
        store.event('SCAN_HEALTH' if terminal else 'SCAN_PROGRESS',str(state['slot']),health,client.now())
        log('TA_PAPER_SCAN',**health)
        if terminal:
            state['terminal_logged']=True
    if state['status']=='COMPLETE':
        store.setting('last_complete_scan_slot_v2',state['slot'])
    _save_scan(store,state)
    return health


def scan(store, client, cfg=CFG):
    """Resume one frozen universe; never turn a partial scan into a completed slot."""
    started=time.monotonic(); now=client.now()
    slot=(now-cfg.publication_delay_ms)//cfg.entry_ms
    raw=store.setting(SCAN_STATE_KEY)
    state=json.loads(raw) if raw else None
    if state and state['slot']!=slot and state['status'] not in ('COMPLETE','EXPIRED'):
        state['status']='EXPIRED'
        _scan_health(store,client,state,started)
    if not state or state['slot']!=slot:
        state=dict(slot=slot,deadline_ms=slot*cfg.entry_ms+cfg.max_scan_delay_ms-1,
                   status='PENDING',markets={},tickers={},done={},retry_at={},handled={},
                   attempted=[],request_attempts=0,errors={},initialized=False)
        _save_scan(store,state)
    if state['status'] in ('COMPLETE','EXPIRED'):
        return _scan_health(store,client,state,started)
    if now>state['deadline_ms']:
        state['status']='EXPIRED'
        return _scan_health(store,client,state,started)
    if _cooldown(client)['remaining_seconds']:
        return _scan_health(store,client,state,started)
    if not state['initialized']:
        client.sync_time()
        now=client.now()
        metadata=client.get('/fapi/v1/exchangeInfo')
        tickers=client.get('/fapi/v1/ticker/24hr')
        if not isinstance(metadata,dict) or not isinstance(tickers,list):
            raise DataUnavailable('UNIVERSE_RESPONSE_INVALID')
        by_symbol={t['symbol']:t for t in tickers}
        markets={m['symbol']:m for m in metadata['symbols']
                 if tradable(m,by_symbol.get(m['symbol']),now,cfg)}
        if not markets:
            raise DataUnavailable('EMPTY_OR_STALE_UNIVERSE')
        store.snapshot('UNIVERSE',{'metadata':metadata,'tickers':tickers},now)
        state.update(markets=markets,tickers={k:by_symbol[k] for k in markets},initialized=True)
        _save_scan(store,state)
    markets=state['markets']; by_symbol=state['tickers']
    batch=0
    for symbol in markets:
        if symbol in state['done'] or client.now()<state['retry_at'].get(symbol,0):
            continue
        if (STOP.is_set() or _cooldown(client)['remaining_seconds'] or
                client.now()>state['deadline_ms'] or time.monotonic()-started>=SCAN_BATCH_SECONDS or
                batch>=SCAN_BATCH_SYMBOLS):
            break
        batch+=1;state['request_attempts']+=1
        if symbol not in state['attempted']:state['attempted'].append(symbol)
        try:
            b=client.candles(symbol,'30m',cfg.history_bars)
            c=client.candles(symbol,'4h',cfg.history_bars)
            stamp=client.now()
            if stamp>state['deadline_ms']:
                break
            p=assess(symbol,b,c,stamp,cfg)
            if p['status']=='SETUP':
                p.update(context_fields(p,closed_bars(b,cfg.entry_ms,stamp,cfg=cfg),
                                        closed_bars(c,cfg.context_ms,stamp,cfg=cfg)))
            store.candles(symbol,'30m',closed_bars(b,cfg.entry_ms,stamp,cfg=cfg))
            store.candles(symbol,'4h',closed_bars(c,cfg.context_ms,stamp,cfg=cfg))
            # Reuse immutable evidence when a crash occurred between assessment and checkpoint.
            key=f"{symbol}:{p['candle_ms']}:{cfg.fingerprint}"
            saved=store.db.execute('SELECT payload FROM assessments WHERE key=?',(key,)).fetchone()
            if saved:p=json.loads(saved[0])
            else:store.assessment(p)
            if p['status']=='SETUP':
                record_setup(store,dict(p),stamp,markets[symbol],'WAITING_SCAN_COVERAGE')
            state['done'][symbol]=p
            state['retry_at'].pop(symbol,None)
        except (DataUnavailable,ValueError,KeyError,TypeError) as exc:
            reason=str(exc)[:120] if isinstance(exc,(DataUnavailable,ValueError)) else type(exc).__name__
            state['errors'][reason]=state['errors'].get(reason,0)+1
            state['retry_at'][symbol]=max(client.now()+MINUTE,_cooldown(client).get('until_ms',0))
            store.event('DATA_ISSUE',symbol,{'reason':reason,'scan_slot':slot},client.now())
        _save_scan(store,state)
    candidates=[p for p in state['done'].values() if p['status']=='SETUP']
    candidates.sort(key=lambda p:(-float(by_symbol[p['symbol']]['quoteVolume']),p['symbol']))
    btc_trend=state['done'].get('BTCUSDT',{}).get('trend')
    admitted=0
    # Complete data coverage is required before portfolio admission or a READY message.
    if len(state['done'])!=len(markets):
        candidates=[]
    for original in candidates:
        symbol = original['symbol']
        if symbol in state['handled']:
            continue
        if _cooldown(client)['remaining_seconds'] or client.now() > state['deadline_ms']:
            break
        # A crash after saving a plan must never duplicate or re-size that plan.
        if any(x['id'] == setup_id(original) for x in store.plans()):
            state['handled'][symbol] = 'ACCEPTED'
            _save_scan(store,state)
            continue
        if client.now() < state['retry_at'].get(symbol,0):
            continue
        p = dict(original)
        stamp = client.now()
        p.update(id=setup_id(p),entry_ms=((stamp+cfg.entry_delay_ms)//MINUTE+1)*MINUTE,
                 expires_ms=stamp+cfg.max_pending_ms)
        sign=1 if p['direction']=='LONG' else -1
        p['btc_agreement']=None if btc_trend is None else 'NEUTRAL' if btc_trend==0 else 'SAME' if btc_trend==sign else 'OPPOSITE'
        record_setup(store,p,stamp,markets[p['symbol']])
        try:
            if stamp-p['candle_ms']-cfg.entry_ms > cfg.max_scan_delay_ms:
                raise ValueError('SCAN_FINISHED_TOO_LATE')
            plans = store.plans()
            if any(x['symbol'] == p['symbol'] and stamp-x['observed_ms'] < cfg.cooldown_ms for x in plans):
                raise ValueError('SYMBOL_COOLDOWN')
            active = store.plans(active=True)
            if any(x['status'] in ('DATA_HOLD', 'FUNDING_PENDING','UNRESOLVED_DATA') for x in active):
                raise ValueError('UNRESOLVED_OPEN_RISK')
            eq, day_start, day_pnl = account(store, stamp, cfg)
            room = risk_room(eq, day_start, day_pnl, active, p['direction'], cfg)
            size = sizing(p, markets[p['symbol']], eq, room, cfg=cfg)
            book = client.get('/fapi/v1/depth', symbol=p['symbol'], limit=100)
            stamp = client.now()
            evidence = book_check(book, stamp, p['direction'], size['notional'], cfg)
            p['entry_half_spread'] = evidence['spread']/2
            size = sizing(p, markets[p['symbol']], eq, room, cfg=cfg)
            spot_ref = evidence['ask'] if p['direction'] == 'LONG' else evidence['bid']
            sizing(p, markets[p['symbol']], eq, room, spot_ref, cfg, reference_is_quote=True)
            store.snapshot('BOOK:'+p['symbol'], book, stamp)
            p.update(size, book=evidence, market=markets[p['symbol']], status='PENDING',
                     observed_ms=stamp, entry_ms=((stamp+cfg.entry_delay_ms)//MINUTE+1)*MINUTE,
                     expires_ms=stamp+cfg.max_pending_ms)
            p['id'] = hashlib.sha256(f"{cfg.fingerprint}:{p['symbol']}:{p['candle_ms']}".encode()).hexdigest()
            store.save(p)
            record_setup(store,p,stamp,markets[p['symbol']],'ACCEPTED')
            notify(store, p, stamp)
            admitted += 1
            state['handled'][symbol] = 'ACCEPTED'
        except (ValueError, DataUnavailable, KeyError, TypeError) as exc:
            reason = str(exc)[:120] if isinstance(exc, (ValueError, DataUnavailable)) else type(exc).__name__
            if isinstance(exc,DataUnavailable):
                state['errors']['ADMISSION_DATA:'+reason] = state['errors'].get('ADMISSION_DATA:'+reason,0)+1
                state['retry_at'][symbol] = max(stamp+MINUTE,_cooldown(client).get('until_ms',0))
            else:
                state['handled'][symbol] = reason
            store.event('ADMISSION_REJECTED', p['symbol'], {'reason': reason, 'setup': p}, stamp)
            record_setup(store,p,stamp,markets[p['symbol']],reason)
        _save_scan(store,state)
        if _cooldown(client)['remaining_seconds']:
            break
    if client.now()>state['deadline_ms']:
        state['status']='EXPIRED'
    elif (len(state['done'])==len(markets) and
          all(p['symbol'] in state['handled'] for p in candidates)):
        state['status']='COMPLETE'
    return _scan_health(store,client,state,started,admitted)


def stop_research(child):
    if child and child.poll() is None:
        try:
            os.killpg(child.pid,signal.SIGTERM)
            child.wait(timeout=10)
        except (OSError,subprocess.TimeoutExpired):
            try:os.killpg(child.pid,signal.SIGKILL)
            except OSError:pass


def research_notice(store,root,now):
    folder=Path(root)/'research'/store.provenance['source_sha256'][:16]
    status_path=folder/'job_status.json'
    if not status_path.exists():return
    state=json.loads(status_path.read_text());phase=state['status']
    if phase=='RUNNING':return
    key='system:research:'+store.provenance['source_sha256']+':'+phase
    if store.setting(key):return
    if phase.startswith('COMPLETE'):
        report=json.loads((folder/'report.json').read_text())
        result=report['results']['base']['strategy']['all_setups']['all']
        value='—' if result['mean_net_r'] is None else f"{result['mean_net_r']:+.3f}R"
        text=("⚪ TA PAPER · İlk arşiv araştırması tamamlandı\n"
              f"Ağustos 2026 · {report['completed_symbols']} sembol işlendi.\n"
              f"Portföy sınırı olmayan model: {result['n_closed']} kapanan, ortalama {value}.\n"
              f"Tamamlanmamış sonuç: {result['incomplete_outcomes']}.\n"
              "Bu keşif testi; daha önce görülmemiş dönem doğrulaması veya kazanç garantisi değil. "
              "Emir defteri, geçmiş lot bilgisi ve gecikme varsayımları içerir. "
              "Maliyet stresi, kontroller ve LONG/SHORT ayrımı ayrıntılı raporda. Başarı DOĞRULANMADI.")
        if state.get('error_symbols'):text+=f"\nVerisi işlenemeyen sembol: {state['error_symbols']}."
    else:
        text=("⚪ TA PAPER · Arşiv araştırması tamamlanamadı\n"
              f"Aşama/neden: {state.get('phase','—')} / {state.get('reason','UNKNOWN')}\n"
              "Sonuç kârlılık kanıtı olarak kullanılmıyor. Canlı paper motoru ayrı çalışır.")
    store.queue(key,text,now);store.setting(key,'queued')


def previous_version_safe(root):
    """Do not silently abandon pending risk when starting a new research cohort."""
    path=Path(root)/'ta_paper_v1_20261026'/'research.sqlite3'
    if not path.exists():return
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
        n=db.execute("SELECT COUNT(*) FROM plans WHERE status NOT IN ('CLOSED','CANCELLED')").fetchone()[0]
    if n:
        raise RuntimeError('PREVIOUS_VERSION_HAS_UNRESOLVED_PLANS_KEEP_OLD_WORKER')


def run(args):
    root = Path(args.state_root or os.getenv('RAILWAY_VOLUME_MOUNT_PATH', '.long-short-state'))
    root.mkdir(parents=True, exist_ok=True)
    folder = root/STATE_FOLDER
    folder.mkdir(exist_ok=True)
    # Durable per-version lock prevents duplicate workers on the same volume.
    with ExitStack() as locks:
        paths=[root/'ta_paper_worker.lock',folder/'worker.lock']
        previous=root/'ta_paper_v1_20261026'/'worker.lock'
        if previous.exists():paths.append(previous)
        for path in paths:
            lock=locks.enter_context(open(path,'a'))
            fcntl.flock(lock,fcntl.LOCK_EX | fcntl.LOCK_NB)
        if not args.report:previous_version_safe(root.resolve())
        store = Store(folder/'research.sqlite3')
        telegram = Telegram(root, enabled=args.telegram)
        if args.report:
            plans = store.plans()
            log('TA_PAPER_REPORT', all=descriptive_stats(plans),
                long=descriptive_stats([p for p in plans if p['direction']=='LONG']),
                short=descriptive_stats([p for p in plans if p['direction']=='SHORT']),
                funnel=funnel_report(store),provenance=store.provenance)
            return 0
        # A research-version change must not erase a known IP deadline.
        previous_cooldown=root/'ta_paper_v1_20261026'/'binance_cooldown.json'
        if not (folder/'binance_cooldown.json').exists() and previous_cooldown.exists():
            shutil.copyfile(previous_cooldown,folder/'binance_cooldown.json')
        client = Binance(folder/'binance_cooldown.json', legacy=store.legacy_cooldown())
        log('TA_PAPER_BOOT', version=CFG.version, config_hash=CFG.fingerprint,
            automatic_orders=False, telegram_ready=telegram.ready, legacy_motors_started=False,
            account_basis='EXAMPLE_PAPER_ACCOUNT', initial_equity=CFG.paper_initial_equity,
            operations_revision=OPERATIONS_REVISION, cooldown=client.gate.state(),
            provenance=store.provenance)
        previous_ops=store.setting('operations_revision')
        if previous_ops!=OPERATIONS_REVISION:
            store.event('OPERATIONS_REVISION',OPERATIONS_REVISION,
                        dict(previous=previous_ops,config_hash=CFG.fingerprint,
                             provenance=store.provenance,change='resumable_scan_and_honest_health'))
            store.setting('operations_revision',OPERATIONS_REVISION)
        boot_key='system:charts:'+CHART_REVISION
        if not store.setting(boot_key):
            n=descriptive_stats(store.plans())['n']
            photo=None
            try:
                from .chart import preview
                photo=preview(client.now(),n=n)
            except Exception as exc:
                log('TA_PAPER_CHART_ERROR',type=type(exc).__name__)
            text=('⚪ TA PAPER · Grafikli bildirim sürümü açıldı.\n'
                  'Bu görsel YAPAY VERİYLE hazırlanmış teslim testidir; gerçek coin önerisi değildir.\n'
                  'Gerçek planlarda giriş, SL, TP, risk/ödül ve USDT tutarları yer alacak.\n'
                  'Piyasa verisinin sağlıklı olduğu henüz doğrulanmadı. Gerçek emir yok. '
                  f'Başarı DOĞRULANMADI (N={n}).')
            store.queue(boot_key,text,client.now(),photo=photo)
            store.setting(boot_key,'queued')
        # Service/health messages must work even when Binance is unavailable.
        telegram.flush(store,client.now(),signals_enabled=False)
        if getattr(args, 'recheck_unknown_cooldown', False):
            try:
                checked = client.recheck_unknown_cooldown(RECHECK_REQUEST)
                outcome = 'TIME_ENDPOINT_OK' if checked else 'SKIPPED_KNOWN_OR_ALREADY_CHECKED'
            except (DataUnavailable, ValueError, KeyError, TypeError) as exc:
                outcome = str(exc) if isinstance(exc, DataUnavailable) else type(exc).__name__
            cooldown = client.gate.state()
            log('TA_PAPER_USER_RECHECK', request_id=RECHECK_REQUEST, outcome=outcome, cooldown=cooldown)
            if outcome != 'SKIPPED_KNOWN_OR_ALREADY_CHECKED':
                store.event('USER_RECHECK', RECHECK_REQUEST, dict(outcome=outcome, cooldown=cooldown))
                text = ('⚪ Tek seferlik bağlantı kontrolü başarılı.\nBinance saat servisi yanıt verdi; '
                        'varsayılan 72 saat bekleme kaldırıldı. Piyasa verisi ayrıca doğrulanacak. Gerçek emir yok.'
                        if outcome == 'TIME_ENDPOINT_OK' else health_message(outcome, cooldown))
                store.queue('system:recheck:'+RECHECK_REQUEST, text, client.now())
                telegram.flush(store, client.now(), signals_enabled=False)
        child=None
        if getattr(args,'historical_research',False):
            root_job=folder/'research'
            status_path=root_job/store.provenance['source_sha256'][:16]/'job_status.json'
            status=json.loads(status_path.read_text()).get('status') if status_path.exists() else None
            if status not in ('COMPLETE','COMPLETE_WITH_GAPS','FAILED'):
                child=subprocess.Popen([sys.executable,'-u','-m','ta_paper.research_job','--root',str(root_job)],start_new_session=True)
                locks.callback(stop_research,child)
                log('TA_RESEARCH_STARTED',pid=child.pid,source_sha256=store.provenance['source_sha256'])
        last_heartbeat = 0
        last_clock_sync = 0.0
        last_data_error = None
        while not STOP.is_set():
            iteration_valid = False
            progress=json.loads(store.setting(SCAN_STATE_KEY) or '{}')
            if progress and progress['status'] not in ('COMPLETE','EXPIRED') and client.now()>progress['deadline_ms']:
                progress['status']='EXPIRED'
                _scan_health(store,client,progress,time.monotonic())
            try:
                if time.monotonic()-last_clock_sync > 60:
                    client.sync_time()
                    last_clock_sync = time.monotonic()
                now = client.now()
                advance(store, client)
                health = scan(store, client)
                if client.gate.state()['remaining_seconds']:
                    raise DataUnavailable('EXCHANGE_COOLDOWN')
                if not health['complete']:
                    raise DataUnavailable('SCAN_EXPIRED' if health['status']=='EXPIRED' else 'SCAN_INCOMPLETE')
                update_health(store,'READY',client.gate.state(),client.now(),health)
                last_data_error = None
                iteration_valid = True
            except (DataUnavailable, ValueError, KeyError, TypeError) as exc:
                reason = str(exc)[:120] if isinstance(exc, (DataUnavailable, ValueError)) else type(exc).__name__
                cooldown=client.gate.state()
                if reason=='EXCHANGE_COOLDOWN':
                    reason=cooldown['reason']
                if reason != last_data_error:
                    store.event('DATA_HEALTH_INVALID', 'runtime', {'reason': reason, 'cooldown':cooldown})
                    log('TA_PAPER_DATA_INVALID', reason=reason, no_new_signal=True, cooldown=cooldown)
                    last_data_error=reason
                progress=json.loads(store.setting(SCAN_STATE_KEY) or '{}')
                update_health(store,reason,cooldown,client.now(),progress)
            if getattr(args,'historical_research',False):
                if child and child.poll() is not None:
                    status_path=folder/'research'/store.provenance['source_sha256'][:16]/'job_status.json'
                    state=json.loads(status_path.read_text()) if status_path.exists() else {}
                    if state.get('status') not in ('COMPLETE','COMPLETE_WITH_GAPS','FAILED'):
                        from .research_job import atomic_json
                        status_path.parent.mkdir(parents=True,exist_ok=True)
                        atomic_json(status_path,dict(state,status='FAILED',reason='RESEARCH_PROCESS_EXIT',returncode=child.returncode))
                    child=None
                research_notice(store,folder,client.now())
            telegram.flush(store, client.now(), signals_enabled=iteration_valid)
            progress=json.loads(store.setting(SCAN_STATE_KEY) or '{}')
            if progress.get('status') in ('COMPLETE','EXPIRED') and not client.gate.state()['remaining_seconds']:
                collect(store,client)
            now = client.now()
            if now-last_heartbeat >= 5*MINUTE:
                plans = store.plans()
                log('TA_PAPER_HEARTBEAT', utc=datetime.now(timezone.utc).isoformat(),
                    states=dict(Counter(p['status'] for p in plans)), telegram_ready=telegram.ready,
                    performance=descriptive_stats(plans),
                    funnel=funnel_report(store,include_coverage=False),
                    cooldown=client.gate.state(),
                    request_usage=client.usage() if hasattr(client,'usage') else {},
                    scan_progress=json.loads(store.setting(SCAN_STATE_KEY) or '{}').get('status','NOT_STARTED'),
                    peak_rss_mb=round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024, 2))
                last_heartbeat = now
            if args.once:
                return 0 if iteration_valid else 2
            STOP.wait(15)
        return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--report', action='store_true')
    parser.add_argument('--telegram', action='store_true', help='Use only the existing configured destination')
    parser.add_argument('--state-root')
    parser.add_argument('--historical-research',action='store_true',help='Run resumable, bounded archive validation alongside paper observation')
    parser.add_argument('--recheck-unknown-cooldown', action='store_true',
                        help='One explicitly requested check of an assumed timer; known exchange deadlines are respected')
    args = parser.parse_args()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: STOP.set())
    return run(args)


if __name__ == '__main__':
    raise SystemExit(main())

