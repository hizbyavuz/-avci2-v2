#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Small Telegram heartbeat for Avci workflows.

Sends one compact status message after a successful scheduled run.
No trading action and no signal scoring.
"""
import os, sqlite3, sys
import requests
from binance_notify import resolve_chat_id, send_telegram

def table_exists(c, name):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None

def q1(c, sql, args=(), default=None):
    try:
        row=c.execute(sql,args).fetchone()
        return row[0] if row and row[0] is not None else default
    except sqlite3.Error:
        return default

def gate_status():
    db="avci2.db"
    if not os.path.exists(db): return ["Gate DB yok"]
    with sqlite3.connect(db,timeout=10) as c:
        health=None; observed=None; pairs=None; contracts=None; anomalies=None
        if table_exists(c,"gate_scan_health"):
            r=c.execute("SELECT status FROM gate_scan_health ORDER BY scan_ts DESC LIMIT 1").fetchone()
            health=r[0] if r else None
        if table_exists(c,"gate_early_observations"):
            observed=q1(c,"SELECT COUNT(*) FROM gate_early_observations",default=0)
            anomalies=q1(c,"SELECT COUNT(*) FROM gate_early_observations WHERE observed_anomaly=1",default=0)
        if table_exists(c,"gate_spot_health"):
            r=c.execute("SELECT pair_count,contract_count FROM gate_spot_health ORDER BY scan_ts DESC LIMIT 1").fetchone()
            if r: pairs,contracts=r
        out=[f"Tarama: {health or 'bilinmiyor'}"]
        if pairs is not None: out.append(f"Gate Spot: {pairs} parite • {contracts} kontrat")
        if observed is not None: out.append(f"Erken gözlem arşivi: {observed} • anomali: {anomalies}")
        return out

def spot_status():
    db=os.getenv("GATE_SPOT_DB","gate_spot_state.db")
    if not os.path.exists(db): return ["Gate Spot DB yok"]
    with sqlite3.connect(db,timeout=10) as c:
        if not table_exists(c,"gate_spot_health"): return ["Henüz snapshot yok"]
        r=c.execute("SELECT status,pair_count,contract_count FROM gate_spot_health ORDER BY scan_ts DESC LIMIT 1").fetchone()
        watches=q1(c,"SELECT COUNT(*) FROM gate_spot_watch WHERE status='PAPER_WATCH'",default=0) if table_exists(c,"gate_spot_watch") else 0
        return [f"Durum: {r[0] if r else 'bilinmiyor'}",
                f"Parite: {r[1] if r else 0} • kontrat: {r[2] if r else 0}",
                f"Kağıt izleme kaydı: {watches}"]

def history_status():
    db=os.getenv("HISTORY_DB","history_miner.db")
    if not os.path.exists(db): return ["History DB yok"]
    with sqlite3.connect(db,timeout=10) as c:
        pairs=q1(c,"SELECT COUNT(*) FROM cex_pairs",default=0) if table_exists(c,"cex_pairs") else 0
        done=q1(c,"SELECT COUNT(*) FROM cex_pairs WHERE status='DONE'",default=0) if table_exists(c,"cex_pairs") else 0
        bars=q1(c,"SELECT COUNT(*) FROM daily_bars",default=0) if table_exists(c,"daily_bars") else 0
        events=q1(c,"SELECT COUNT(*) FROM rise_events",default=0) if table_exists(c,"rise_events") else 0
        controls=q1(c,"SELECT COUNT(*) FROM event_features WHERE label='CONTROL'",default=0) if table_exists(c,"event_features") else 0
        timeline=q1(c,"SELECT COUNT(*) FROM timeline",default=0) if table_exists(c,"timeline") else 0
        return [f"Geçmiş parite: {done}/{pairs}",
                f"Günlük mum: {bars:,}",
                f"Yükseliş olayı: {events} • kontrol: {controls}",
                f"On-chain snapshot: {timeline}"]

def binance_status():
    db="binance_avci2.db"
    if not os.path.exists(db): return ["Binance DB yok"]
    with sqlite3.connect(db,timeout=10) as c:
        scan="tamamlandı"
        if table_exists(c,"scans"):
            try:
                cols={r[1] for r in c.execute("PRAGMA table_info(scans)")}
                if "data_health" in cols:
                    scan=q1(c,"SELECT data_health FROM scans ORDER BY rowid DESC LIMIT 1",default="tamamlandı")
            except sqlite3.Error: pass
        candidates=0
        if table_exists(c,"signal_events"):
            candidates=q1(c,"SELECT COUNT(*) FROM signal_events WHERE event_class='CANDIDATE'",default=0)
        features=q1(c,"SELECT COUNT(*) FROM features",default=0) if table_exists(c,"features") else 0
        return [f"Tarama: {scan}",f"Toplam aday kaydı: {candidates}",f"Feature kaydı: {features:,}"]

def main():
    mode=(sys.argv[1] if len(sys.argv)>1 else "").lower()
    funcs={"gate":gate_status,"spot":spot_status,"history":history_status,"binance":binance_status}
    if mode not in funcs:
        raise SystemExit("mode: gate|spot|history|binance")
    token=(os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    configured=(os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    if not token:
        print("Telegram heartbeat kapalı: token yok"); return
    db={"gate":"avci2.db","spot":os.getenv("GATE_SPOT_DB","gate_spot_state.db"),
        "history":os.getenv("HISTORY_DB","history_miner.db"),"binance":"binance_avci2.db"}[mode]
    chat=resolve_chat_id(token,configured,db,f"{mode} heartbeat")
    title={"gate":"GATE AVCI","spot":"GATE SPOT","history":"HISTORY MINER","binance":"BINANCE AVCI"}[mode]
    lines=(["⛏ GEÇMİŞ KAZICI | 10 DK RAPORU","Yeni tur tamamlandı."] if mode=="history"
       else ["🟢 MOTOR DURUMU | "+title,"Tur tamamlandı."])
    lines.extend("• "+x for x in funcs[mode]())
    send_telegram(token,chat,"\n".join(lines))
    print("Telegram heartbeat sent:",mode)

if __name__=="__main__":
    main()
