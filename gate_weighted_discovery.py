#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gate weighted discovery observer.

Observation-only yellow lane. It never changes frozen V5 candidate membership,
rulesets, controls, labels, thresholds, or validation statistics.

Design:
- market evidence is weighted instead of all-or-nothing;
- security/sellability remains fail-closed through the existing security gate;
- only the best few soft-score rows are enriched to keep API load bounded;
- every reviewed row is persisted for later outcome research.
"""
from __future__ import annotations

import json
import sqlite3
import time

from gate_early_observer import contract_key
from gate_notify import security_decision

VERSION = "gate-weighted-discovery-v1-20260926"
MAX_SECURITY_REVIEWS = 8
MAX_SAFE_OUTPUT = 5

def _f(v, default=0.0):
    try:
        return float(v if v is not None else default)
    except (TypeError, ValueError):
        return default

def _bool_points(ok, pts):
    return pts if ok else 0.0

def _latest_own_ratio(con, batch, network, contract):
    row=con.execute("""SELECT own_volume_ratio FROM gate_early_observations
        WHERE batch_id=? AND network_id=? AND token_contract=? LIMIT 1""",
        (batch,network,contract_key(network,contract))).fetchone()
    return None if not row else row[0]

def score_item(item, own_ratio=None):
    """Transparent 100-point soft evidence score; not a probability."""
    liq=_f(item.get("liquidity"))
    v24=_f(item.get("volume_24h"))
    v1=_f(item.get("volume_1h"))
    v5=_f(item.get("volume_5m"))
    c24=_f(item.get("change_24h"))
    c1=_f(item.get("change_1h"))
    c5=_f(item.get("change_5m"))
    b5=_f(item.get("buys_5m")); s5=_f(item.get("sells_5m"))
    b1=_f(item.get("buys_1h")); s1=_f(item.get("sells_1h"))
    age=item.get("age_minutes")
    age=_f(age,-1) if age is not None else -1

    score=0.0
    evidence=[]
    missing=[]

    # Own-history anomaly: strongest early evidence, but absence is not a veto.
    if own_ratio is None:
        missing.append("own_history")
    else:
        r=_f(own_ratio)
        if r>=3.0:
            score+=22; evidence.append(f"hacim kendi geçmişinin {r:.1f} katı")
        elif r>=2.0:
            score+=16; evidence.append(f"hacim kendi geçmişinin {r:.1f} katı")
        elif r>=1.5:
            score+=9; evidence.append(f"hacim kendi geçmişinin {r:.1f} katı")

    # Buyer pressure/breadth proxy.
    if b5+s5>0:
        ratio=b5/max(s5,1.0)
        if b5+s5>=8 and ratio>=1.6:
            score+=16; evidence.append(f"5dk alıcı/satıcı oranı {ratio:.1f}x")
        elif b5+s5>=5 and ratio>=1.3:
            score+=10; evidence.append(f"5dk alıcı akışı {ratio:.1f}x")
        elif ratio>=1.1:
            score+=4
    else:
        missing.append("5m_flow")

    if b1+s1>=20 and b1>=1.2*max(s1,1):
        score+=7; evidence.append("1s alıcı akışı destekliyor")

    # Early price zone: reward movement, penalize being already extended.
    if -2<=c24<=15:
        score+=13; evidence.append(f"24s hareket erken bölgede %{c24:+.1f}")
    elif 15<c24<=25:
        score+=7; evidence.append(f"24s hareket %{c24:+.1f}")
    elif 25<c24<=40:
        score+=2
    elif c24>40:
        score-=12; evidence.append("24s hareket çok uzamış")

    if -1<=c1<=6:
        score+=8
    elif 6<c1<=12:
        score+=4
    elif c1>15:
        score-=5

    if -1<=c5<=3:
        score+=6
    elif 3<c5<=7:
        score+=3
    elif c5>10:
        score-=5

    # Tradability/liquidity is soft here; true sellability remains hard security.
    if liq>=100000:
        score+=10; evidence.append("likidite güçlü")
    elif liq>=30000:
        score+=7
    elif liq>=15000:
        score+=3
    else:
        score-=10

    if v24>=300000:
        score+=8
    elif v24>=100000:
        score+=5
    elif v24>=30000:
        score+=2

    if liq>0 and v24/liq>=1.0:
        score+=4
    if v1>=1000:
        score+=3
    if v5>=100:
        score+=2

    # Age is evidence, not a gate. Very new pools get a caution penalty.
    if age>=24*60:
        score+=3
    elif 0<=age<60:
        score-=6; evidence.append("çok yeni havuz")

    # Existing risk-shape outputs, if present, are counter-evidence.
    if (item.get("climax") or {}).get("risk"):
        score-=12; evidence.append("climax riski")
    if (item.get("trap_proxy") or {}).get("risk"):
        score-=12; evidence.append("trap riski")

    score=max(0.0,min(100.0,score))
    return round(score,1), evidence, missing

def _init(con):
    con.execute("""CREATE TABLE IF NOT EXISTS gate_weighted_discovery (
        batch_id TEXT NOT NULL,
        scan_ts INTEGER NOT NULL,
        network_id TEXT NOT NULL,
        token_contract TEXT NOT NULL,
        pool TEXT,
        symbol TEXT,
        price REAL,
        score REAL NOT NULL,
        status TEXT NOT NULL,
        security_reason TEXT NOT NULL,
        evidence_json TEXT NOT NULL,
        missing_json TEXT NOT NULL,
        own_volume_ratio REAL,
        change_24h REAL,
        liquidity REAL,
        volume_24h REAL,
        version TEXT NOT NULL,
        PRIMARY KEY(batch_id,network_id,token_contract,version)
    )""")
    con.execute("""CREATE INDEX IF NOT EXISTS idx_gate_weighted_discovery_batch
        ON gate_weighted_discovery(batch_id,status,score DESC)""")

def review(db_path, batch, observations, enrich, risk_shapes, now=None):
    now=int(now or time.time())
    # Deduplicate to the deepest-liquidity pool for each exact contract.
    by_key={}
    for raw in observations:
        if not raw.get("network_id") or not raw.get("token_contract"):
            continue
        key=(raw["network_id"],contract_key(raw["network_id"],raw["token_contract"]))
        if key not in by_key or _f(raw.get("liquidity"))>_f(by_key[key].get("liquidity")):
            by_key[key]=raw

    with sqlite3.connect(db_path,timeout=30) as con:
        _init(con)
        health=con.execute("""SELECT scan_ts,status FROM gate_scan_health
            WHERE batch_id=?""",(batch,)).fetchone()
        if not health or health[1]!="VALID" or now-int(health[0])>20*60:
            con.commit()
            return {"scored":0,"reviewed":0,"safe":0,"blocked":0}

        ranked=[]
        for (network,contract),raw in by_key.items():
            own=_latest_own_ratio(con,batch,network,contract)
            item=dict(raw)
            score,evidence,missing=score_item(item,own)
            # Minimal junk floor only; no frozen signal rules used.
            if _f(item.get("price_usd"))<=0 or _f(item.get("liquidity"))<10000 or _f(item.get("volume_24h"))<15000:
                continue
            ranked.append((score,network,contract,own,evidence,missing,item))
        ranked.sort(key=lambda x:(x[0],_f(x[6].get("liquidity"))),reverse=True)

        reviewed=0; safe=0; blocked=0
        climax_fn,trap_fn=risk_shapes
        for score,network,contract,own,evidence,missing,item in ranked[:MAX_SECURITY_REVIEWS]:
            item=dict(item)
            item["volume_liquidity_ratio"]=_f(item.get("volume_24h"))/max(_f(item.get("liquidity")),1)
            item["buy_sell_ratio_24h"]=_f(item.get("buys_24h"))/max(_f(item.get("sells_24h")),1)
            item["tx_count_5m"]=_f(item.get("buys_5m"))+_f(item.get("sells_5m"))
            item["climax"]=climax_fn(item)
            item["trap_proxy"]=trap_fn(item)
            reason=""
            try:
                enrich(item)
                reason=security_decision(item) or ""
            except (ValueError,TypeError,KeyError,OverflowError,RuntimeError) as exc:
                reason="Güvenlik doğrulaması tamamlanamadı"
            reviewed+=1
            status="SAFE_DISCOVERY" if not reason else "BLOCKED"
            if status=="SAFE_DISCOVERY":
                safe+=1
            else:
                blocked+=1
            con.execute("""INSERT OR REPLACE INTO gate_weighted_discovery
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(
                batch,int(health[0]),network,contract,item.get("pool"),
                item.get("symbol") or item.get("name"),_f(item.get("price_usd")),
                score,status,reason,json.dumps(evidence,ensure_ascii=False),
                json.dumps(missing,ensure_ascii=False),own,_f(item.get("change_24h")),
                _f(item.get("liquidity")),_f(item.get("volume_24h")),VERSION))
        con.commit()
        return {"scored":len(ranked),"reviewed":reviewed,"safe":safe,"blocked":blocked}

def top_safe(db_path,batch,limit=MAX_SAFE_OUTPUT):
    with sqlite3.connect(db_path,timeout=30) as con:
        con.row_factory=sqlite3.Row
        try:
            return con.execute("""SELECT * FROM gate_weighted_discovery
                WHERE batch_id=? AND status='SAFE_DISCOVERY'
                ORDER BY score DESC,liquidity DESC LIMIT ?""",(batch,limit)).fetchall()
        except sqlite3.Error:
            return []
