#!/usr/bin/env python3
"""V3.2 independent two-direction forward-test observer.

One state record per (symbol, direction); each side has its OWN locked trigger,
stop, targets, structure gate, analyst permission, and event history.

Frozen V3.1 signal/Telegram/execution code is never changed by this module.
This is a *separate* V3.2 dual-side paper layer, not a second order engine.
"""
from __future__ import annotations
import hashlib
import json
import math
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

VERSION="LS_V32_DUAL_SIDE_OBSERVER_2026_10_08"
MAX_SCAN_AGE_SECONDS=1200
MAX_CANDLE_AGE_SECONDS=75
MAX_PLAN_AGE_SECONDS=10800

def _ts(x):
    t=datetime.fromisoformat(str(x).replace("Z","+00:00"))
    if t.tzinfo is None: raise ValueError("NAIVE_TIMESTAMP")
    return t.astimezone(timezone.utc)

def versioned_plan(symbol,direction,plan,gate,score,opponent_score,permit,reason,source):
    """Only immutable initial plan fields take part in the setup identity."""
    core={
        "symbol":symbol,"direction":direction,"trigger_level":plan["trigger_level"],
        "invalidation":plan["invalidation"],"target1":plan["target1"],
        "target2":plan["target2"],"retest_low":plan["retest_low"],
        "retest_high":plan["retest_high"],"setup_type":plan.get("setup_type","BREAKOUT"),
    }
    data={"version":VERSION,**core,"score":float(score),"other_score":float(opponent_score),
          "analyst_permit":bool(permit),"nonpermit_reason":reason,"data_source":source,
          "structure_gate":gate}
    data["plan_id"]=hashlib.sha256(json.dumps(core,sort_keys=True).encode()).hexdigest()[:24]
    return data

def build_both_sides(*,symbol,price,k5,t15,chart,build_breakout,build_pullback,
                     build_structure,k15,k30,k1h,k4h,long_score,short_score,
                     preferred_direction,preferred_setup_type,analyst_eligible,
                     derivatives_ready,liquidity_ok,external_only,atr_pct,source,
                     required_room_pct,minimum_cost_pct,min_net_r):
    """Independently compute LONG and SHORT geometry from the SAME closed candles.

    Actionable permission cannot be created merely by observing the opposite
    direction. V3.1's existing direction hard gate remains authoritative for
    Telegram (the independent V3.2 gate is for comparison only).
    """
    out={}
    for direction in ("LONG","SHORT"):
        if direction==preferred_direction and preferred_setup_type=="PULLBACK":
            p=build_pullback(direction,price,k5,t15)
        else:
            p=build_breakout(direction,price,k5,t15,chart)
            p["setup_type"]="BREAKOUT"
        g=build_structure(direction,price,p,k5,k15,k30,k1h,k4h)
        trigger=float(p.get("trigger_level") or 0.0)
        stop=float(p.get("invalidation") or 0.0)
        t1=float(p.get("target1") or 0.0)
        # Do not rely on a boolean from a stale or contradictory zone.
        ordered=(stop<trigger<t1 if direction=="LONG" else t1<trigger<stop)
        cost=float(minimum_cost_pct)
        risk_pct=abs(trigger-stop)/trigger*100 if trigger>0 else 0
        reward_pct=((t1/trigger-1)*100 if direction=="LONG"
                    else (trigger/t1-1)*100) if t1>0 and trigger>0 else -999
        exact_net_r=(reward_pct-cost)/risk_pct if risk_pct>0 else None
        geom_ok=(ordered and bool(g.get("qualified_precheck"))
                 and float(g.get("room_pct") or 0)>=required_room_pct
                 and exact_net_r is not None and exact_net_r>=min_net_r)
        base_data=bool(derivatives_ready and liquidity_ok and not external_only
                       and float(atr_pct)<4.0 and source not in ("UNKNOWN","DERIVATIVES_INCOMPLETE"))
        # The opposite side remains monitored even when the V3.1 model
        # currently favors the other side; it cannot send a trade signal.
        eligible=bool(direction==preferred_direction and analyst_eligible and geom_ok and base_data)
        reason=("AUTHORIZED_OBSERVATION" if eligible else
                "MODEL_DIRECTION_NOT_AUTHORIZED" if direction!=preferred_direction else
                "BASE_DATA_INVALID" if not base_data else
                "STRUCTURE_OR_REWARD_RISK_VETO" if not geom_ok else "MODEL_UNQUALIFIED")
        out[direction]=versioned_plan(
            symbol,direction,p,g,long_score if direction=="LONG" else short_score,
            short_score if direction=="LONG" else long_score,
            eligible,reason,source)
        out[direction]["geometry_qualified"]=bool(geom_ok)
        out[direction]["data_healthy"]=bool(base_data)
        out[direction]["net_t1_r"]=round(exact_net_r,4) if exact_net_r is not None else None
    return out

def init(con):
    con.execute("""CREATE TABLE IF NOT EXISTS dual_side_v32(
        symbol TEXT NOT NULL,direction TEXT NOT NULL,plan_id TEXT NOT NULL,
        scan_utc TEXT NOT NULL,locked_at_utc TEXT NOT NULL,
        stage TEXT NOT NULL DEFAULT 'WATCH',
        authorized INTEGER NOT NULL,active INTEGER NOT NULL DEFAULT 1,
        last_candle_utc TEXT,
        confirmed_candle_utc TEXT,
        close_count INTEGER NOT NULL DEFAULT 0,
        plan_json TEXT NOT NULL,
        last_price REAL,
        last_updated_utc TEXT NOT NULL,
        PRIMARY KEY(symbol,direction)
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS dual_side_v32_events(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        observed_utc TEXT NOT NULL,symbol TEXT NOT NULL,direction TEXT NOT NULL,
        plan_id TEXT NOT NULL,from_stage TEXT,to_stage TEXT NOT NULL,
        event_price REAL,candle_utc TEXT,
        authorized INTEGER NOT NULL,source TEXT,
        status TEXT NOT NULL DEFAULT 'PAPER_ONLY',
        reason TEXT,
        payload_json TEXT NOT NULL
    )""")
    con.execute("CREATE INDEX IF NOT EXISTS dual_side_v32_time ON dual_side_v32_events(observed_utc,symbol,direction)")

def _event(con,when,row,previous,stage,price,candle,reason,extra=None):
    raw=json.loads(row["plan_json"])
    con.execute("""INSERT INTO dual_side_v32_events(
        observed_utc,symbol,direction,plan_id,from_stage,to_stage,event_price,
        candle_utc,authorized,source,status,reason,payload_json)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (when,row["symbol"],row["direction"],row["plan_id"],previous,stage,price,
         candle,int(row["authorized"]),raw.get("data_source"),
         "PAPER_ONLY",reason,json.dumps({"version":VERSION,"plan":raw,"extra":extra or {}},
                                    ensure_ascii=False)))

def sync(con,db,now_utc):
    """Read latest analyst snapshot and ensure each symbol keeps BOTH sides."""
    init(con)
    result={"scanned":0,"both_sides":0,"active":0,"authorized":0,"no_plan":0,"issues":[]}
    if not Path(db).is_file():
        result["issues"].append("ANALYST_DB_MISSING")
        return result
    try:
        with sqlite3.connect("file:"+os.path.abspath(db)+"?mode=ro",uri=True) as a:
            a.row_factory=sqlite3.Row
            snapshot=a.execute("SELECT MAX(scan_time_utc) FROM analyses").fetchone()[0]
            if not snapshot or not 0<=( _ts(now_utc)-_ts(snapshot)).total_seconds()<=MAX_SCAN_AGE_SECONDS:
                result["issues"].append("ANALYST_SCAN_STALE")
                return result
            entries=a.execute("SELECT symbol,payload_json FROM analyses WHERE scan_time_utc=?",(snapshot,)).fetchall()
    except (sqlite3.Error,OSError,TypeError,ValueError):
        result["issues"].append("ANALYST_DB_INVALID")
        return result
    con.execute("UPDATE dual_side_v32 SET active=0,authorized=0")
    result["scanned"]=len(entries)
    for e in entries:
        try:
            payload=json.loads(e["payload_json"] or "{}")
            pair=payload.get("v32_dual_sides") or {}
        except (TypeError,ValueError):
            continue
        if set(pair)!=set(("LONG","SHORT")):
            result["no_plan"]+=1
            continue
        result["both_sides"]+=1
        for direction in ("LONG","SHORT"):
            p=pair[direction]
            if p.get("version")!=VERSION or p.get("direction")!=direction or p.get("symbol")!=e["symbol"]:
                continue
            current=con.execute("SELECT * FROM dual_side_v32 WHERE symbol=? AND direction=?",
                                (e["symbol"],direction)).fetchone()
            authorized=int(bool(p.get("analyst_permit")))
            if current and current["plan_id"]==p["plan_id"]:
                # Same plan: NEVER move existing stop or target, only permission
                # and fresh scan. An invalidated plan cannot silently resurrect.
                con.execute("""UPDATE dual_side_v32 SET scan_utc=?,active=1,authorized=?,
                    last_updated_utc=? WHERE symbol=? AND direction=?""",
                    (snapshot,authorized,now_utc,e["symbol"],direction))
            elif current and current["stage"] in ("CLOSE_CONFIRMED","RETESTING","TRIGGER_READY"):
                # Old plan remains locked; an unaligned new one pauses it.
                con.execute("""UPDATE dual_side_v32 SET active=0,authorized=0,
                     last_updated_utc=? WHERE symbol=? AND direction=?""",
                    (now_utc,e["symbol"],direction))
                result["issues"].append("LOCKED_PLAN_CHANGED:"+e["symbol"]+":"+direction)
            else:
                # Refresh stale initial watch only on a NEW analyst scan.
                old_stage=current["stage"] if current else "NONE"
                if current:
                    _event(con,now_utc,current,old_stage,"REPLACED",current["last_price"],
                           current["last_candle_utc"],"NEW_SCAN_PLAN")
                con.execute("""INSERT OR REPLACE INTO dual_side_v32(
                    symbol,direction,plan_id,scan_utc,locked_at_utc,stage,authorized,active,
                    last_candle_utc,confirmed_candle_utc,close_count,plan_json,last_price,last_updated_utc)
                    VALUES(?,?,?,?,?,'WATCH',?,1,NULL,NULL,0,?,NULL,?)""",
                    (e["symbol"],direction,p["plan_id"],snapshot,now_utc,authorized,
                     json.dumps(p,ensure_ascii=False),now_utc))
    counts=con.execute("SELECT COUNT(*),SUM(authorized) FROM dual_side_v32 WHERE active=1").fetchone()
    result["active"]=int(counts[0] or 0)
    result["authorized"]=int(counts[1] or 0)
    con.commit()
    return result

def advance(con,symbol,snapshot,now_utc,confirm_fn):
    """One single venue-consistent snapshot updates the independent two sides.

    snapshot=(price,closed_5m,closed_candle_utc,early_features).
    Never sends Telegram or alters V3.1 event/state tables.
    """
    price,closed,candle,early=snapshot
    rows=con.execute("SELECT * FROM dual_side_v32 WHERE symbol=? AND active=1 ORDER BY direction",
                     (symbol,)).fetchall()
    transitions=[]
    for row in rows:
        p=json.loads(row["plan_json"])
        current=row["stage"]
        if current in ("INVALIDATED","EXPIRED","TRIGGER_READY"):
            continue
        try:
            since=(_ts(now_utc)-_ts(row["locked_at_utc"])).total_seconds()
            candle_age=(_ts(now_utc)-_ts(candle)).total_seconds()
        except (TypeError,ValueError):
            continue
        if not 0<=since<=MAX_PLAN_AGE_SECONDS:
            nxt="EXPIRED";reason="PLAN_TIMEOUT";data={}
        else:
            inv=float(p["invalidation"])
            direction=row["direction"]
            invalid=(closed<inv if direction=="LONG" else closed>inv)
            if invalid:
                nxt="INVALIDATED";reason="STOP_STRUCTURE_LOST";data={}
            elif not row["authorized"]:
                # Both are monitored, but the weaker thesis cannot make a
                # CLOSE_CONFIRMED by turning the latest price one way.
                nxt="OBSERVING" if current=="WATCH" else current
                reason="NOT_AUTHORIZED_BY_LATEST_DIRECTION_AND_HARD_GATES";data={}
            elif not (0<=candle_age<=MAX_CANDLE_AGE_SECONDS):
                nxt=current;reason="OLD_5M_CANDLE";data={}
            else:
                fake={
                    "direction":direction,"trigger_level":p["trigger_level"],
                    "structure_gate_json":json.dumps({
                        **p["structure_gate"],"_v3_precheck_ok":bool(p["geometry_qualified"]),
                        "_v3_setup_type":p.get("setup_type","BREAKOUT")}),
                }
                quality=confirm_fn(fake,closed,early)
                data={"qualified_close":bool(quality.get("qualified")),
                      "quality_checks":quality.get("quality_checks"),
                      "acceptance_bars":quality.get("acceptance_bars_beyond_last3")}
                crossed=closed>float(p["trigger_level"]) if direction=="LONG" else closed<float(p["trigger_level"])
                fresh_candle=candle!=row["last_candle_utc"]
                if not crossed or not quality.get("qualified"):
                    nxt="APPROACHING" if abs(price/float(p["trigger_level"])-1)*100<=0.25 else "WATCH"
                    reason="NO_QUALIFIED_CLOSE"
                elif not fresh_candle:
                    nxt=current;reason="REPEATED_CANDLE"
                elif current=="CLOSE_CONFIRMED" and quality.get("acceptance_bars_beyond_last3",0)>=2:
                    nxt="TRIGGER_READY";reason="TWO_CLOSED_BARS_ACCEPTED_PAPER_ONLY"
                else:
                    nxt="CLOSE_CONFIRMED";reason="FIRST_QUALIFIED_CLOSED_5M"
        con.execute("""UPDATE dual_side_v32 SET stage=?,last_price=?,last_candle_utc=?,
            confirmed_candle_utc=CASE WHEN ?='CLOSE_CONFIRMED' THEN ? ELSE confirmed_candle_utc END,
            last_updated_utc=? WHERE symbol=? AND direction=?""",
            (nxt,price,candle,nxt,candle,now_utc,symbol,row["direction"]))
        if nxt!=current:
            _event(con,now_utc,row,current,nxt,price,candle,reason,data)
            transitions.append({"symbol":symbol,"direction":row["direction"],
                                "from":current,"to":nxt,"authorized":bool(row["authorized"]),
                                "reason":reason})
    con.commit()
    return transitions
