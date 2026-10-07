#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Long/Short V3 pure feature and hard-gate helpers.

No additive LONG/SHORT score in this module. Numeric values are used only for
ranking discovery candidates or measuring gates. Final eligibility is boolean.
"""
from __future__ import annotations

import math
import statistics
from typing import Any

V3_VERSION = "LS_V3_0_STATE_MACHINE_FREEZE_2026-10-07"


def _mean(xs):
    return statistics.fmean(xs) if xs else 0.0


def _pct(a, b):
    return 0.0 if not a else (b / a - 1.0) * 100.0


def _ema(xs, period):
    if not xs:
        return 0.0
    alpha = 2.0 / (period + 1.0)
    value = float(xs[0])
    for x in xs[1:]:
        value = alpha * float(x) + (1.0 - alpha) * value
    return value


def _true_ranges(rows):
    vals=[]
    for prev,cur in zip(rows[:-1],rows[1:]):
        ph=float(cur[2]); pl=float(cur[3]); pc=float(prev[4])
        vals.append(max(ph-pl,abs(ph-pc),abs(pl-pc)))
    return vals


def rolling_atr_values(rows, period=14):
    tr=_true_ranges(rows)
    if len(tr)<period:
        return []
    return [_mean(tr[i-period+1:i+1]) for i in range(period-1,len(tr))]


def percentile_rank(values, value):
    vals=sorted(float(x) for x in values if x is not None and math.isfinite(float(x)))
    if not vals:
        return 0.5
    return sum(1 for x in vals if x <= float(value)) / float(len(vals))


def bb_width_series(closes, period=20):
    closes=[float(x) for x in closes]
    out=[]
    for i in range(period-1,len(closes)):
        w=closes[i-period+1:i+1]
        m=_mean(w)
        sd=statistics.pstdev(w) if len(w)>1 else 0.0
        out.append((4.0*sd/m) if m else 0.0)
    return out


def compression_features(k15: dict[str,Any], t15: dict[str,Any]) -> dict[str,float]:
    atrs=rolling_atr_values(k15["raw"],14)
    current_atr=float(t15.get("atr") or 0.0)
    hist=atrs[-50:-1] if len(atrs)>1 else []
    med=statistics.median(hist) if hist else current_atr
    atr_ratio=(current_atr/med) if med>0 else 1.0

    widths=bb_width_series(k15["close"],20)
    current_width=widths[-1] if widths else 0.0
    width_hist=widths[-50:-1] if len(widths)>1 else widths
    bb_pct=percentile_rank(width_hist,current_width) if width_hist else 0.5

    atr_pct=max(float(t15.get("atr_pct") or 0.0),1e-6)
    move_1h=abs(float(t15.get("change_4") or 0.0))
    extension_atr=move_1h/atr_pct
    return {
        "atr_ratio":atr_ratio,
        "bb_width":current_width,
        "bb_width_percentile":bb_pct,
        "pre_signal_extension_atr":extension_atr,
    }


def beta_residual_3h(coin_1h: dict[str,Any], btc_1h: dict[str,Any]) -> dict[str,float]:
    cc=[float(x) for x in coin_1h.get("close",[])]
    bc=[float(x) for x in btc_1h.get("close",[])]
    n=min(len(cc),len(bc),72)
    if n<12:
        return {"beta":1.0,"residual_3h_pct":0.0,"coin_3h_pct":0.0,"btc_3h_pct":0.0}
    cc=cc[-n:]; bc=bc[-n:]
    cr=[cc[i]/cc[i-1]-1.0 for i in range(1,n)]
    br=[bc[i]/bc[i-1]-1.0 for i in range(1,n)]
    mb=_mean(br); mc=_mean(cr)
    var=_mean([(x-mb)**2 for x in br])
    cov=_mean([(x-mb)*(y-mc) for x,y in zip(br,cr)])
    beta=(cov/var) if var>1e-12 else 1.0
    coin3=_pct(cc[-4],cc[-1]) if len(cc)>=4 else 0.0
    btc3=_pct(bc[-4],bc[-1]) if len(bc)>=4 else 0.0
    return {
        "beta":beta,
        "residual_3h_pct":coin3-beta*btc3,
        "coin_3h_pct":coin3,
        "btc_3h_pct":btc3,
    }


def classify_phase(k15: dict[str,Any], t15: dict[str,Any]) -> tuple[str,dict[str,float]]:
    comp=compression_features(k15,t15)
    if comp["pre_signal_extension_atr"] >= 2.50:
        return "EXTENDED",comp
    if comp["atr_ratio"] <= 0.90 and comp["bb_width_percentile"] <= 0.35:
        return "BALANCE",comp
    return "IMPULSE",comp


def discovery_rank(k5, k15, t5, t15, day_change_pct, levels):
    """Rank *pre-move readiness*, not absolute mover magnitude.

    This rank only chooses which symbols deserve expensive deep analysis. It is
    never used to authorize a LONG/SHORT signal.
    """
    phase,comp=classify_phase(k15,t15)
    price=float(t5["price"])
    a=max(float(t15["atr"]),price*0.002)
    dres=max(0.0,(float(levels["resistance"])-price)/a)
    dsup=max(0.0,(price-float(levels["support"]))/a)
    dlevel=min(dres,dsup)
    compression=max(0.0,1.0-min(comp["atr_ratio"],1.5)/1.5)
    bb=max(0.0,1.0-min(comp["bb_width_percentile"],1.0))
    proximity=max(0.0,1.0-min(dlevel,2.5)/2.5)
    # Prefer volume waking up, not already in a climax spike.
    vm=float(t15.get("vol_mult") or 1.0)
    reaccel=max(0.0,1.0-abs(vm-1.15)/1.15) if vm<=2.2 else 0.0
    extension_penalty=max(0.0,comp["pre_signal_extension_atr"]-1.5)
    day_atr=abs(float(day_change_pct))/max(float(t15.get("atr_pct") or 0.01),0.01)
    day_penalty=max(0.0,day_atr-8.0)*0.10
    rank=4.0*compression+3.0*bb+3.0*proximity+1.5*reaccel-2.0*extension_penalty-day_penalty
    return rank,{
        "phase":phase,
        **comp,
        "distance_key_level_atr":dlevel,
        "volume_reacceleration":reaccel,
        "day_extension_atr_proxy":day_atr,
    }


def decide_setup(
    *,
    symbol: str,
    k5: dict[str,Any],
    k15: dict[str,Any],
    k1h: dict[str,Any],
    t5: dict[str,Any],
    t15: dict[str,Any],
    t1h: dict[str,Any],
    levels: dict[str,float],
    day_change_pct: float,
    deriv_ready: bool,
    oi_change_1h: float,
    funding_pct: float,
    taker_ratio: float,
    long_short_ratio: float,
    spot_flow: dict[str,Any],
    residual: dict[str,float],
) -> dict[str,Any]:
    phase,comp=classify_phase(k15,t15)
    price=float(t5["price"])
    a=max(float(t15["atr"]),price*0.002)
    dist_res=(float(levels["resistance"])-price)/a
    dist_sup=(price-float(levels["support"]))/a
    residual3=float(residual.get("residual_3h_pct") or 0.0)

    if symbol=="BTCUSDT":
        residual_long=residual_short=True
    else:
        residual_long=residual3>=0.0
        residual_short=residual3<=0.0

    long_trend=bool(float(t1h["price"])>float(t1h["ema20"])>float(t1h["ema50"]))
    short_trend=bool(float(t1h["price"])<float(t1h["ema20"])<float(t1h["ema50"]))
    ema_dist=abs(price-float(t5["ema20"]))/max(float(t5["atr"]),price*0.001)

    breakout_long=bool(
        phase=="BALANCE" and 0.0<=dist_res<=1.0
        and int(t15.get("structure") or 0)>=0 and residual_long
    )
    breakout_short=bool(
        phase=="BALANCE" and 0.0<=dist_sup<=1.0
        and int(t15.get("structure") or 0)<=0 and residual_short
    )
    pullback_long=bool(
        phase=="IMPULSE" and long_trend and int(t15.get("structure") or 0)>=0
        and ema_dist<=0.80 and residual_long
    )
    pullback_short=bool(
        phase=="IMPULSE" and short_trend and int(t15.get("structure") or 0)<=0
        and ema_dist<=0.80 and residual_short
    )

    candidates=[]
    if breakout_long: candidates.append(("BREAKOUT","LONG"))
    if breakout_short: candidates.append(("BREAKOUT","SHORT"))
    if pullback_long: candidates.append(("PULLBACK","LONG"))
    if pullback_short: candidates.append(("PULLBACK","SHORT"))

    if not candidates:
        return {
            "version":V3_VERSION,"eligible":False,"setup_type":"NONE","direction":"NONE",
            "phase":phase,"compression":comp,"residual":residual,
            "gates":{"setup":False},"veto_reasons":["no_v3_setup"],
        }

    # If both sides are close in a balance, residual alpha chooses the side.
    if len(candidates)>1:
        if residual3>0:
            candidates=[x for x in candidates if x[1]=="LONG"] or candidates
        elif residual3<0:
            candidates=[x for x in candidates if x[1]=="SHORT"] or candidates
    setup_type,direction=candidates[0]

    spot_available=bool(spot_flow.get("available"))
    spot_delta=float(spot_flow.get("delta_share") or 0.0)
    spot_ok=spot_available and ((direction=="LONG" and spot_delta>0.0) or (direction=="SHORT" and spot_delta<0.0))

    oic=float(oi_change_1h or 0.0)
    p1h=float(t1h.get("change_1") or 0.0)
    liquidation_like=bool(
        (direction=="LONG" and p1h>0 and oic<-1.5)
        or (direction=="SHORT" and p1h<0 and oic<-1.5)
    )
    funding_extreme=bool(
        (direction=="LONG" and float(funding_pct or 0.0)>=0.10)
        or (direction=="SHORT" and float(funding_pct or 0.0)<=-0.10)
    )
    crowd_extreme=bool(
        (direction=="LONG" and float(long_short_ratio or 1.0)>=2.5)
        or (direction=="SHORT" and float(long_short_ratio or 1.0)<=0.40)
    )
    taker_not_opposite=bool(
        (direction=="LONG" and float(taker_ratio or 1.0)>=0.90)
        or (direction=="SHORT" and float(taker_ratio or 1.0)<=1.10)
    )
    extension_ok=comp["pre_signal_extension_atr"]<=2.50

    gates={
        "setup":True,
        "derivatives_ready":bool(deriv_ready),
        "spot_flow":spot_ok,
        "oi_not_liquidation_or_cover":not liquidation_like,
        "funding_not_extreme":not funding_extreme,
        "crowding_not_extreme":not crowd_extreme,
        "taker_not_strongly_opposite":taker_not_opposite,
        "anti_chase":extension_ok,
        "residual_direction":residual_long if direction=="LONG" else residual_short,
    }
    veto=[k for k,v in gates.items() if not v]
    return {
        "version":V3_VERSION,
        "eligible":not veto,
        "setup_type":setup_type,
        "direction":direction,
        "phase":phase,
        "compression":comp,
        "residual":residual,
        "spot_flow":spot_flow,
        "flow":{
            "oi_change_1h":oic,
            "funding_pct":float(funding_pct or 0.0),
            "taker_ratio":float(taker_ratio or 1.0),
            "long_short_ratio":float(long_short_ratio or 1.0),
        },
        "gates":gates,
        "veto_reasons":veto,
        "distance_resistance_atr":dist_res,
        "distance_support_atr":dist_sup,
        "ema20_distance_atr":ema_dist,
    }
