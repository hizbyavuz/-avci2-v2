"""Offline/read-only context audit. No alerts, orders, scores or gate changes.
Run against a consistent snapshot of retained SQLite files.
The archived scan timestamp is not proof of its insertion/availability time.
"""
import argparse
import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

VERSION = "CONTEXT_AUDIT_V1_2026_10_14"
SOURCES = {"BINANCE_UM_FUTURES_WEBSOCKET", "BINANCE_UM_FUTURES_ARCHIVE", "BINANCE_UM_FUTURES_REST"}

def ro(path):
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError(str(path))
    c = sqlite3.connect(path.as_uri()+"?mode=ro", uri=True, timeout=5)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA query_only=ON")
    return c

def trend(tf):
    try:
        p, a, b = (float(tf[k]) for k in ("price", "ema20", "ema50"))
        if not all(math.isfinite(v) and v > 0 for v in (p,a,b)):
            return "UNKNOWN"
        return "UP" if p>a>b else "DOWN" if p<a<b else "MIXED"
    except (KeyError, ValueError, TypeError):
        return "UNKNOWN"

def archived_context(c, symbol, sent, max_age=600):
    # Compare numeric instants, not mixed ISO timestamp text formats.
    start = datetime.fromtimestamp(sent-max_age,timezone.utc).isoformat()
    end = datetime.fromtimestamp(sent,timezone.utc).isoformat()
    rows = c.execute("""SELECT scan_time_utc,payload_json FROM analyses
        WHERE symbol=? AND julianday(scan_time_utc) BETWEEN julianday(?) AND julianday(?)
        ORDER BY julianday(scan_time_utc) DESC LIMIT 1""",(symbol,start,end)).fetchall()
    if not rows:
        return {"status":"NO_RECENT_ARCHIVED_SCAN"}
    row = rows[0]
    stamp = datetime.fromisoformat(row["scan_time_utc"].replace("Z","+00:00"))
    if stamp.tzinfo is None:
        return {"status":"AMBIGUOUS_SCAN_TIME"}
    age = sent-stamp.timestamp()
    if not 0 <= age <= max_age:
        return {"status":"SCAN_OUTSIDE_WINDOW"}
    try:
        p = json.loads(row["payload_json"])
    except (ValueError, TypeError):
        return {"status":"UNREADABLE_SCAN"}
    if not isinstance(p,dict):
        return {"status":"UNREADABLE_SCAN"}
    engine = p.get("direction_engine") or p.get("v3") or {}
    return {"status":"ARCHIVED_SCAN_ONLY", "scan_age_seconds":round(age,3),
        "scan_time_utc":row["scan_time_utc"],
        "availability_at_delivery_verified":False,
        "availability_note":"scan timestamp only; completion/insertion receipt not retained here",
        "direction":engine.get("direction"), "eligible":engine.get("eligible"),
        "score":engine.get("best_score"), "edge":engine.get("edge"),
        "trend_15m":trend(p.get("t15") or {}),
        "trend_1h":trend(p.get("t1h") or {}),
        "trend_4h":trend(p.get("t4h") or {}),
        "market_regime":p.get("market_regime"),
        "quote_volume_24h":p.get("quote_volume_24h"),
        "score_components":p.get("score_components"),
        "setup_plan":p.get("setup_plan"), "structure_gate":p.get("structure_gate"),
        "derivatives_ready":p.get("derivatives_ready"),
        "data_mode":p.get("data_mode")}

def volume_context(c, symbol, sent):
    # Seven complete trailing 24h blocks, aligned to the last closed hour.
    hour=3600000
    end=int(sent*1000)//hour*hour
    begin=end-168*hour
    rows=c.execute("""SELECT open_ms,close_ms,quote_volume,source
        FROM futures_closed_klines WHERE symbol=? AND interval='1h'
        AND open_ms>=? AND open_ms<? ORDER BY open_ms""",(symbol,begin,end)).fetchall()
    if [r["open_ms"] for r in rows] != list(range(begin,end,hour)):
        return {"status":"INCOMPLETE_7D_HOURLY_PATH","bars":len(rows)}
    for r in rows:
        if (r["close_ms"]!=r["open_ms"]+hour-1 or r["source"] not in SOURCES
            or r["quote_volume"] is None or not math.isfinite(float(r["quote_volume"]))
            or float(r["quote_volume"])<0):
            return {"status":"UNVERIFIED_HOURLY_PATH"}
    blocks=[sum(float(r["quote_volume"]) for r in rows[i:i+24]) for i in range(0,168,24)]
    prev=blocks[-2];five=blocks[-6]
    return {"status":"RECONSTRUCTED_CLOSED_CANDLES","availability_at_delivery_verified":False,
        "end_utc":datetime.fromtimestamp(end/1000,timezone.utc).isoformat(),
        "lag_seconds":sent-end/1000,"quote_volume_24h_blocks_oldest_first":blocks,
        "last24_vs_previous24":blocks[-1]/prev if prev else None,
        "last24_vs_5days_earlier24":blocks[-1]/five if five else None}

def summarize(messages):
    result={}
    for h in ("15","30","60","180"):
        resolved=[m for m in messages if m.get("included_as_direction_idea")
                  and m.get("outcomes",{}).get(h,{}).get("status")=="RESOLVED"
                  and m.get("direction") in ("LONG","SHORT")]
        values=[m["outcomes"][h]["direction_pct"] for m in resolved]
        raw=[v*(1 if m["direction"]=="LONG" else -1) for m,v in zip(resolved,values)]
        result[h]={"resolved":len(values),"right":sum(v>0 for v in values),
                   "wrong":sum(v<0 for v in values),"flat":sum(v==0 for v in values),
                   "always_long_right_same_endpoints":sum(v>0 for v in raw),
                   "always_short_right_same_endpoints":sum(v<0 for v in raw),
                   "mean_gross_direction_pct":sum(values)/len(values) if values else None}
    return result

def run(state, analyst_db, now, hours):
    from long_short_message_outcomes import report
    audit=report(state,now=now,hours=hours)
    with ro(analyst_db) as analyst, ro(Path(state)/"binance_futures_kline_shadow.db") as candles:
        for m in audit["messages"]:
            if not m.get("included_as_direction_idea"):
                continue
            for key,fn in (("archived_context",lambda:archived_context(analyst,m["symbol"],m["sent_epoch"])),
                           ("volume_context",lambda:volume_context(candles,m["symbol"],m["sent_epoch"]))):
                try:m[key]=fn()
                except (sqlite3.Error,ValueError,TypeError) as e:
                    m[key]={"status":"DATA_ERROR","error_type":type(e).__name__}
    early=[m for m in audit["messages"] if m["kind"]=="EARLY_EARLY"]
    return {"version":VERSION,"research_only":True,"read_only":True,
            "issues":audit["issues"],"hours":hours,"now_epoch":now,
            "analyst_db":str(Path(analyst_db).name),
            "scope":"retained message receipts; edits are updates, not independent trades",
            "limitations":["source_text may differ from merged displayed card",
                          "no entry/exit execution simulation","no out-of-sample efficacy claim",
                          "archived scan time is not verified availability time",
                          "candle reconstruction does not establish original feed availability"],
            "early_baselines":summarize(early),"messages":audit["messages"]}

if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state",required=True)
    parser.add_argument("--analyst-db",required=True)
    parser.add_argument("--now",type=float,required=True,help="Frozen UTC epoch cutoff")
    parser.add_argument("--hours",type=float,default=24)
    args=parser.parse_args()
    if not math.isfinite(args.now) or not math.isfinite(args.hours) or not 0<args.hours<=168:
        parser.error("finite now and 0<hours<=168 required")
    print(json.dumps(run(args.state,args.analyst_db,args.now,args.hours),ensure_ascii=False))
