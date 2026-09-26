#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Record reported recommendations and send one short 72h follow-up.

Research-only. Does not change scanner rules, evidence scores, or candidate labels.
"""
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone, timedelta

from binance_notify import resolve_chat_id, send_telegram

HOURS=72
COOLDOWN_HOURS=24

def now():
    return datetime.now(timezone.utc)

def parse_dt(x):
    if not x:
        return None
    try:
        return datetime.fromisoformat(str(x).replace("Z","+00:00"))
    except Exception:
        return None

def table(c,t):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone() is not None

def init(c):
    c.execute("""CREATE TABLE IF NOT EXISTS recommendation_followups(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT NOT NULL,
        asset_key TEXT NOT NULL,
        display_name TEXT NOT NULL,
        recommended_at_utc TEXT NOT NULL,
        recommendation_price REAL,
        evidence_tier TEXT,
        early_dot TEXT,
        due_at_utc TEXT NOT NULL,
        checked_at_utc TEXT,
        followup_price REAL,
        change_pct REAL,
        status TEXT NOT NULL DEFAULT 'PENDING',
        UNIQUE(source,asset_key,recommended_at_utc)
    )""")
    c.execute("""CREATE INDEX IF NOT EXISTS idx_recommendation_followups_due
        ON recommendation_followups(source,status,due_at_utc)""")

def change_pct(a,b):
    if not a or not b:
        return None
    return 100.0*(float(b)/float(a)-1.0)

def early_binance(r):
    trigger=bool(r["trigger"]); wake=bool(r["wakeup"]); reign=bool(r["reignition"])
    retention=bool(r["retention"]); climax=bool(r["climax_risk"])
    if trigger and (wake or reign) and retention and not climax: return "🔴"
    if (trigger or wake or reign) and not climax: return "🟡"
    return "🟢"

def early_gate(obs):
    if not obs: return "🟢"
    vr=obs["own_volume_ratio"]; buys=float(obs["buys_5m"] or 0); sells=float(obs["sells_5m"] or 0)
    day=float(obs["change_24h"] or 0)
    score=sum((vr is not None and float(vr)>=2.0,
               (buys+sells)>=5 and buys>=1.3*max(sells,1),
               -2<=day<=20))
    return "🔴" if score==3 else ("🟡" if score>=2 else "🟢")

def recent_exists(c,source,asset_key,at):
    cutoff=(at-timedelta(hours=COOLDOWN_HOURS)).isoformat()
    return c.execute("""SELECT 1 FROM recommendation_followups
        WHERE source=? AND asset_key=? AND recommended_at_utc>=? LIMIT 1""",
        (source,asset_key,cutoff)).fetchone() is not None

def register_binance(c):
    scan=c.execute("""SELECT scan_time_utc FROM scans WHERE health_status!='INVALID'
        ORDER BY scan_time_utc DESC LIMIT 1""").fetchone()
    if not scan or not table(c,"binance_candidate_evidence"):
        return 0
    ts=scan["scan_time_utc"]; at=parse_dt(ts) or now()
    rows=c.execute("""SELECT e.*,f.price,f.wakeup,f.reignition,f.trigger,f.retention,f.climax_risk
        FROM binance_candidate_evidence e JOIN features f
        ON f.scan_time_utc=e.scan_time_utc AND f.symbol=e.symbol
        WHERE e.scan_time_utc=?
        ORDER BY CASE e.summary WHEN 'DAYANAK_COK_GUCLU' THEN 0
             WHEN 'DAYANAK_GUCLU' THEN 1 WHEN 'DAYANAK_ORTA' THEN 2 ELSE 3 END,
             e.evidence_count DESC,e.counter_count ASC LIMIT 5""",(ts,)).fetchall()
    n=0
    for r in rows:
        key=r["symbol"]
        if recent_exists(c,"BINANCE",key,at):
            continue
        due=(at+timedelta(hours=HOURS)).isoformat()
        c.execute("""INSERT OR IGNORE INTO recommendation_followups
            (source,asset_key,display_name,recommended_at_utc,recommendation_price,
             evidence_tier,early_dot,due_at_utc,status)
            VALUES('BINANCE',?,?,?,?,?,?,?,'PENDING')""",
            (key,key,ts,r["price"],r["summary"],early_binance(r),due))
        n+=c.execute("SELECT changes()").fetchone()[0]
    return n

def gate_name(c,network,contract):
    if table(c,"snapshots"):
        row=c.execute("""SELECT raw_json FROM snapshots WHERE network_id=? AND token_contract=?
            ORDER BY id DESC LIMIT 1""",(network,contract)).fetchone()
        if row:
            try:
                d=json.loads(row[0] or "{}")
                return d.get("symbol") or d.get("name") or contract[:8]
            except Exception:
                pass
    return contract[:8]

def register_gate(c):
    health=c.execute("""SELECT batch_id,scan_ts FROM gate_scan_health WHERE status='VALID'
        ORDER BY scan_ts DESC LIMIT 1""").fetchone()
    if not health or not table(c,"gate_candidate_evidence"):
        return 0
    batch=health["batch_id"]; at=datetime.fromtimestamp(int(health["scan_ts"]),timezone.utc)
    rows=c.execute("""SELECT * FROM gate_candidate_evidence WHERE batch_id=?
        ORDER BY CASE summary WHEN 'DAYANAK_COK_GUCLU' THEN 0
             WHEN 'DAYANAK_GUCLU' THEN 1 WHEN 'DAYANAK_ORTA' THEN 2 ELSE 3 END,
             evidence_count DESC,counter_count ASC LIMIT 5""",(batch,)).fetchall()
    n=0
    for r in rows:
        net=r["network_id"]; contract=r["token_contract"]; key=f"{net}:{contract}"
        if recent_exists(c,"GATE",key,at):
            continue
        obs=c.execute("""SELECT price,own_volume_ratio,buys_5m,sells_5m,change_24h
            FROM gate_early_observations WHERE batch_id=? AND network_id=? AND token_contract=?
            LIMIT 1""",(batch,net,contract)).fetchone() if table(c,"gate_early_observations") else None
        price=float(obs["price"]) if obs and obs["price"] else None
        name=gate_name(c,net,contract)
        due=(at+timedelta(hours=HOURS)).isoformat()
        c.execute("""INSERT OR IGNORE INTO recommendation_followups
            (source,asset_key,display_name,recommended_at_utc,recommendation_price,
             evidence_tier,early_dot,due_at_utc,status)
            VALUES('GATE',?,?,?,?,?,?,?,'PENDING')""",
            (key,name,at.isoformat(),price,r["summary"],early_gate(obs),due))
        n+=c.execute("SELECT changes()").fetchone()[0]
    return n

def latest_binance_price(c,symbol):
    r=c.execute("""SELECT price FROM features WHERE symbol=? AND price IS NOT NULL
        ORDER BY scan_time_utc DESC LIMIT 1""",(symbol,)).fetchone()
    return float(r[0]) if r and r[0] else None

def latest_gate_price(c,key):
    try: net,contract=key.split(":",1)
    except ValueError: return None
    r=c.execute("""SELECT price FROM gate_early_observations
        WHERE network_id=? AND token_contract=? AND price IS NOT NULL
        ORDER BY scan_ts DESC LIMIT 1""",(net,contract)).fetchone()
    return float(r[0]) if r and r[0] else None

def process_due(c,source):
    due=c.execute("""SELECT * FROM recommendation_followups
        WHERE source=? AND status='PENDING' AND due_at_utc<=?
        ORDER BY due_at_utc LIMIT 20""",(source,now().isoformat())).fetchall()
    out=[]
    for r in due:
        px=latest_binance_price(c,r["asset_key"]) if source=="BINANCE" else latest_gate_price(c,r["asset_key"])
        if px is None:
            # Keep it pending; a later scan may recover a valid market price.
            continue
        ch=change_pct(r["recommendation_price"],px)
        c.execute("""UPDATE recommendation_followups SET checked_at_utc=?,followup_price=?,
            change_pct=?,status='CHECKED' WHERE id=?""",(now().isoformat(),px,ch,r["id"]))
        out.append((r,px,ch))
    return out

def send_followups(db,source,rows):
    if not rows:
        return
    token=(os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        return
    chat=resolve_chat_id(token,(os.getenv("TELEGRAM_CHAT_ID") or "").strip(),db,
                         "Binance Motor" if source=="BINANCE" else "Gate Web3 Motor")
    lines=["⏱ 3 GÜN SONRA KONTROL"]
    for r,px,ch in rows:
        before="-" if r["recommendation_price"] is None else f"{float(r['recommendation_price']):.8g}"
        after=f"{px:.8g}"
        delta="-" if ch is None else f"%{ch:+.1f}"
        lines.append(f"• {r['display_name']}: {before} → {after} | {delta}")
    send_telegram(token,chat,"\n".join(lines)[:3900])

def main():
    source=(sys.argv[1] if len(sys.argv)>1 else "").strip().lower()
    if source not in ("binance","gate"):
        raise SystemExit("usage: recommendation_followup.py binance|gate")
    db=os.getenv("BINANCE_DB","binance_avci2.db") if source=="binance" else os.getenv("AVCI_DB","avci2.db")
    if not os.path.exists(db):
        print("followup DB yok",db); return
    with sqlite3.connect(db,timeout=30) as c:
        c.row_factory=sqlite3.Row
        init(c)
        added=register_binance(c) if source=="binance" else register_gate(c)
        checked=process_due(c,source.upper())
        c.commit()
    send_followups(db,source.upper(),checked)
    print(f"recommendation followup | source={source} added={added} checked={len(checked)}")

if __name__=="__main__":
    main()
