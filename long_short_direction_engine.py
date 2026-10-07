#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Production LONG/SHORT direction evidence engine.

Purpose:
- decide market bias from the combined evidence already computed by the analyst;
- do NOT turn every soft feature into a veto;
- keep only data/tradability safety as hard blockers;
- leave entry timing to the live confirmation layer.

This module never places orders.
"""
from __future__ import annotations

from typing import Any

DIRECTION_ENGINE_VERSION = "LS_DIRECTION_EVIDENCE_V1_2026-10-07"
MIN_DIRECTION_SCORE = 42.0
MIN_DIRECTION_EDGE = 12.0
MAX_SPOT_FLOW_BONUS = 6.0
MAX_RESIDUAL_BONUS = 4.0


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, float(x)))


def decide_direction(
    *,
    long_score: float,
    short_score: float,
    deriv_ready: bool,
    actionable_liquidity_ok: bool,
    external_only_unverified: bool,
    spot_flow: dict[str, Any] | None = None,
    residual: dict[str, Any] | None = None,
    phase: str = "NONE",
) -> dict[str, Any]:
    """Combine directional evidence; reserve hard vetoes for safety only.

    The analyst's long/short scores already aggregate trend, structure,
    momentum, volume, OI, funding, taker flow, order-book context and BTC
    regime. Spot delta and BTC-beta residual are added here as *soft* evidence.

    A disagreeing soft feature can reduce the edge, but cannot erase seven
    agreeing features by itself.
    """
    l=float(long_score or 0.0)
    s=float(short_score or 0.0)
    evidence=[]

    sf=spot_flow or {}
    if bool(sf.get("available")) and sf.get("delta_share") is not None:
        delta=float(sf.get("delta_share") or 0.0)
        bonus=min(MAX_SPOT_FLOW_BONUS, abs(delta)*10.0)
        if delta>0.02:
            l+=bonus
            evidence.append({"name":"spot_flow","side":"LONG","strength":bonus,"value":delta})
        elif delta<-0.02:
            s+=bonus
            evidence.append({"name":"spot_flow","side":"SHORT","strength":bonus,"value":delta})
        else:
            evidence.append({"name":"spot_flow","side":"NEUTRAL","strength":0.0,"value":delta})

    rr=residual or {}
    residual3=float(rr.get("residual_3h_pct") or 0.0)
    if residual3:
        bonus=min(MAX_RESIDUAL_BONUS, abs(residual3)*4.0)
        if residual3>0:
            l+=bonus
            evidence.append({"name":"btc_residual","side":"LONG","strength":bonus,"value":residual3})
        else:
            s+=bonus
            evidence.append({"name":"btc_residual","side":"SHORT","strength":bonus,"value":residual3})

    l=_clamp(l)
    s=_clamp(s)
    edge=abs(l-s)
    best=max(l,s)

    direction="NONE"
    if best>=MIN_DIRECTION_SCORE and edge>=MIN_DIRECTION_EDGE:
        direction="LONG" if l>s else "SHORT"

    hard_blockers=[]
    if not bool(deriv_ready):
        hard_blockers.append("derivatives_incomplete")
    if not bool(actionable_liquidity_ok):
        hard_blockers.append("liquidity_floor")
    if bool(external_only_unverified):
        hard_blockers.append("unverified_external_only")

    eligible=bool(direction!="NONE" and not hard_blockers)
    setup_type="PULLBACK" if str(phase)=="IMPULSE" else "BREAKOUT"

    return {
        "version":DIRECTION_ENGINE_VERSION,
        "eligible":eligible,
        "direction":direction,
        "setup_type":setup_type,
        "phase":str(phase or "NONE"),
        "long_score":round(l,3),
        "short_score":round(s,3),
        "edge":round(edge,3),
        "best_score":round(best,3),
        "thresholds":{
            "min_direction_score":MIN_DIRECTION_SCORE,
            "min_direction_edge":MIN_DIRECTION_EDGE,
        },
        "hard_blockers":hard_blockers,
        "veto_reasons":hard_blockers,
        "evidence":evidence,
    }
