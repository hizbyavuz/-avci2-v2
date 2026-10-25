"""One paper engine; never imports or starts a legacy motor."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import resource
import signal
import threading
import time

from .core import (CFG, MINUTE, assess, closed_bars, descriptive_stats, exit_on_bar,
                   funding_cost, risk_room, sizing)
from .io import Binance, DataUnavailable, Store, Telegram, book_check, tradable

STOP = threading.Event()
OPERATIONS_REVISION = 'CHARTS_20261026_1'


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


def update_health(store, reason, cooldown, now):
    identity = reason+':'+str(cooldown.get('until_ms',0) if cooldown.get('remaining_seconds',0) else 0)
    if store.setting('notification_health') == identity:
        return
    store.setting('notification_health',identity)
    text = ('⚪ TA PAPER · Veri akışı doğrulandı\nGrafikli test bildirimleri açık. '
            'Uygun koşul yoksa işlem planı gönderilmez.\nGerçek emir yok; başarı DOĞRULANMADI.'
            if reason=='READY' else health_message(reason,cooldown))
    store.queue('system:health:'+str(now),text,now)
    store.event('RUNTIME_HEALTH',reason,dict(cooldown,reason=reason),now)


def advance(store, client, cfg=CFG):
    now = client.now()
    for p in store.plans(active=True):
        try:
            if p['status'] == 'DATA_HOLD':
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
            raw = client.candles(p['symbol'], '1m', limit=1000, startTime=start,
                                 endTime=now-cfg.publication_delay_ms)
            bars = closed_bars(raw, MINUTE, now, minimum=1, require_current=False, cfg=cfg)
            if bars[0][0] != start:
                raise DataUnavailable('ENTRY_OR_OUTCOME_PATH_GAP')
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
            store.save(p)
        except (DataUnavailable, ValueError, KeyError, TypeError) as exc:
            reason = str(exc)[:120] if isinstance(exc, (ValueError, DataUnavailable)) else type(exc).__name__
            p.update(resume_status=p['status'], status='DATA_HOLD', data_issue=reason)
            store.save(p)
            store.event('DATA_ISSUE', p['id'], {'reason': reason}, now)


def scan(store, client, cfg=CFG):
    started = time.monotonic()
    client.sync_time()
    now = client.now()
    metadata = client.get('/fapi/v1/exchangeInfo')
    tickers = client.get('/fapi/v1/ticker/24hr')
    if not isinstance(metadata, dict) or not isinstance(tickers, list):
        raise DataUnavailable('UNIVERSE_RESPONSE_INVALID')
    store.snapshot('UNIVERSE', {'metadata': metadata, 'tickers': tickers}, now)
    by_symbol = {t['symbol']: t for t in tickers}
    markets = {m['symbol']: m for m in metadata['symbols']
               if tradable(m, by_symbol.get(m['symbol']), now, cfg)}
    if not markets:
        raise DataUnavailable('EMPTY_OR_STALE_UNIVERSE')
    counts, candidates = Counter(), []
    def fetch(m):
        symbol = m['symbol']
        return (symbol, client.candles(symbol, '30m', cfg.history_bars),
                client.candles(symbol, '4h', cfg.history_bars))
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(fetch, m): symbol for symbol, m in markets.items()}
        for future in as_completed(futures):
            symbol = futures[future]
            if STOP.is_set():
                for pending in futures:
                    pending.cancel()
                break
            try:
                symbol, b, c = future.result()
                stamp = client.now()
                p = assess(symbol, b, c, stamp, cfg)
                store.candles(symbol, '30m', closed_bars(b, cfg.entry_ms, stamp, cfg=cfg))
                store.candles(symbol, '4h', closed_bars(c, cfg.context_ms, stamp, cfg=cfg))
                fresh = store.assessment(p)
                counts[p['reason']] += 1
                if fresh and p['status'] == 'SETUP':
                    candidates.append(p)
            except (DataUnavailable, ValueError, KeyError, TypeError) as exc:
                reason = str(exc)[:120] if isinstance(exc, (DataUnavailable, ValueError)) else type(exc).__name__
                counts['DATA:'+reason] += 1
                store.event('DATA_ISSUE', symbol, {'reason': reason}, client.now())
    # A deterministic liquidity order is capacity allocation, not a confidence score.
    candidates.sort(key=lambda p: (-float(by_symbol[p['symbol']]['quoteVolume']), p['symbol']))
    admitted = 0
    for p in candidates:
        stamp = client.now()
        try:
            if stamp-p['candle_ms']-cfg.entry_ms > cfg.max_scan_delay_ms:
                raise ValueError('SCAN_FINISHED_TOO_LATE')
            plans = store.plans()
            if any(x['symbol'] == p['symbol'] and stamp-x['observed_ms'] < cfg.cooldown_ms for x in plans):
                raise ValueError('SYMBOL_COOLDOWN')
            active = store.plans(active=True)
            if any(x['status'] in ('DATA_HOLD', 'FUNDING_PENDING') for x in active):
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
            sizing(p, markets[p['symbol']], eq, room, spot_ref, cfg)  # current anti-chase gate
            store.snapshot('BOOK:'+p['symbol'], book, stamp)
            p.update(size, book=evidence, market=markets[p['symbol']], status='PENDING',
                     observed_ms=stamp, entry_ms=((stamp+cfg.entry_delay_ms)//MINUTE+1)*MINUTE,
                     expires_ms=stamp+cfg.max_pending_ms)
            p['id'] = hashlib.sha256(f"{cfg.fingerprint}:{p['symbol']}:{p['candle_ms']}".encode()).hexdigest()
            store.save(p)
            notify(store, p, stamp)
            admitted += 1
        except (ValueError, DataUnavailable, KeyError, TypeError) as exc:
            reason = str(exc)[:120] if isinstance(exc, (ValueError, DataUnavailable)) else type(exc).__name__
            counts['ADMISSION:'+reason] += 1
            store.event('ADMISSION_REJECTED', p['symbol'], {'reason': reason, 'setup': p}, stamp)
    elapsed = round(time.monotonic()-started, 2)
    healthy = sum(v for k,v in counts.items() if not k.startswith(('DATA:', 'ADMISSION:')))
    health = dict(universe=len(markets), assessed=healthy, admitted=admitted, reasons=dict(counts),
                  elapsed_seconds=elapsed, peak_rss_mb=round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024, 2),
                  config_hash=cfg.fingerprint, automatic_orders=False)
    store.event('SCAN_HEALTH', cfg.fingerprint, health, client.now())
    log('TA_PAPER_SCAN', **health)
    return health


def run(args):
    root = Path(args.state_root or os.getenv('RAILWAY_VOLUME_MOUNT_PATH', '.long-short-state'))
    root.mkdir(parents=True, exist_ok=True)
    folder = root/'ta_paper_v1_20261026'
    folder.mkdir(exist_ok=True)
    # Durable per-version lock prevents duplicate workers on the same volume.
    with open(folder/'worker.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        store = Store(folder/'research.sqlite3')
        telegram = Telegram(root, enabled=args.telegram)
        if args.report:
            plans = store.plans()
            log('TA_PAPER_REPORT', all=descriptive_stats(plans),
                long=descriptive_stats([p for p in plans if p['direction']=='LONG']),
                short=descriptive_stats([p for p in plans if p['direction']=='SHORT']))
            return 0
        client = Binance(folder/'binance_cooldown.json', legacy=store.legacy_cooldown())
        log('TA_PAPER_BOOT', version=CFG.version, config_hash=CFG.fingerprint,
            automatic_orders=False, telegram_ready=telegram.ready, legacy_motors_started=False,
            account_basis='EXAMPLE_PAPER_ACCOUNT', initial_equity=CFG.paper_initial_equity,
            operations_revision=OPERATIONS_REVISION, cooldown=client.gate.state())
        boot_key='system:charts:'+OPERATIONS_REVISION
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
        last_heartbeat = 0
        last_clock_sync = 0.0
        last_data_error = None
        while not STOP.is_set():
            iteration_valid = False
            try:
                if time.monotonic()-last_clock_sync > 60:
                    client.sync_time()
                    last_clock_sync = time.monotonic()
                now = client.now()
                advance(store, client)
                slot = (now-CFG.publication_delay_ms)//CFG.entry_ms
                if store.setting('last_scan_slot') != str(slot):
                    health = scan(store, client)
                    if not health['assessed']:
                        raise DataUnavailable('NO_VALID_CANDLES')
                    store.setting('last_scan_slot', slot)
                if client.gate.state()['remaining_seconds']:
                    raise DataUnavailable('EXCHANGE_COOLDOWN')
                update_health(store,'READY',client.gate.state(),client.now())
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
                update_health(store,reason,cooldown,client.now())
            telegram.flush(store, client.now(), signals_enabled=iteration_valid)
            now = client.now()
            if now-last_heartbeat >= 5*MINUTE:
                plans = store.plans()
                log('TA_PAPER_HEARTBEAT', utc=datetime.now(timezone.utc).isoformat(),
                    states=dict(Counter(p['status'] for p in plans)), telegram_ready=telegram.ready,
                    performance=descriptive_stats(plans),
                    cooldown=client.gate.state(),
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
    args = parser.parse_args()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: STOP.set())
    return run(args)


if __name__ == '__main__':
    raise SystemExit(main())
