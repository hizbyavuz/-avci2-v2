"""Causal chart annotations, never votes for entry or changes to risk levels.

SMC terms use the explicit mechanical definitions below; they do not establish
institutional activity, resting orders, or predictive reliability.
"""
from .core import significant_swings, describe, atr_series

RULES = {
    'structure': 'First close across a previously confirmed swing: BOS with prior structure, CHoCH against it, BREAK if structure is unclear.',
    'sweep': 'Wick beyond a previously confirmed swing followed by the same candle closing back inside. Price proxy only, no order-book proof.',
    'fvg': 'Three-candle wick gap: third low above first high (bull), or third high below first low (bear). Visible only while untouched by a later wick.',
    'ob': 'Candidate only: last opposite-colour candle within five bars before a structural break whose body exceeds one preceding ATR. Whole candle range; removed after adverse close through far edge.',
    'indicators': 'EMA uses first close seed; Wilder RSI14 uses initial 14-change mean; MACD12/26/9 uses EMA seeds. Computed over available closed history.',
    'scope': 'Annotations do not modify direction, entry, stop, target or eligibility. No wedge, triangle, flag, head-and-shoulders, harmonics or Elliott detector.',
}


def ema(values, n):
    out=[]
    for v in values:
        out.append(v if not out else out[-1]+2/(n+1)*(v-out[-1]))
    return out


def rsi(values, n=14):
    out=[None]*len(values)
    if len(values)<=n:return out
    deltas=[b-a for a,b in zip(values,values[1:])]
    gain=sum(max(d,0) for d in deltas[:n])/n
    loss=sum(max(-d,0) for d in deltas[:n])/n
    def value():return 50. if gain==loss==0 else 100. if loss==0 else 100-100/(1+gain/loss)
    out[n]=value()
    for i in range(n+1,len(values)):
        gain=(gain*(n-1)+max(deltas[i-1],0))/n
        loss=(loss*(n-1)+max(-deltas[i-1],0))/n
        out[i]=value()
    return out


def indicators(rows):
    closes=[b['close'] for b in rows]
    fast,slow=ema(closes,12),ema(closes,26)
    macd=[a-b for a,b in zip(fast,slow)];signal=ema(macd,9)
    return dict(open_ms=[b['open_ms'] for b in rows],ema20=ema(closes,20),ema50=ema(closes,50),
                rsi14=rsi(closes),macd=macd,signal=signal,histogram=[a-b for a,b in zip(macd,signal)],
                quote_volume=[b['quote_volume'] for b in rows])


def analyze(rows):
    events=[];blocks=[];gaps=[];seen=set()
    atr=atr_series(rows)
    for i in range(2,len(rows)):
        b,a=rows[i],rows[i-2]
        for side,low,high in [('LONG',a['high'],b['low']),('SHORT',b['high'],a['low'])]:
            if high>low:
                # These zones existed at candle i's close, not at candle i-2.
                gaps.append(dict(kind='FVG',side=side,low=low,high=high,open_ms=a['open_ms'],known_ms=b['close_ms']))
    # Bound chart event discovery to the latest 60 closed candles.
    for i in range(max(3,len(rows)-60),len(rows)):
        past=rows[:i];b=rows[i];previous=rows[i-1]
        swings=significant_swings(past)
        direction=describe(past)['direction']
        for side,kind,sign in [('LONG','H',1),('SHORT','L',-1)]:
            eligible=[p for p in swings if p['kind']==kind]
            if not eligible:continue
            p=eligible[-1];level=p['price'];key=(side,p['open_ms'])
            broken_before=any(sign*(x['close']-level)>0 for x in past if x['open_ms']>p['known_ms'])
            crossed=sign*(b['close']-level)>0 and sign*(previous['close']-level)<=0
            wick=b['high'] if sign==1 else b['low']
            if not broken_before and sign*(wick-level)>0 and sign*(b['close']-level)<=0:
                events.append(dict(kind='SWEEP',side=side,level=level,anchor_ms=p['open_ms'],at_ms=b['open_ms'],known_ms=b['close_ms']))
            if not crossed or broken_before or key in seen:continue
            seen.add(key)
            aligned='UP' if sign==1 else 'DOWN';opposite='DOWN' if sign==1 else 'UP'
            label='BOS' if direction==aligned else 'CHoCH' if direction==opposite else 'BREAK'
            events.append(dict(kind=label,side=side,level=level,anchor_ms=p['open_ms'],at_ms=b['open_ms'],known_ms=b['close_ms']))
            if sign*(b['close']-b['open'])>atr[i-1]:
                opposite_bars=[x for x in rows[max(0,i-5):i] if sign*(x['close']-x['open'])<0]
                if opposite_bars:
                    anchor=opposite_bars[-1]
                    blocks.append(dict(kind='OB_ADAY',side=side,low=anchor['low'],high=anchor['high'],open_ms=anchor['open_ms'],known_ms=b['close_ms']))
    def untouched(z):
        return not any(b['low']<=z['high'] and b['high']>=z['low'] for b in rows if b['open_ms']>z['known_ms'])
    def not_invalid(z):
        return not any((b['close']<z['low'] if z['side']=='LONG' else b['close']>z['high']) for b in rows if b['open_ms']>z['known_ms'])
    active_gaps=[z for z in gaps if untouched(z)]
    active_blocks=list({(z['side'],z['open_ms']):z for z in blocks if not_invalid(z)}.values())
    return dict(rules=RULES,indicators=indicators(rows),events=events,zones=active_gaps+active_blocks,
                counts=dict(structure=sum(e['kind']!='SWEEP' for e in events),sweeps=sum(e['kind']=='SWEEP' for e in events),
                            active_fvg=len(active_gaps),ob_candidates=len(active_blocks)),decision_effect='NONE')
