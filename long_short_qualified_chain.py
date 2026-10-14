"""Versioned candidate-to-core routing; no direction or execution threshold changes."""
import json
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path

VERSION="QUALIFIED_SIGNAL_CHAIN_V1_2026_10_14"

def enabled():
    return os.getenv("LS_QUALIFIED_SIGNAL_CHAIN_V1","0").strip().lower() in ("1","true","yes","on")

def read_pending(root, analyst_db, now_ms=None):
    """Fresh active observations, ordered by least recently analysed first.
    Scheduling only: neither observation direction nor strength grants entry.
    """
    now_ms=int(time.time()*1000) if now_ms is None else now_ms
    path=Path(root)/"long_short_breakout_observer.db"
    if not path.is_file():
        return []
    with sqlite3.connect(path.resolve().as_uri()+"?mode=ro",uri=True,timeout=3) as c:
        rows=c.execute("""SELECT symbol,early_ms,last_features_json FROM episodes
            WHERE state IN ('EARLY','CONFIRMED') AND ended_ms IS NULL
            AND early_ms IS NOT NULL AND early_ms<=?""",(now_ms,)).fetchall()
    latest={}
    if Path(analyst_db).is_file():
        with sqlite3.connect(Path(analyst_db).resolve().as_uri()+"?mode=ro",uri=True,timeout=3) as c:
            for symbol,stamp in c.execute("SELECT symbol,MAX(scan_time_utc) FROM analyses GROUP BY symbol"):
                try:
                    dt=datetime.fromisoformat(stamp.replace("Z","+00:00"))
                    if dt.tzinfo is not None and dt.timestamp()*1000<=now_ms:
                        latest[symbol]=dt.timestamp()*1000
                except (ValueError,TypeError,AttributeError):
                    continue
    candidates={}
    reasons={"active_episodes":len(rows),"stale_price":0,"invalid_features":0,"recently_analysed":0,"eligible":0}
    for symbol,early_ms,raw in rows:
        try:
            f=json.loads(raw)
            # Reuse the observer's existing live-price freshness constraint.
            if not 0<=now_ms-f["price_event_ms"]<=30000:
                reasons["stale_price"]+=1
                continue
        except (ValueError,TypeError,KeyError):
            reasons["invalid_features"]+=1
            continue
        last=latest.get(symbol,0)
        if last>=early_ms and now_ms-last<300000:
            reasons["recently_analysed"]+=1
            continue
        key=(last if last>=early_ms else 0,early_ms,symbol)
        candidates[symbol]=min(candidates.get(symbol,key),key)
    reasons["eligible"]=len(candidates)
    print("QUALIFIED_CHAIN_PRIORITY_DIAG "+json.dumps(reasons,sort_keys=True),flush=True)
    return sorted(candidates,key=lambda s:candidates[s])

def reserve(rows, normal, priority, cap, symbol):
    """Reserve at most six existing slots, never add or duplicate symbols."""
    cap=max(0,int(cap))
    lookup={symbol(r):r for r in rows}
    keys=list(dict.fromkeys(s for s in priority if s in lookup))[:min(6,cap)]
    selected=[lookup[s] for s in keys]
    seen=set(keys)
    for r in normal:
        key=symbol(r)
        if key not in seen and len(selected)<cap:
            selected.append(r);seen.add(key)
    return selected[:cap]

def priority(root,analyst_db):
    try:
        return read_pending(root,analyst_db)
    except (sqlite3.Error,OSError) as e:
        print("QUALIFIED_CHAIN_PRIORITY_UNAVAILABLE "+type(e).__name__,flush=True)
        return []

def retain_candidates(state):
    """Keep every observation; consume transport queue without sending alerts."""
    cur=state.execute("""UPDATE transitions SET delivery_status='CANDIDATE_ONLY_V1'
        WHERE delivery_status='PENDING' OR delivery_status LIKE 'FAILED:%'""")
    state.commit()
    if cur.rowcount:
        print("QUALIFIED_CHAIN_CANDIDATES "+json.dumps(
            {"version":VERSION,"retained":cur.rowcount,"telegram_sent":0}),flush=True)
    return cur.rowcount

