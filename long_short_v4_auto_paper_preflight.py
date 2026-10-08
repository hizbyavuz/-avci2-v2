#!/usr/bin/env python3
"""Read-only bridge from actual SENT V3 Telegram triggers to V4 auto-paper gate.
Only writes to independent V4 DB; never sends orders or Telegram. Every rejection
is archived and its reasons exposed. NO missing-data neutral defaults.
"""
from __future__ import annotations
import argparse
import json
import os
import sqlite3
import time
from datetime import datetime,timezone
from long_short_v4_execution_gate import assess,reserve,change_state,init_journal
from long_short_v4_native_pipeline import SOURCE
from long_short_v4_research import setup

def milliseconds(value):
    dt=datetime.fromisoformat(str(value).replace("Z","+00:00"))
    if dt.tzinfo is None:raise ValueError("naive UTC time")
    return int(dt.timestamp()*1000)

def read_recent_events(live_db,cutoff_ms):
    if not live_db or not os.path.isfile(live_db):return []
    with sqlite3.connect("file:"+os.path.abspath(live_db)+"?mode=ro",uri=True) as c:
        c.row_factory=sqlite3.Row
        # The immutable event's SMS status, not an unconfirmed analyst watch,
        # decides what reaches the research gate.
        rows=c.execute("""SELECT * FROM events
         WHERE stage_to='TRIGGERED' AND telegram_status='SENT'
         AND telegram_sent_time_utc IS NOT NULL ORDER BY id DESC LIMIT 50""").fetchall()
    return [dict(r) for r in rows if milliseconds(r["telegram_sent_time_utc"])>=cutoff_ms]

def latest_market(c,symbol,asof_ms):
    """Only recorded exchange events from before the exact decision timestamp."""
    c.row_factory=sqlite3.Row
    book=c.execute("""SELECT * FROM book_top WHERE symbol=? AND event_ms<=?
      ORDER BY event_ms DESC LIMIT 1""",(symbol,asof_ms)).fetchone()
    mark=c.execute("""SELECT * FROM marks WHERE symbol=? AND event_ms<=?
      ORDER BY event_ms DESC LIMIT 1""",(symbol,asof_ms)).fetchone()
    feat=c.execute("""SELECT * FROM feature_snapshots WHERE symbol=? AND close_ms<=?
      ORDER BY close_ms DESC LIMIT 1""",(symbol,asof_ms)).fetchone()
    if not book or not mark or not feat:return None
    book=dict(book);mark=dict(mark);feat=dict(feat)
    return {"symbol":symbol,"source":SOURCE,"event_ms":min(int(book["event_ms"]),int(mark["event_ms"])),
        "closed_candle_time_ms":int(feat["close_ms"]),
        "data_health_ok":feat["health"]=="OBSERVATION_ONLY",
        "complete_history":not json.loads(feat["errors_json"] or "[]"),
        "closed_candle_only":True,
        "spread_bps":float(book["spread_bps"]),
        # No full order-book quote/depth => cannot estimate $50 slippage safely.
        "estimated_slippage_bps":None,
        # Never infer exchange filters or trading status from spot or demo.
        "step_size":None,"min_qty":None,"min_notional":None,
        "best_bid":float(book["bid"]),"best_ask":float(book["ask"]),
        "mark_price":float(mark["mark"])}

def normalize_plan(ev):
    payload=json.loads(ev.get("payload_json") or "{}")
    source=str(payload.get("_live_price_source") or "UNKNOWN")
    d=ev["direction"]
    return {"setup_id":str(payload.get("plan_id") or ev["id"]),
        "symbol":ev["symbol"],"state":"TRIGGERED",
        "direction":d,
        # A Bybit/Gate chart NEVER becomes a native Binance Futures setup.
        "source":SOURCE if source in ("BINANCE_FUTURES","BINANCE_FUTURES_UM_WS") else source,
        "signal_time_ms":milliseconds(ev["telegram_sent_time_utc"]),
        "entry":ev["price"],"stop":payload.get("invalidation"),
        "tp1":payload.get("target1"),"tp2":payload.get("target2"),
        "analyst_authorized":True,
        "stop_and_tp_locked":bool(payload.get("invalidation") and payload.get("target1") and payload.get("target2")),
        "derivatives_quality_ok":str(payload.get("derivatives_quality") or "").upper() in ("FULL","NATIVE"),
        "data_cohort":"BINANCE_FUTURES_NATIVE" if source in ("BINANCE_FUTURES","BINANCE_FUTURES_UM_WS")
              else "VENUE_LABELED_EXTERNAL" if source in ("GATE_FUTURES","BYBIT_LINEAR") else "UNKNOWN",
        "telegram_sent_time_utc":ev["telegram_sent_time_utc"]}

def initialize(c):
    init_journal(c)
    setup(c)
    c.executescript("""CREATE TABLE IF NOT EXISTS v4_gate_audit (
      signal_id TEXT PRIMARY KEY, seen_ms INTEGER NOT NULL, symbol TEXT NOT NULL,
      approved INTEGER NOT NULL, reasons_json TEXT NOT NULL, verdict_json TEXT NOT NULL,
      signal_json TEXT NOT NULL, check_mode TEXT NOT NULL
    );""")
    c.commit()

def scan(live_db,native_db,now_ms=None):
    now_ms=int(now_ms or time.time()*1000)
    events=read_recent_events(live_db,now_ms-90000)
    results=[]
    os.makedirs(os.path.dirname(os.path.abspath(native_db)),exist_ok=True)
    with sqlite3.connect(native_db) as c:
        initialize(c)
        for ev in events:
            plan=normalize_plan(ev)
            market=latest_market(c,plan["symbol"],now_ms) or {}
            # Paper account values are explicit laboratory assumptions.
            # There is NO exchange equity/position access here.
            account={"one_way_mode":True,"isolated_margin":True,"account_reconciled":False,
              "kill_switch":False,"unresolved_order":False,"unprotected_position":False,
              "open_positions":None,"realized_loss_today_usdt":None,
              "equity_usdt":None,"leverage":None,"venue_permitted":False}
            decision=assess(plan,market,account,mode="PAPER",now_ms=now_ms)
            # NEVER approve pretend account state. A separate authenticated
            # testnet account reconciliation must supply actual values.
            c.execute("""INSERT OR IGNORE INTO v4_gate_audit
              (signal_id,seen_ms,symbol,approved,reasons_json,verdict_json,signal_json,check_mode)
              VALUES(?,?,?,?,?,?,?,?)""",
              (str(ev["id"]),now_ms,ev["symbol"],int(decision.approved),
               json.dumps(decision.reasons),json.dumps(decision.__dict__),
               json.dumps(plan),"PAPER_READONLY_PREFLIGHT"))
            results.append({"symbol":ev["symbol"],"approved":decision.approved,
                            "reasons":decision.reasons})
        c.commit()
    return {"mode":"READ_ONLY_PREFLIGHT","signals_checked":len(results),
            "signals_ready":sum(r["approved"] for r in results),
            "signals_blocked":sum(not r["approved"] for r in results),
            "details":results,"automatic_real_orders":0}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--live-db",default="long_short_v31_live_pool.db")
    p.add_argument("--native-db",default="long_short_v4_native.db")
    p.add_argument("--report",default="long_short_v4_auto_paper_preflight.json")
    args=p.parse_args()
    result=scan(args.live_db,args.native_db)
    with open(args.report,"w",encoding="utf-8") as f:json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps({k:v for k,v in result.items() if k!="details"},ensure_ascii=False))

if __name__=="__main__":main()
