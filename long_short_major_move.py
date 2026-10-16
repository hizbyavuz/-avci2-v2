"""Versioned major-move hypotheses. No fitted probabilities or order API.

Selection precedes entry timing: wake-up, continuation, failed large advance.
All thresholds are declared research assumptions, not demonstrated edge.
"""
import hashlib
import json
import math
import statistics
from pathlib import Path

VERSION = 'MAJOR_MOVE_V1_1_PLANNED_ENTRY_2026_10_16'
CONFIG = dict(min_quote_volume=8000000, min_history_4h=180, major_leg_pct=10.0,
              wake_volume_multiple=1.5, relative_strength_pp=2.0,
              min_retention=.50, max_early_gain_pct=10.0,
              min_room_pct=10.0, max_stop_pct=8.0, chase_atr15=.5,
              compression_ratio=.8, stop_buffer_atr1h=.15, planned_entry_geometry=True,
              quote_max_age_ms=5000, max_active=12, max_new_per_scan=3,
              cooldown_hours=24, scan_seconds=60, round_trip_cost_pct=.30,
              horizons_hours=[6,24,72], reach_thresholds_pct=[10,20,30,40,50,60,70,80,100],
              primary_horizon_hours=24, max_plan_hours=72, candle_batch=240, outcome_batch=24)
CONFIG_HASH = hashlib.sha256(json.dumps(CONFIG,sort_keys=True).encode()).hexdigest()


def strategy_id():
    names = ['long_short_major_move.py','long_short_major_move_worker.py','long_short_major_move_store.py']
    return hashlib.sha256(CONFIG_HASH.encode()+b''.join(Path(__file__).with_name(n).read_bytes() for n in names)).hexdigest()


def finite(x):
    return isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x)


def atr(rows):
    pairs=list(zip(rows[-15:-1],rows[-14:]))
    return statistics.mean(max(b[2]-b[3],abs(b[2]-a[4]),abs(b[3]-a[4])) for a,b in pairs)


def pivots(rows, high=True):
    field=2 if high else 3
    values=[]
    for i in range(2,len(rows)-2):
        v=rows[i][field];around=[r[field] for r in rows[i-2:i]+rows[i+1:i+3]]
        if (v>max(around) if high else v<min(around)):values.append((rows[i][0],v))
    return values


def planned_geometry(direction, price, trigger, stop, levels):
    """Best entry still permitted by a directional break, not today's WATCH price.

    A later entry farther through the trigger can only worsen fixed-stop risk.
    Recompute the nearest obstacle from that entry; levels behind it do not
    become negative reward. Execution still rechecks the actual live quote.
    """
    sign=1 if direction=='LONG' else -1
    entry=max(price,trigger) if sign==1 else min(price,trigger)
    ahead=[v for _,v in levels if sign*(v-entry)>0]
    obstacle=(min(ahead) if sign==1 else max(ahead)) if ahead else None
    return dict(planned_entry=entry,planned_risk_pct=sign*(entry-stop)/entry*100,
                planned_obstacle=obstacle,
                planned_room_pct=sign*(obstacle/entry-1)*100 if obstacle else None)


def validate(frames, now_ms):
    for tf,period,count in [('15m',900000,96),('1h',3600000,96),('4h',14400000,CONFIG['min_history_4h'])]:
        rows=frames.get(tf,[])
        if len(rows)<count:raise ValueError('HISTORY_MISSING_'+tf)
        if not 250<=now_ms-rows[-1][6]<=period+20000:raise ValueError('STALE_'+tf)
        for i,r in enumerate(rows):
            if (r[0]%period or r[6]!=r[0]+period-1 or r[6]>=now_ms-249 or
                not all(finite(v) and v>0 for v in r[1:5]) or not finite(r[5]) or r[5]<0 or
                not r[3]<=min(r[1],r[4])<=max(r[1],r[4])<=r[2] or
                i and r[0]-rows[i-1][0]!=period):raise ValueError('INVALID_'+tf)


def features(frames, btc_frames, now_ms):
    validate(frames,now_ms);validate(btc_frames,now_ms)
    h=frames['1h'];four=frames['4h'];btc=btc_frames['1h'];p=h[-1][4]
    if h[-1][6]!=btc[-1][6]:raise ValueError('BTC_NOT_TIME_ALIGNED')
    volbase=statistics.median(r[5] for r in h[-49:-1])
    if volbase<=0:raise ValueError('VOLUME_BASELINE_EMPTY')
    vol=h[-1][5]/volbase
    r4=(p/h[-5][4]-1)*100;btc4=(btc[-1][4]/btc[-5][4]-1)*100
    recent=h[-72:];peak_i=max(range(len(recent)),key=lambda i:recent[i][2])
    # An observed trough must precede the peak, never a later low after a crash.
    before=recent[:peak_i]
    base=min((r[3] for r in before),default=recent[0][1]);peak=recent[peak_i][2]
    leg=(peak/base-1)*100;ret=(p-base)/(peak-base) if peak>base else 0
    box=h[-5:-1];older=h[-29:-5]
    width=max(r[2] for r in box)-min(r[3] for r in box)
    previous_range=statistics.mean(r[2]-r[3] for r in older)
    compression=statistics.mean(r[2]-r[3] for r in box)/previous_range if previous_range>0 else math.inf
    low=min(r[3] for r in four[-42:]);high=max(r[2] for r in four[-42:])
    return dict(price=p,return_4h_pct=r4,return_24h_pct=(p/h[-25][4]-1)*100,
        btc_return_4h_pct=btc4,relative_strength_4h_pp=r4-btc4,
        volume_1h_multiple=vol,compression_ratio=compression,box_width_pct=width/p*100,
        leg_base=base,leg_peak=peak,leg_gain_pct=leg,peak_ms=recent[peak_i][0],
        retention=ret,drawdown_from_peak_pct=(p/peak-1)*100,
        range_7d_position=(p-low)/(high-low) if high>low else .5,
        atr1h=atr(h),atr15=atr(frames['15m']),
        prior_day_high=max(r[2] for r in h[-25:-1]),
        box_high=max(r[2] for r in box),box_low=min(r[3] for r in box),
        confirmed_hour_highs=pivots(h[-72:],True),confirmed_hour_lows=pivots(h[-72:],False))


def assess(symbol,frames,price,quote_volume,now_ms,btc_frames,flow=None):
    out=dict(version=VERSION,config_hash=CONFIG_HASH,symbol=symbol,observed_ms=now_ms,
             price=price,stage='REJECTED',reason=None,direction='NONE',features={})
    def reject(reason):out['reason']=reason;return out
    if not finite(quote_volume) or quote_volume<CONFIG['min_quote_volume']:return reject('LIQUIDITY')
    if not finite(price) or price<=0:return reject('FRESH_PRICE_MISSING')
    try:f=features(frames,btc_frames,now_ms)
    except (ValueError,IndexError,ZeroDivisionError) as exc:return reject(str(exc))
    out['features']=f;out['hourly_flow']=flow or {'status':'UNAVAILABLE'}
    h=frames['1h'];m=frames['15m'];p=f['price'];a=f['atr1h'];a15=f['atr15']
    if a<=0 or a15<=0:return reject('ZERO_RANGE')
    hh=f['confirmed_hour_highs'];ll=f['confirmed_hour_lows']
    prior_m=m[-9:-1]
    lower_high=len(hh)>=2 and hh[-1][1]<hh[-2][1]
    broken_hour=bool(ll) and p<ll[-1][1]
    strong=f['relative_strength_4h_pp']>=CONFIG['relative_strength_pp']
    early=(f['compression_ratio']<=CONFIG['compression_ratio'] and
           f['volume_1h_multiple']>=CONFIG['wake_volume_multiple'] and
           p>f['box_high'] and strong and f['return_24h_pct']<CONFIG['max_early_gain_pct'])
    continuation=(f['leg_gain_pct']>=CONFIG['major_leg_pct'] and f['retention']>=CONFIG['min_retention']
                  and len(ll)>=2 and ll[-1][1]>ll[-2][1] and strong and p>h[-2][4]
                  and f['peak_ms']<h[-2][0] and p<f['leg_peak'])
    breakdown=(f['leg_gain_pct']>=CONFIG['major_leg_pct'] and lower_high and broken_hour
               and f['relative_strength_4h_pp']<=-CONFIG['relative_strength_pp'])
    if breakdown:
        model='TOP_BREAKDOWN';side='SHORT';trigger=min(r[3] for r in prior_m)
        stop=hh[-1][1]+CONFIG['stop_buffer_atr1h']*a
    elif early:
        model='WAKE_UP';side='LONG';trigger=max(f['box_high'],max(r[2] for r in prior_m))
        stop=f['box_low']-CONFIG['stop_buffer_atr1h']*a
    elif continuation:
        model='MAJOR_CONTINUATION';side='LONG';trigger=max(r[2] for r in prior_m)
        stop=ll[-1][1]-CONFIG['stop_buffer_atr1h']*a
    else:return reject('NO_MAJOR_MOVE_STRUCTURE')
    sign=1 if side=='LONG' else -1
    out.update(model=model,direction=side,trigger=trigger,stop=stop)
    risk=sign*(price-stop)/price*100
    if risk<=0:return reject('STRUCTURE_ALREADY_BROKEN')
    if risk>CONFIG['max_stop_pct']:return reject('STRUCTURAL_STOP_TOO_DISTANT')
    if sign*(price-trigger)>CONFIG['chase_atr15']*a15:return reject('LATE_CHASE')
    # Actual 4h structural obstacles, not small 5m hurdles. Unknown extension
    # is explicitly labelled a research objective, never a guaranteed target.
    levels=pivots(frames['4h'],high=sign==1)
    ahead=[v for _,v in levels if sign*(v-price)>0]
    obstacle=min(ahead) if sign==1 and ahead else max(ahead) if ahead else None
    room=sign*(obstacle/price-1)*100 if obstacle else None
    if room is not None and room<CONFIG['min_room_pct']:return reject('MAJOR_ROOM_BLOCKED')
    geometry=planned_geometry(side,price,trigger,stop,levels)
    out.update(geometry)
    if geometry['planned_risk_pct']>CONFIG['max_stop_pct']:
        return reject('PLANNED_STOP_TOO_DISTANT')
    if geometry['planned_room_pct'] is not None and geometry['planned_room_pct']<CONFIG['min_room_pct']:
        return reject('PLANNED_ROOM_BLOCKED')
    objective=price*(1+sign*CONFIG['min_room_pct']/100)
    horizon=CONFIG['max_plan_hours']
    # Fresh 15m break plus holding the same side. Old breaks cannot be replayed.
    fresh=250<=now_ms-m[-1][6]<=90000
    crossed=sign*(m[-1][4]-trigger)>0 and sign*(m[-2][4]-trigger)<=0
    ready=fresh and crossed and sign*(price-trigger)>=0
    out.update(stage='READY' if ready else 'WATCH',reason='OK',obstacle=obstacle,
               structural_room_pct=room,objective=objective,objective_pct=CONFIG['min_room_pct'],
               objective_label='RESEARCH_MILESTONE_NOT_FORECAST',risk_pct=risk,
               entry_limit=trigger+sign*CONFIG['chase_atr15']*a15,
               horizon_hours=horizon,expires_ms=now_ms+horizon*3600000,
               gain_before_signal_pct=(price/f['leg_base']-1)*100,
               confirmation_ms=m[-1][6],source='BINANCE_UM_FUTURES')
    return out


def rank(candidates):
    """Rank major movement evidence, not distance to the closest micro-level."""
    if not candidates:return []
    for p in candidates:
        strength=abs(p['features']['relative_strength_4h_pp'])
        p['relative_strength_percentile']=100*sum(abs(q['features']['relative_strength_4h_pp'])<=strength for q in candidates)/len(candidates)
    return sorted(candidates,key=lambda p:(p['stage']!='READY',
        -p['relative_strength_percentile'],-p['features']['volume_1h_multiple'],p['symbol']))


def message(p):
    from datetime import datetime,timezone,timedelta
    label={'WAKE_UP':'BÜYÜK HAREKET BAŞLANGICI','MAJOR_CONTINUATION':'BÜYÜK YÜKSELİŞTE DEVAM',
           'TOP_BREAKDOWN':'YÜKSELİŞ SONRASI BOZULMA'}[p['model']]
    f=p['features'];side=p['direction'];emoji='🟢' if side=='LONG' else '🔴'
    fmt=lambda x:f'{x:.8f}'.rstrip('0').rstrip('.')
    stamp=datetime.fromtimestamp(p['observed_ms']/1000,timezone(timedelta(hours=3))).strftime('%d.%m %H:%M')
    boundary='altı' if side=='LONG' else 'üstü'
    from long_short_chart_flow import flow_note
    return '\n'.join([f'{emoji} {side} | {p["symbol"]} — {label}',
        'Durum: Yeni modelin giriş adayı; başarı oranı henüz doğrulanmadı.',
        f'Giriş referansı: {fmt(p["price"])} | Kırılan seviye: {fmt(p["trigger"])}',
        f'Fikir bozulur: {fmt(p["stop"])} {boundary} (mesafe %{p["risk_pct"]:.1f})',
        f'İlk büyük hareket ölçüm eşiği: {fmt(p["objective"])} (%10; fiyat tahmini değildir).',
        '%10 sonrası takip sürer; stop, kapanmış saatlik mum yapısını izler.',
        ('4 saatlik ilk engel: '+fmt(p['obstacle']) if p['obstacle'] else 'İleride doğrulanmış 4 saatlik engel bulunamadı; açık alan garantisi değildir.'),
        f'Neden: 1 saatlik hacim normalin {f["volume_1h_multiple"]:.1f} katı; BTC’ye göre 4 saatte {f["relative_strength_4h_pp"]:+.1f} puan.',
        flow_note(p.get('hourly_flow'),side),
        f'Geçmiş hareket: dipten tepeye %{f["leg_gain_pct"]:.1f}; tepeye uzaklık %{abs(f["drawdown_from_peak_pct"]):.1f}.',
        f'Takip süresi: en çok {p["horizon_hours"]} saat; bozulursa daha erken kapanır.',
        f'Saat: {stamp} Türkiye | MAJOR V1'])


def exit_message(p,g):
    labels={'STOP':'STOP / TAKİP EDEN STOP','TIMEOUT':'TAKİP SÜRESİ DOLDU'}
    exit_=g['plan_exit']
    lines=message(p).splitlines()
    lines[1]='Durum: PLAN KAPANDI — '+labels[exit_['kind']]
    lines += [f'Varsayımsal plan sonucu: %{exit_["net_return_pct"]:+.2f} (toplam %0,30 masraf varsayımı).',
              f'Bildirimden bu güncellemeye kadar en büyük lehte hareket: %{g["max_favorable_pct"]:.1f}.',
              'Gerçek hesap getirisi değildir. Kapanış sonrası hareketler ayrı kaydedilir.']
    return '\n'.join(lines)


def tracking_message(p,g):
    lines=message(p).splitlines()
    lines[1]='Durum: %10 eşiği görüldü; büyük hareket takibi sürüyor.'
    lines += [f'Güncel takip eden stop: {g["active_stop"]:.8g}',
              'Bu seviye yalnızca tamamlanmış saatlik mumlardan sonra değişir.']
    return '\n'.join(lines)
