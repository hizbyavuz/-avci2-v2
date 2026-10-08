#!/usr/bin/env python3
"""Read-only V4 market structure helpers. All lookbacks strictly end before signal."""
from __future__ import annotations
from collections import defaultdict
from datetime import datetime,timezone,timedelta
import statistics

def oi_engine(price_change_pct,oi_change_pct):
    """OI venue is separate; classification is NOT a trade direction."""
    if price_change_pct is None or oi_change_pct is None:
        return "UNKNOWN"
    if price_change_pct>0 and oi_change_pct>0:return "PRICE_UP_OI_UP_NEW_RISK"
    if price_change_pct>0 and oi_change_pct<0:return "PRICE_UP_OI_DOWN_SHORT_COVER"
    if price_change_pct<0 and oi_change_pct>0:return "PRICE_DOWN_OI_UP_NEW_SHORT_RISK"
    if price_change_pct<0 and oi_change_pct<0:return "PRICE_DOWN_OI_DOWN_LONG_COVER"
    return "FLAT_OR_MIXED"

def resample_complete_5m(rows,minutes):
    """Aggregate COMPLETE consecutive native 5m bars to 15m, 1h, 4h.
    Never forward-fill and never use partial higher-timeframe bars."""
    if minutes not in (15,60,240):raise ValueError("unsupported timeframe")
    bucket_ms=minutes*60000
    count=minutes//5
    groups=defaultdict(list)
    for r in rows:
        groups[int(r["open_ms"])//bucket_ms].append(r)
    out=[]
    for key in sorted(groups):
        b=sorted(groups[key],key=lambda x:int(x["open_ms"]))
        start=key*bucket_ms
        if len(b)!=count:continue
        if [int(x["open_ms"]) for x in b]!=[start+j*300000 for j in range(count)]:
            continue
        out.append({"open_ms":start,"close_ms":start+bucket_ms-1,
            "open":float(b[0]["open"]),"high":max(float(x["high"]) for x in b),
            "low":min(float(x["low"]) for x in b),"close":float(b[-1]["close"]),
            "volume":sum(float(x["volume"]) for x in b),
            "source":b[0].get("source") if isinstance(b[0],dict) else b[0]["source"],
            "timeframe_min":minutes,"derived_from":"COMPLETE_NATIVE_5M"})
    return out

def previous_utc_day_week_levels(rows,asof_close_ms):
    """Previous COMPLETED UTC day/week only, require ALL native 5m bars."""
    asof=datetime.fromtimestamp(asof_close_ms/1000,timezone.utc)
    day_start=asof.replace(hour=0,minute=0,second=0,microsecond=0)
    prev_start=day_start-timedelta(days=1)
    week_start=day_start-timedelta(days=day_start.weekday())
    last_week=week_start-timedelta(days=7)
    windows={"previous_day":(prev_start,day_start,288),
             "previous_week":(last_week,week_start,2016)}
    result={}
    for key,(a,b,needed) in windows.items():
        selected=[r for r in rows if int(a.timestamp()*1000)<=int(r["open_ms"])<int(b.timestamp()*1000)]
        selected=sorted(selected,key=lambda r:int(r["open_ms"]))
        if len(selected)!=needed or any(
            int(y["open_ms"])-int(x["open_ms"])!=300000
            for x,y in zip(selected,selected[1:])):
            result[key]={"high":None,"low":None,"complete":False}
            continue
        result[key]={"high":max(float(r["high"]) for r in selected),
                     "low":min(float(r["low"]) for r in selected),"complete":True}
    return result

def swing_zones(rows,atr_abs,touches=2):
    """Historical equal highs/lows from closed 5m bars; no future pivots."""
    if not rows or atr_abs is None or atr_abs<=0:return {"equal_highs":[],"equal_lows":[]}
    tol=0.22*atr_abs
    highs=[float(r["high"]) for r in rows[:-1]]
    lows=[float(r["low"]) for r in rows[:-1]]
    def groups(vals):
        zones=[]
        for x in vals:
            group=next((z for z in zones if abs(z["level"]-x)<=tol),None)
            if group is None:zones.append({"level":x,"touches":1})
            else:
                n=group["touches"]
                group["level"]=(n*group["level"]+x)/(n+1)
                group["touches"]=n+1
        return sorted([z for z in zones if z["touches"]>=touches],
                      key=lambda z:-z["touches"])[:8]
    return {"equal_highs":groups(highs),"equal_lows":groups(lows)}

def funding_percentile(past_settlement_rates,current):
    """Only compare independent past rates, not repeated mark stream ticks."""
    if current is None or len(past_settlement_rates)<20:return None
    return sum(float(x)<=float(current) for x in past_settlement_rates)/len(past_settlement_rates)

def robust_signal_calibration(events,min_events=150):
    """An estimate is permitted only with enough INDEPENDENT finished samples.
    Each event has (episode_id, net_r). Correlated events share an episode.
    No weights or thresholds are optimized here.
    """
    grouped=defaultdict(list)
    for e in events:
        if e.get("net_r") is not None and e.get("episode_id"):
            grouped[str(e["episode_id"])].append(float(e["net_r"]))
    independent=[statistics.mean(xs) for xs in grouped.values()]
    n=len(independent)
    if n<min_events:
        return {"status":"INSUFFICIENT_DATA","independent_episodes":n,
                "required":min_events,"edge_proven":False}
    # Exploratory descriptive stats only, not a significance claim.
    split=int(n*.7)
    early=independent[:split]
    late=independent[split:]
    return {"status":"EXPLORATORY_ONLY","independent_episodes":n,
            "train_mean_r":statistics.mean(early),"holdout_mean_r":statistics.mean(late),
            "holdout_positive_share":sum(x>0 for x in late)/len(late),
            "edge_proven":False}
