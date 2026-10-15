"""Versioned chart-first manual decision support. No scores or order execution.

Rules are declared hypotheses, not calibrated win probabilities. Daily candles
are exact UTC aggregates of six verified, closed Binance Futures 4h candles.
Auxiliary derivatives never select, reverse or veto a chart scenario.
"""
import hashlib
import json
import math
import os
from pathlib import Path

VERSION = 'CHART_FIRST_MULTIFRAME_V1_2026_10_15'
CONFIG = dict(pivot_confirmation_bars=2, daily_min_bars=20,
              approach_atr15=1.0, chase_atr5=1.0, level_tolerance_atr5=0.10,
              candle_grace_ms=20000, quote_max_age_ms=5000,
              min_quote_volume=25000000, round_trip_cost_pct=0.30,
              max_initial_notices_per_cycle=3, cooldown_ms=3600000,
              horizon_minutes=180)
CONFIG_HASH = hashlib.sha256(json.dumps(CONFIG, sort_keys=True).encode()).hexdigest()
STRATEGY_ID = hashlib.sha256((CONFIG_HASH+VERSION).encode()+Path(__file__).read_bytes()+
    Path(__file__).with_name('long_short_chart_first_worker.py').read_bytes()).hexdigest()
PERIODS = {'5m':300000, '15m':900000, '1h':3600000, '4h':14400000, '1d':86400000}


def enabled():
    return os.getenv('LS_CHART_FIRST_V1', '0') == '1'


def daily_from_4h(rows):
    groups = {}
    for r in rows:
        groups.setdefault(int(r[0]) // PERIODS['1d'], []).append(r)
    daily = []
    for day, bars in sorted(groups.items()):
        bars = sorted(bars, key=lambda r:r[0])
        start = day * PERIODS['1d']
        if [int(r[0]) for r in bars] != [start+i*PERIODS['4h'] for i in range(6)]:
            continue
        if any(int(r[6]) != int(r[0])+PERIODS['4h']-1 for r in bars):
            continue
        daily.append([start, float(bars[0][1]), max(float(r[2]) for r in bars),
                      min(float(r[3]) for r in bars), float(bars[-1][4]),
                      sum(float(r[5]) for r in bars), start+PERIODS['1d']-1])
    return daily


def pivots(rows):
    highs, lows = [], []
    for i in range(2, len(rows)-2):
        high, low = float(rows[i][2]), float(rows[i][3])
        if high > max(float(r[2]) for r in rows[i-2:i]) and high >= max(float(r[2]) for r in rows[i+1:i+3]):
            highs.append((int(rows[i][0]), high))
        if low < min(float(r[3]) for r in rows[i-2:i]) and low <= min(float(r[3]) for r in rows[i+1:i+3]):
            lows.append((int(rows[i][0]), low))
    return highs, lows


def describe(rows):
    highs, lows = pivots(rows)
    close = float(rows[-1][4])
    high = highs[-1][1] if highs else None
    low = lows[-1][1] if lows else None
    direction = 'RANGE'
    if high is not None and close > high:
        direction = 'UP'
    elif low is not None and close < low:
        direction = 'DOWN'
    elif len(highs)>=2 and len(lows)>=2:
        if highs[-1][1]>highs[-2][1] and lows[-1][1]>lows[-2][1]:direction='UP'
        elif highs[-1][1]<highs[-2][1] and lows[-1][1]<lows[-2][1]:direction='DOWN'
    elif len(rows)>=20:
        older,recent=rows[-20:-10],rows[-10:]
        if max(r[2] for r in recent)>max(r[2] for r in older) and min(r[3] for r in recent)>min(r[3] for r in older):direction='UP'
        elif max(r[2] for r in recent)<max(r[2] for r in older) and min(r[3] for r in recent)<min(r[3] for r in older):direction='DOWN'
    return dict(direction=direction, high=high, low=low, highs=highs, lows=lows,
                close=close, close_ms=int(rows[-1][6]), bars=len(rows))


def average_range(rows):
    pairs=list(zip(rows[-15:-1],rows[-14:]))
    return sum(max(float(b[2])-float(b[3]), abs(float(b[2])-float(a[4])),
                   abs(float(b[3])-float(a[4]))) for a,b in pairs)/len(pairs)


def volume_context(rows):
    base=sum(float(r[5]) for r in rows[-21:-1])/20
    return round(float(rows[-1][5])/base, 2) if base>0 else None


def supporting_data(evidence):
    e=evidence or {}
    values={}
    for field, fresh in (('oi_change_1h','oi_fresh'),('taker_ratio','taker_fresh'),
                         ('funding_pct','funding_fresh'),('long_short_ratio','ls_fresh')):
        value=e.get(field)
        values[field]=float(value) if (e.get(fresh) and isinstance(value,(int,float))
            and not isinstance(value,bool) and math.isfinite(value)) else None
    return values


def assess(symbol, frames, price, quote_volume, now_ms, evidence=None):
    out=dict(version=VERSION,config_hash=CONFIG_HASH,symbol=symbol,observed_ms=now_ms,
             price=price, direction='NONE', stage='REJECTED', reason=None,
             context={}, support=supporting_data(evidence), warnings=[])
    out['strategy_id']=STRATEGY_ID
    def reject(reason):
        out['reason']=reason
        return out
    if not isinstance(price,(int,float)) or not math.isfinite(price) or price<=0:
        return reject('FRESH_PRICE_UNAVAILABLE')
    if not math.isfinite(float(quote_volume)) or quote_volume<CONFIG['min_quote_volume']:
        return reject('LIQUIDITY_FLOOR')
    frames=dict(frames)
    for tf in ('5m','15m','1h','4h'):
        rows=frames.get(tf) or []
        if len(rows)<30:return reject('CHART_HISTORY_MISSING_'+tf)
        period=PERIODS[tf]
        if not 250<=now_ms-int(rows[-1][6])<=period+CONFIG['candle_grace_ms']:
            return reject('CHART_STALE_'+tf)
        if any(int(b[0])-int(a[0])!=period for a,b in zip(rows,rows[1:])):
            return reject('CHART_GAP_'+tf)
        for r in rows:
            if (int(r[6])!=int(r[0])+period-1 or
                not all(math.isfinite(float(v)) and float(v)>0 for v in r[1:5]) or
                not float(r[3])<=min(float(r[1]),float(r[4]))<=max(float(r[1]),float(r[4]))<=float(r[2])):
                return reject('CHART_INVALID_'+tf)
    frames['1d']=daily_from_4h(frames['4h'])
    daily=frames['1d']
    if len(daily)<CONFIG['daily_min_bars']:return reject('DAILY_CONTEXT_MISSING')
    if now_ms-int(daily[-1][6])>PERIODS['1d']+CONFIG['candle_grace_ms']:
        return reject('DAILY_CONTEXT_STALE')
    context={tf:describe(rows) for tf,rows in frames.items()}
    # Persist compact context, while all raw candles remain in their native DB.
    out['context']={tf:{k:v for k,v in c.items() if k not in ('highs','lows')}
                    for tf,c in context.items()}
    out['daily_source']='UTC_AGGREGATE_OF_CLOSED_BINANCE_FUTURES_4H'
    out['volume_5m_multiple']=volume_context(frames['5m'])
    out['volume_15m_multiple']=volume_context(frames['15m'])
    one=context['1h'];four=context['4h'];local=context['5m'];mid=context['15m']
    if one['direction']=='RANGE':return reject('HOURLY_STRUCTURE_UNCLEAR')
    direction='LONG' if one['direction']=='UP' else 'SHORT'
    sign=1 if direction=='LONG' else -1
    out['direction']=direction
    opposing='DOWN' if sign==1 else 'UP'
    scenario='CONTINUATION'
    if four['direction']==opposing:
        # A small 5m bounce is not a reversal: require a 1h structural break.
        anchor=one['high'] if sign==1 else one['low']
        if anchor is None or sign*(one['close']-anchor)<=0:
            return reject('COUNTERTREND_WITHOUT_HOURLY_BREAK')
        scenario='REVERSAL_ATTEMPT'
        out['warnings'].append('4 saatlik ana harekete karşı dönüş denemesi.')
    if context['1d']['direction']==opposing:
        out['warnings'].append('Günlük yön bu fikre ters; uzun vadeli yön değişimi doğrulanmış değil.')
    out['scenario']=scenario
    trigger=local['high'] if sign==1 else local['low']
    stop=local['low'] if sign==1 else local['high']
    if trigger is None or stop is None:return reject('LOCAL_STRUCTURE_UNCLEAR')
    out.update(trigger=trigger,stop=stop)
    if sign*(trigger-stop)<=0 or sign*(price-stop)<=0:return reject('LOCAL_IDEA_INVALIDATED')
    a5=average_range(frames['5m']);a15=average_range(frames['15m'])
    if a5<=0 or a15<=0:return reject('INVALID_RANGE')
    out.update(atr5=a5,atr15=a15)
    if sign*(price-trigger)>CONFIG['chase_atr5']*a5:return reject('ENTRY_ALREADY_EXTENDED')
    if sign*(trigger-price)>CONFIG['approach_atr15']*a15:return reject('ENTRY_NOT_NEAR')
    # First distinct obstacle from ALL timeframes, anchored to the trigger.
    # Do not silently skip a crossed obstacle and manufacture a farther target.
    levels=[]
    for tf,c in context.items():
        for stamp,level in (c['highs'] if sign==1 else c['lows']):
            # A resistance already closed through is not an untouched future
            # barrier. New confirmed pivots may establish that region again.
            cleared=any(int(r[0])>stamp and sign*(float(r[4])-level)>0 for r in frames[tf])
            if not cleared and sign*(level-trigger)>CONFIG['level_tolerance_atr5']*a5:
                levels.append((sign*(level-trigger),level,tf,stamp))
    levels.sort()
    obstacle=levels[0][1] if levels else None
    out['obstacle']=obstacle
    out['obstacle_timeframe']=levels[0][2] if levels else None
    if obstacle is not None:
        room=sign*(obstacle-max(price,trigger) if sign==1 else obstacle-min(price,trigger))
        # Sign handles shorts; denominator is the actual current/conditional entry.
        entry=max(price,trigger) if sign==1 else min(price,trigger)
        room_pct=room/entry*100
        out['room_pct']=room_pct
        out['reference_reward_risk']=sign*(obstacle-entry)/abs(entry-stop)
        if room_pct<=CONFIG['round_trip_cost_pct']:return reject('OBSTACLE_WITHIN_COST_ALLOWANCE')
        if out['reference_reward_risk']<1:
            out['warnings'].append('İlk engele kalan mesafe, fikrin bozulma mesafesinden kısa.')
    else:
        out['warnings'].append('Geçmiş mumlarda ileride belirgin bir engel bulunamadı; hedef belirsiz.')
    if mid['direction']==opposing:return reject('SHORT_TERM_PULLBACK_NOT_FINISHED')
    last,prev=frames['5m'][-1],frames['5m'][-2]
    if sign*(float(last[4])-float(prev[4]))<=0 and sign*(float(last[4])-trigger)<=0:
        return reject('NO_LOCAL_TURN_YET')
    ready=sign*(float(last[4])-trigger)>0 and sign*(price-trigger)>=0
    # A scan must not call a several-candle-old break a new entry opportunity.
    if ready and all(sign*(float(r[4])-trigger)>0 for r in frames['5m'][-4:]):
        return reject('OLD_BREAKOUT_WAIT_FOR_NEW_STRUCTURE')
    entry_limit=trigger+sign*CONFIG['chase_atr5']*a5
    if obstacle is not None:
        cost_limit=obstacle/(1+sign*CONFIG['round_trip_cost_pct']/100)
        entry_limit=min(entry_limit,cost_limit) if sign==1 else max(entry_limit,cost_limit)
    out.update(stage='READY' if ready else 'WATCH',reason='OK',entry_limit=entry_limit)
    out['setup_key']=hashlib.sha256(json.dumps([STRATEGY_ID,symbol,direction,trigger,stop],
                                              separators=(',',':')).encode()).hexdigest()
    return out


def support_notes(p):
    """Interpret auxiliary observations without pretending to know trader intent."""
    s=p.get('support') or {};side=p['direction'];notes=[]
    flow=s.get('taker_ratio')
    if flow is not None:
        aligned=flow>1 if side=='LONG' else flow<1
        notes.append('Agresif işlem akışı grafik yönünü '+('destekliyor.' if aligned else 'henüz desteklemiyor.'))
    oi=s.get('oi_change_1h')
    if oi is not None:
        notes.append('Açık pozisyon miktarı '+('artıyor; bu tek başına long/short ayrımı yapmaz.' if oi>0 else 'azalıyor; kapanan pozisyonlar hareketi etkiliyor olabilir.'))
    ls=s.get('long_short_ratio');fund=s.get('funding_pct')
    if ls is not None and fund is not None:
        if ls>2 and fund>0:notes.append('LONG hesapları kalabalık ve taşıma oranı pozitif; ters hareket riski dikkate alınmalı.')
        elif ls<.5 and fund<0:notes.append('SHORT hesapları kalabalık ve taşıma oranı negatif; ters hareket riski dikkate alınmalı.')
    return notes


def message(p, stage=None):
    from datetime import datetime, timezone
    from long_short_display import local_time
    stage=stage or p['stage'];side=p['direction'];up=side=='LONG'
    marker='🟢' if up else '🔴';relation='üstünde' if up else 'altında'
    title='GİRİŞ BÖLGESİ' if stage=='READY' else 'İÇİN İZLE'
    if stage in ('BROKEN','EXPIRED','OBSTACLE_REACHED'):
        why={'BROKEN':'Fikrin bozulma seviyesi aşıldı.',
             'EXPIRED':'Bu değerlendirme güncelliğini yitirdi; yeni yapı bekleniyor.',
             'OBSTACLE_REACHED':'İlk destek/direnç bölgesine ulaşıldı; eski giriş fikri tamamlandı.'}[stage]
        return f'{marker} {side} TAKİBİ TAMAMLANDI | {p["symbol"]}\n• {why}\n• Güncel fiyat: {p["price"]:.10g}'
    labels={'UP':'yukarı','DOWN':'aşağı','RANGE':'yatay / karışık'}
    ctx=p['context'];support=p.get('support') or {}
    scenario='Ana hareket yönünde devam' if p['scenario']=='CONTINUATION' else '4 saatlik harekete karşı dönüş denemesi'
    rows=[f'{marker} {side} {title} | {p["symbol"]}',
          f'• Şu an fiyat: {p["price"]:.10g}',
          f'• Günlük: {labels[ctx["1d"]["direction"]]}',
          f'• 4 saat: {labels[ctx["4h"]["direction"]]} | 1 saat: {labels[ctx["1h"]["direction"]]}',
          f'• Fikir: {scenario}.']
    if stage=='READY':
        lo,hi=sorted((p['trigger'],p['entry_limit']))
        rows.extend([f'• Giriş koşulu oluştu: 5 dk mum {p["trigger"]:.10g} {relation} kapandı.',
                     f'• Değerlendirilen giriş alanı: {lo:.10g}–{hi:.10g}'])
    else:
        rows.append(f'• Beklenen: 5 dk mum {p["trigger"]:.10g} {relation} kapanmalı; giriş koşulu henüz yok.')
    rows.append(f'• Fikir {p["stop"]:.10g} {"altında" if up else "üstünde"} bozulur.')
    if p.get('obstacle'):
        rows.append(f'• İlk {"direnç" if up else "destek"}: {p["obstacle"]:.10g}')
    vol=p.get('volume_15m_multiple')
    if vol is not None:rows.append(f'• 15 dk işlem hacmi: yakın ortalamanın {vol:.2f} katı.')
    oi=support.get('oi_change_1h');taker=support.get('taker_ratio')
    if oi is not None:rows.append(f'• OI (açık pozisyon miktarı), son 1 saat: %{oi:+.2f}')
    if taker is not None:rows.append(f'• Agresif alış/satış hacmi oranı: {taker:.2f}×')
    ls=support.get('long_short_ratio');funding=support.get('funding_pct')
    if ls is not None:rows.append(f'• LONG/SHORT hesap sayısı oranı: {ls:.2f}× — tek başına yön göstermez.')
    if funding is not None:rows.append(f'• Funding (pozisyon taşıma oranı): %{funding:+.4f}')
    missing=[name for key,name in (('oi_change_1h','açık pozisyon'),('taker_ratio','alış/satış'),
             ('long_short_ratio','hesap oranı'),('funding_pct','taşıma oranı')) if support.get(key) is None]
    if missing:rows.append('• Destek verisi eksik: '+', '.join(missing)+'.')
    rows.extend('• '+note for note in support_notes(p))
    rows.extend('• Dikkat: '+w for w in p.get('warnings',[]))
    rows.extend(['• İşlem yönetimi sende; otomatik emir yok.',
                 '🕒 '+local_time(datetime.fromtimestamp(p['observed_ms']/1000,timezone.utc).isoformat())+' (Türkiye)'])
    return '\n'.join(rows)
