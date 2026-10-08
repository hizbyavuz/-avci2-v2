#!/usr/bin/env python3
"""Observational V3.2 correction to the V3.1 structure-room false-veto class.

NEVER modifies frozen V3.1 entry/score/gate, stop, TP, Telegram or paper labels.
The V3.1 gate can count the breakout trigger's own overlapping resistance/
support as its "next obstacle", and measures room from scan price rather than
actual breakout entry. This module tests the counterfactual conservatively:
first require price to clear the trigger *zone outer edge*, then count only
distinct support/resistance regions as subsequent obstacles. Re-evaluate
target/cost/R using that more conservative entry.
"""
from __future__ import annotations
import math

VERSION="LS_V3_2_DISTINCT_ROOM_SHADOW_2026_10_08"
BASE_BUFFER=0.0003  # same distance buffer as frozen gate, not optimized
MIN_COST_PCT=0.30

def overlaps(a,b):
    """A candidate obstacle is part of the same barrier if bands intersect."""
    if not a or not b:return False
    return float(a["low"])<=float(b["high"]) and float(b["low"])<=float(a["high"])

def room_shadow(direction,trigger,stop,target,trigger_zone,zones,required_room=0.90,minimum_cost_pct=MIN_COST_PCT,min_net_r=1.0):
    if direction not in ("LONG","SHORT"):
        raise ValueError("direction must be LONG or SHORT")
    trigger=float(trigger or 0)
    stop=float(stop or 0)
    target=float(target or 0)
    if not all(math.isfinite(x) and x>0 for x in (trigger,stop,target)):
        return {"version":VERSION,"valid":False,"reason":"INVALID_LEVELS","research_only":True}
    # No trigger zone = not enough geometry to claim a valid structural breakout.
    if not trigger_zone:
        return {"version":VERSION,"valid":False,"reason":"NO_TRIGGER_ZONE","research_only":True}
    band_low=float(trigger_zone["low"])
    band_high=float(trigger_zone["high"])
    if not all(math.isfinite(x) for x in (band_low,band_high)) or band_low>band_high:
        return {"version":VERSION,"valid":False,"reason":"INVALID_TRIGGER_ZONE","research_only":True}
    if direction=="LONG":
        entry=max(trigger,band_high)
        correct=stop<entry<target
    else:
        entry=min(trigger,band_low)
        correct=target<entry<stop
    if not correct:
        return {"version":VERSION,"valid":False,"reason":"NO_ROOM_AFTER_FULL_ZONE_CLEARANCE",
                "entry_reference":entry,"research_only":True}
    obstacles=[]
    overlapping=0
    for z in zones:
        if not isinstance(z,dict):continue
        if z.get("side")!=("RESISTANCE" if direction=="LONG" else "SUPPORT"):continue
        if overlaps(trigger_zone,z):
            overlapping+=1
            continue
        if direction=="LONG":
            edge=float(z["low"])
            if edge<=entry*(1+BASE_BUFFER):continue
            gap=(edge/entry-1)*100
        else:
            edge=float(z["high"])
            if edge>=entry*(1-BASE_BUFFER):continue
            gap=(entry/edge-1)*100
        if math.isfinite(gap) and gap>0:
            obstacles.append((gap,z))
    obstacles.sort(key=lambda e:e[0])
    barrier_pct,barrier=(obstacles[0] if obstacles else (None,None))
    reward_pct=(target/entry-1)*100 if direction=="LONG" else (entry/target-1)*100
    risk_pct=abs(entry-stop)/entry*100
    reach_pct=min(reward_pct,barrier_pct) if barrier_pct is not None else reward_pct
    net_r=(reward_pct-float(minimum_cost_pct))/risk_pct if risk_pct>0 else None
    room_ok=reach_pct>=float(required_room)
    rr_ok=net_r is not None and net_r>=float(min_net_r)
    return {
        "version":VERSION,"valid":True,"research_only":True,
        "trigger_zone_edge_entry":entry,"entry_clearance_pct":round(abs(entry/trigger-1)*100,6),
        "target_room_pct":round(reward_pct,6),"distinct_barrier_room_pct":None if barrier_pct is None else round(barrier_pct,6),
        "room_pct":round(reach_pct,6),"room_required_pct":float(required_room),
        "room_ok":bool(room_ok),"net_t1_r":round(net_r,6) if net_r is not None else None,
        "rr_ok":bool(rr_ok),"fully_qualified_geometry":bool(room_ok and rr_ok),
        "overlapping_zones_ignored":overlapping,
        "next_distinct_zone":barrier,
        "note":"not executable; need future closed-candle acceptance beyond outer trigger zone + live execution cost check",
    }

def from_analyst_snapshots(direction,trigger,stop,target,trigger_zone,k5,k15,k30,k1h,k4h,price,zone_builder,required_room,minimum_cost_pct,min_net_r):
    zones=[]
    for k,lab,wt in ((k5,"5m",.75),(k15,"15m",1),(k30,"30m",1.35),(k1h,"1h",1.75),(k4h,"4h",2.25)):
        if k is not None:
            zones.extend(zone_builder(k,lab,wt,price))
    return room_shadow(direction,trigger,stop,target,trigger_zone,zones,required_room,minimum_cost_pct,min_net_r)
