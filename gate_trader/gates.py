"""Deterministic Gate Futures LONG/SHORT evaluation; no orders."""
from __future__ import annotations
import math
from bisect import bisect_left
from dataclasses import dataclass, field
from . import config as C
from .series import Series

@dataclass
class Decision:
    ok: bool
    gate: str
    side: str | None = None
    entry: float | None = None
    sl: float | None = None
    tp: float | None = None
    level: float | None = None
    target_level: float | None = None
    rr: float | None = None
    net_rr: float | None = None
    stop_pct: float | None = None
    signal_ts: int | None = None
    reasons: list[str] = field(default_factory=list)

def _ok(x):
    return x is not None and not (isinstance(x,float) and math.isnan(x))

def trend(s:Series, idx:int)->str:
    if idx < C.EMA_SLOW+C.SLOPE_BARS:return "RANGE"
    fast,slow=s.ema(C.EMA_FAST),s.ema(C.EMA_SLOW)
    if not all(_ok(v) for v in (fast[idx],slow[idx],slow[idx-C.SLOPE_BARS])):return "RANGE"
    if s.c[idx]>slow[idx] and fast[idx]>slow[idx]>slow[idx-C.SLOPE_BARS]:return "UP"
    if s.c[idx]<slow[idx] and fast[idx]<slow[idx]<slow[idx-C.SLOPE_BARS]:return "DOWN"
    return "RANGE"

def levels(s1h:Series,j:int)->list[tuple[float,int]]:
    a=s1h.atr()[j]
    if not _ok(a) or a<=0:return []
    piv=s1h.pivots(C.PIVOT_K)
    key=("piv_idx",C.PIVOT_K)
    if key not in s1h._cache:s1h._cache[key]=[p[0] for p in piv]
    pidx=s1h._cache[key]
    start=bisect_left(pidx,j-C.LEVEL_LOOKBACK_1H)
    end=bisect_left(pidx,j-C.PIVOT_K+1)
    raw=[piv[x][1] for x in range(start,end)]
    close_ts=s1h.t[j]+s1h.sec
    day_start=close_ts-close_ts%86400
    a0=bisect_left(s1h.t,day_start-86400)
    a1=bisect_left(s1h.t,day_start)
    if a1-a0>=20:raw.extend((max(s1h.h[a0:a1]),min(s1h.l[a0:a1])))
    if not raw:return []
    raw.sort();tol=C.LEVEL_MERGE_ATR*a
    out=[];group=[raw[0]]
    for p in raw[1:]:
        if p-group[-1]<=tol:group.append(p)
        else:out.append((sum(group)/len(group),len(group)));group=[p]
    out.append((sum(group)/len(group),len(group)))
    return out

def volume_24h(s1h:Series,j:int)->float:
    return sum(s1h.qv[max(0,j-23):j+1])

def evaluate(btc4h:Series,c4h:Series,c1h:Series,c15:Series,i:int,
             vol24:float|None=None,funding:float|None=None,
             cost_pct:float|None=None)->Decision:
    cost=C.ROUND_TRIP_COST_PCT if cost_pct is None else cost_pct
    T=c15.t[i]+c15.sec
    b=btc4h.last_closed_index(T)
    market=trend(btc4h,b) if b>=0 else "RANGE"
    reasons=[f"BTC 4s: {market} (bilgi, engel değil)"]
    k=c4h.last_closed_index(T)
    ct=trend(c4h,k) if k>=0 else "RANGE"
    if ct=="RANGE":return Decision(False,"2-COIN",reasons=reasons+["coin 4s trend yönsüz"])
    side="LONG" if ct=="UP" else "SHORT"
    j=c1h.last_closed_index(T)
    if j<60:return Decision(False,"2-COIN",side,reasons=reasons+["1s veri yetersiz"])
    v24=vol24 if vol24 is not None else volume_24h(c1h,j)
    if v24<C.MIN_QUOTE_VOLUME_24H:
        return Decision(False,"2-COIN",side,reasons=reasons+[f"hacim düşük {v24/1e6:.1f}M"])
    reasons.append(f"coin 4s trend {'yukarı' if side=='LONG' else 'aşağı'}")
    a=c1h.atr()[j];lv=levels(c1h,j)
    if not lv or not _ok(a) or i<25:return Decision(False,"3-SEVIYE",side,reasons=reasons+["seviye yok"])
    o,h,l,c=c15.o[i],c15.h[i],c15.l[i],c15.c[i]
    prev_c=c15.c[i-C.HOLD_BARS:i]
    touch=C.TOUCH_ATR*a
    if side=="LONG":
        candidates=[(p,n) for p,n in lv if l<=p+touch and c>p and sum(x>p for x in prev_c)>=C.HOLD_MIN]
        selected=max(candidates) if candidates else None
    else:
        candidates=[(p,n) for p,n in lv if h>=p-touch and c<p and sum(x<p for x in prev_c)>=C.HOLD_MIN]
        selected=min(candidates) if candidates else None
    if selected is None:return Decision(False,"3-SEVIYE",side,reasons=reasons+["fiyat seviyede değil"])
    L,touches=selected
    reasons.append(f"{'destek' if side=='LONG' else 'direnç'} {fmt(L)} ({touches} test)")
    rng=h-l
    if rng<=0:return Decision(False,"4-TEYIT",side,reasons=reasons+["mum yok"])
    pos=(c-l)/rng if side=="LONG" else (h-c)/rng
    body_ok=c>o if side=="LONG" else c<o
    avg=sum(c15.qv[i-20:i])/20
    vmult=c15.qv[i]/avg if avg>0 else 0
    if not body_ok or pos<C.MIN_CLOSE_POSITION:
        return Decision(False,"4-TEYIT",side,level=L,reasons=reasons+["ret mumu zayıf"])
    if vmult<C.MIN_VOLUME_MULT:
        return Decision(False,"4-TEYIT",side,level=L,reasons=reasons+[f"hacim zayıf ({vmult:.1f}x)"])
    if funding is not None:
        if side=="LONG" and funding>C.MAX_FUNDING_WITH:
            return Decision(False,"4-TEYIT",side,level=L,reasons=reasons+["funding aşırı pozitif"])
        if side=="SHORT" and funding< -C.MAX_FUNDING_WITH:
            return Decision(False,"4-TEYIT",side,level=L,reasons=reasons+["funding aşırı negatif"])
    reasons.append(f"15dk ret mumu, hacim {vmult:.1f}x")
    entry=c
    if side=="LONG":
        sl=min(l,L)-C.STOP_BUFFER_ATR*a
        risk=entry-sl
        opposite=sorted(p for p,_ in lv if p>entry+0.1*a)
    else:
        sl=max(h,L)+C.STOP_BUFFER_ATR*a
        risk=sl-entry
        opposite=sorted((p for p,_ in lv if p<entry-0.1*a),reverse=True)
    stop_pct=risk/entry*100
    if not C.MIN_STOP_PCT<=stop_pct<=C.MAX_STOP_PCT:
        return Decision(False,"5-RISK",side,level=L,reasons=reasons+[f"stop uygunsuz %{stop_pct:.2f}"])
    target=opposite[0] if opposite else None
    if target is None:reward=C.DEFAULT_RR*risk
    else:
        room=abs(target-entry)-C.TARGET_BUFFER_ATR*a
        if room<C.MIN_RR*risk:
            return Decision(False,"5-RISK",side,level=L,target_level=target,reasons=reasons+["hedefe yer yok"])
        reward=min(room,C.MAX_RR*risk)
    tp=entry+reward if side=="LONG" else entry-reward
    rr=reward/risk
    net_rr=(reward/entry*100-cost)/(stop_pct+cost)
    if net_rr<C.MIN_NET_RR:
        return Decision(False,"5-RISK",side,level=L,reasons=reasons+["maliyet sonrası getiri yetersiz"])
    reasons.append(f"hedef {rr:.1f}R")
    return Decision(True,"TAMAM",side,entry,sl,tp,L,target,rr,net_rr,stop_pct,T,reasons)

def fmt(p:float|None)->str:
    if p is None:return "-"
    if p>=1000:return f"{p:,.1f}"
    if p>=1:return f"{p:.4g}" if p<10 else f"{p:.2f}"
    return f"{p:.5g}"
