"""V2: closed 4h context, closed 15m break/retest timing, no order API.

Thresholds are declared research hypotheses, not fitted win probabilities.
V1.1 remains byte-for-byte frozen for paired observation.
"""
import hashlib
import json
import statistics
from pathlib import Path
import long_short_major_move_v1_1_frozen as baseline
from long_short_major_move_v1_1_frozen import finite, atr, pivots, validate, planned_geometry

VERSION = 'MAJOR_CONTEXT_ENTRY_V2_2026_10_16'
BASELINE_ID = '16d77225ac66b9fd9d1d0f48c5df2103d73a72bc8d2b65ffd6db08fe01f0d493'
CONFIG = dict(baseline.CONFIG, version=VERSION, min_room_pct=10.,
              obstacle_confirmation_atr15=1., stop_buffer_atr15=.15,
              retest_tolerance_atr15=.15, retest_bars=4,
              entry_valid_ms=900000, poll_seconds=2, quote_retry_seconds=5,
              data_retry_seconds=10, maintenance_seconds=30,
              baseline_id=BASELINE_ID, room_is_veto=False)
CONFIG_HASH = hashlib.sha256(json.dumps(CONFIG, sort_keys=True).encode()).hexdigest()


def strategy_id():
    names = ['long_short_major_move.py', 'long_short_major_move_worker.py',
             'long_short_major_move_store.py', 'long_short_major_move_v1_1_frozen.py']
    return hashlib.sha256(CONFIG_HASH.encode() + b''.join(Path(__file__).with_name(n).read_bytes() for n in names)).hexdigest()


def ema(values, period=20):
    value = values[0]
    for item in values[1:]:
        value += 2 / (period + 1) * (item - value)
    return value


def trend(rows):
    closes = [r[4] for r in rows]
    level = ema(closes)
    prior = ema(closes[:-1])
    return 'UP' if closes[-1] > level and level > prior else 'DOWN' if closes[-1] < level and level < prior else 'MIXED'


def features(frames, btc_frames, now_ms):
    f = baseline.features(frames, btc_frames, now_ms)
    m = frames['15m']; b = btc_frames['15m']; h = frames['1h']; four = frames['4h']
    if m[-1][6] != b[-1][6]:
        raise ValueError('BTC_NOT_TIME_ALIGNED_15m')
    p = m[-1][4]
    # Twenty-four complete, non-overlapping 1h volume windows, excluding current hour.
    volumes = [sum(r[5] for r in m[i:i+4]) for i in range(len(m)-100, len(m)-4, 4)]
    normal = statistics.median(volumes)
    if normal <= 0:
        raise ValueError('VOLUME_BASELINE_EMPTY')
    r4 = (p / m[-17][4] - 1) * 100
    btc4 = (b[-1][4] / b[-17][4] - 1) * 100
    recent = h[-72:]
    trough_i = min(range(len(recent)), key=lambda i: recent[i][3])
    before = recent[:trough_i]
    down_peak = max((r[2] for r in before), default=recent[0][1])
    down_trough = recent[trough_i][3]
    days = {}
    for r in four:
        days.setdefault(r[0] // 86400000, []).append(r)
    complete = []
    for rows in days.values():
        if len(rows) == 6 and rows[0][0] % 86400000 == 0:
            complete.append([rows[0][0], rows[0][1], max(r[2] for r in rows),
                             min(r[3] for r in rows), rows[-1][4], sum(r[5] for r in rows), rows[-1][6]])
    pre = m[-17:-1]; older = m[-113:-17]
    previous_range = statistics.mean(r[2]-r[3] for r in older)
    compression = statistics.mean(r[2]-r[3] for r in pre) / previous_range if previous_range > 0 else float('inf')
    f.update(price=p, return_4h_pct=r4, btc_return_4h_pct=btc4,
             return_24h_pct=(p/m[-97][4]-1)*100,
             relative_strength_4h_pp=r4-btc4,
             volume_1h_multiple=sum(r[5] for r in m[-4:])/normal,
             compression_ratio=compression,
             retention=(p-f['leg_base'])/(f['leg_peak']-f['leg_base']) if f['leg_peak']>f['leg_base'] else 0.,
             drawdown_from_peak_pct=(p/f['leg_peak']-1)*100,
             down_leg_pct=(1-down_trough/down_peak)*100,
             down_peak=down_peak, down_trough=down_trough,
             context_4h=trend(four), context_4h_close_ms=four[-1][6],
             context_daily=trend(complete) if len(complete)>=20 else 'UNAVAILABLE',
             context_daily_close_ms=complete[-1][6] if complete else None)
    return f


def choose_model(f):
    strong = f['relative_strength_4h_pp'] >= CONFIG['relative_strength_pp']
    weak = f['relative_strength_4h_pp'] <= -CONFIG['relative_strength_pp']
    up = f['return_4h_pct'] > 0
    down = f['return_4h_pct'] < 0
    broken = f['confirmed_hour_lows'] and f['price'] < f['confirmed_hour_lows'][-1][1]
    if f['leg_gain_pct'] >= CONFIG['major_leg_pct'] and broken and weak and down:
        return 'TOP_BREAKDOWN', 'SHORT'
    if f['context_4h'] == 'DOWN' and f['down_leg_pct'] >= CONFIG['major_leg_pct'] and weak and down:
        return 'TREND_CONTINUATION_SHORT', 'SHORT'
    if f['context_4h'] != 'DOWN' and strong and up:
        if f['leg_gain_pct'] >= CONFIG['major_leg_pct'] and f['retention'] >= CONFIG['min_retention']:
            return 'MAJOR_CONTINUATION', 'LONG'
        if f['compression_ratio'] <= CONFIG['compression_ratio'] and f['volume_1h_multiple'] >= CONFIG['wake_volume_multiple']:
            return 'WAKE_UP', 'LONG'
    return None, None


def entry_event(rows, side, a15, level=None):
    """Only closed candles; a retest reuses the level frozen at its original break."""
    sign = 1 if side == 'LONG' else -1
    latest = len(rows)-1
    for i in range(latest, max(8, latest-CONFIG['retest_bars'])-1, -1):
        prior = rows[i-8:i]
        trigger = level if level is not None else (max(r[2] for r in prior) if sign == 1 else min(r[3] for r in prior))
        if sign*(rows[i][4]-trigger) <= 0 or sign*(rows[i-1][4]-trigger) > 0:
            continue
        if i == latest:
            return dict(kind='BREAK', trigger=trigger, break_ms=rows[i][6])
        held = all(sign*(r[4]-trigger) > 0 for r in rows[i+1:])
        touch = rows[-1][3] <= trigger + CONFIG['retest_tolerance_atr15']*a15 if sign == 1 else rows[-1][2] >= trigger-CONFIG['retest_tolerance_atr15']*a15
        if held and touch:
            return dict(kind='RETEST', trigger=trigger, break_ms=rows[i][6])
    return None


def assess(symbol, frames, price, quote_volume, now_ms, btc_frames, flow=None):
    out = dict(version=VERSION, config_hash=CONFIG_HASH, symbol=symbol, observed_ms=now_ms,
               price=price, stage='REJECTED', reason=None, direction='NONE', features={})
    def reject(reason):
        out['reason'] = reason
        return out
    if not finite(quote_volume) or quote_volume < CONFIG['min_quote_volume']:
        return reject('LIQUIDITY')
    if not finite(price) or price <= 0:
        return reject('FRESH_PRICE_MISSING')
    try:
        f = features(frames, btc_frames, now_ms)
    except (ValueError, IndexError, ZeroDivisionError) as exc:
        return reject(str(exc))
    out.update(features=f, hourly_flow=flow or {'status':'UNAVAILABLE'})
    a15 = f['atr15']; m = frames['15m']
    if a15 <= 0 or f['atr1h'] <= 0:
        return reject('ZERO_RANGE')
    model, side = choose_model(f)
    if not model:
        return reject('NO_MAJOR_MOVE_STRUCTURE')
    sign = 1 if side == 'LONG' else -1
    event = entry_event(m, side, a15)
    trigger = event['trigger'] if event else (max(r[2] for r in m[-9:-1]) if sign == 1 else min(r[3] for r in m[-9:-1]))
    levels = pivots(frames['4h'], high=sign == 1)
    # A nearby UNBROKEN obstacle is a confirmation level, not a 10%-room veto.
    # Use the last closed price to keep the required level fixed within this bar.
    near = [v for _, v in levels if 0 < sign*(v-f['price']) <= CONFIG['obstacle_confirmation_atr15']*a15 and sign*(v-trigger)>0]
    obstacle_wait = False
    if near:
        trigger = min(near) if sign == 1 else max(near)
        event = entry_event(m, side, a15, trigger)
        obstacle_wait = event is None
    local = pivots(m[-32:], high=sign == -1)
    usable = [v for _, v in local if sign*(trigger-v)>0]
    structural_stop = usable[-1] if usable else (min(r[3] for r in m[-9:]) if sign == 1 else max(r[2] for r in m[-9:]))
    # The latest confirming candle must not already violate the proposed stop.
    structural_stop = min(structural_stop,m[-1][3]) if sign == 1 else max(structural_stop,m[-1][2])
    stop = structural_stop-sign*CONFIG['stop_buffer_atr15']*a15
    geometry = planned_geometry(side, price, trigger, stop, levels)
    risk = sign*(price-stop)/price*100
    out.update(model=model, direction=side, trigger=trigger, stop=stop, **geometry)
    if risk <= 0:
        return reject('STRUCTURE_ALREADY_BROKEN')
    if geometry['planned_risk_pct'] > CONFIG['max_stop_pct']:
        return reject('PLANNED_STOP_TOO_DISTANT')
    if event and sign*(price-trigger) > CONFIG['chase_atr15']*a15:
        return reject('LATE_CHASE')
    obstacle = geometry['planned_obstacle']; room = geometry['planned_room_pct']
    fresh = 250 <= now_ms-m[-1][6] < CONFIG['entry_valid_ms']
    ready = bool(event and fresh and sign*(price-trigger)>=0)
    reason = 'OK' if ready else 'WAIT_OBSTACLE_BREAK' if obstacle_wait else 'WAIT_BREAK_OR_RETEST'
    out.update(stage='READY' if ready else 'WATCH', reason=reason, obstacle=obstacle,
               structural_room_pct=room, obstacle_to_risk=room/geometry['planned_risk_pct'] if room is not None else None,
               objective=price*(1+sign*.10), objective_pct=10., objective_label='RESEARCH_MILESTONE_NOT_FORECAST',
               risk_pct=risk, entry_limit=trigger+sign*CONFIG['chase_atr15']*a15,
               entry_kind=event['kind'] if event else None, break_ms=event['break_ms'] if event else None,
               entry_valid_until_ms=m[-1][6]+CONFIG['entry_valid_ms'],
               horizon_hours=CONFIG['max_plan_hours'], expires_ms=now_ms+CONFIG['max_plan_hours']*3600000,
               gain_before_signal_pct=(price/f['leg_base']-1)*100,
               confirmation_ms=m[-1][6], source='BINANCE_UM_FUTURES')
    return out


rank = baseline.rank


def message(p):
    if p.get('version') != VERSION:
        return baseline.message(p)
    from datetime import datetime, timezone, timedelta
    from long_short_chart_flow import flow_note
    names = {'WAKE_UP':'YÜKSELİŞ BAŞLANGICI', 'MAJOR_CONTINUATION':'YÜKSELİŞİN DEVAMI',
             'TOP_BREAKDOWN':'YÜKSELİŞ SONRASI BOZULMA', 'TREND_CONTINUATION_SHORT':'DÜŞÜŞÜN DEVAMI'}
    trends = {'UP':'yukarı', 'DOWN':'aşağı', 'MIXED':'karışık', 'UNAVAILABLE':'eksik'}
    f=p['features']; side=p['direction']; fmt=lambda x:f'{x:.8g}'
    stamp=datetime.fromtimestamp(p['observed_ms']/1000,timezone(timedelta(hours=3))).strftime('%d.%m %H:%M')
    return '\n'.join([
        f'{"🟢" if side=="LONG" else "🔴"} {side} | {p["symbol"]} — {names[p["model"]]}',
        'Durum: Giriş adayı; yeni modelin başarısı henüz doğrulanmadı.',
        f'Giriş referansı: {fmt(p["price"])} | Stop: {fmt(p["stop"])} (%{p["risk_pct"]:.1f})',
        f'Büyük yapı: 4 saatlik {trends[f["context_4h"]]}; günlük {trends[f["context_daily"]]}.',
        f'Giriş nedeni: 15 dakikalık {"kırılma" if p["entry_kind"]=="BREAK" else "kırılan seviyenin yeniden korunması"}; seviye {fmt(p["trigger"])}.',
        f'Hacim: normalin {f["volume_1h_multiple"]:.1f} katı | BTC’ye göre 4 saat: {f["relative_strength_4h_pp"]:+.1f} puan.',
        ('İlk 4 saatlik engel: '+fmt(p['obstacle'])+f' (%{p["structural_room_pct"]:.1f})' if p['obstacle'] else 'İleride doğrulanmış 4 saatlik engel bulunamadı; açık alan garantisi değildir.'),
        flow_note(p.get('hourly_flow'),side),
        f'%10 hareket ölçüm seviyesi: {fmt(p["objective"])}; fiyat tahmini değildir.',
        'Stop sonrası hareket de ölçülür. %10 sonrası saatlik yapıyla takip sürer; en çok 72 saat.',
        f'{stamp} Türkiye | MAJOR V2'])


def exit_message(p,g):
    lines=message(p).splitlines(); e=g['plan_exit']
    lines[1]='Durum: PLAN KAPANDI — '+('STOP / TAKİP EDEN STOP' if e['kind']=='STOP' else 'TAKİP SÜRESİ DOLDU')
    lines += [f'Varsayımsal plan sonucu: %{e["net_return_pct"]:+.2f} (toplam %0,30 masraf varsayımı).',
              f'En büyük lehte hareket: %{g["max_favorable_pct"]:.1f}. Gerçek hesap getirisi değildir.']
    return '\n'.join(lines)


def tracking_message(p,g):
    lines=message(p).splitlines();lines[1]='Durum: %10 eşiği görüldü; takip sürüyor.'
    return '\n'.join(lines+[f'Güncel takip eden stop: {g["active_stop"]:.8g}'])
