#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Trader-facing Binance Telegram summary.

Research continues in the background. This file only compresses the latest
research state into one short human-facing message. It never changes scanner,
selection, validation, or security rules.
"""
import json, os, sqlite3
from binance_notify import resolve_chat_id, send_telegram

DB=os.getenv("BINANCE_DB","binance_avci2.db")

def table(c,t):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone() is not None

def arr(s):
    try:
        x=json.loads(s or "[]")
        return x if isinstance(x,list) else []
    except Exception:
        return []

def pick_reason(r):
    con=arr(r["counter_json"]); unk=arr(r["unknown_json"])
    if con: return con[0]
    if unk: return unk[0]
    return "işlem hazırlığı tamamlanmadı"

def main():
    if not os.path.exists(DB):
        print("Binance trader message: DB yok"); return
    with sqlite3.connect(DB,timeout=30) as c:
        c.row_factory=sqlite3.Row
        scan=c.execute("""SELECT * FROM scans WHERE health_status!='INVALID'
            ORDER BY scan_time_utc DESC LIMIT 1""").fetchone()
        if not scan:
            print("Binance trader message: scan yok"); return
        ts=scan["scan_time_utc"]
        rows=[]
        if table(c,"binance_candidate_evidence") and table(c,"trade_readiness"):
            rows=c.execute("""SELECT e.*,f.change_24h,f.btc_relative_24h,
                       p.status pool_status,p.confirmation_score pool_score,
                       tr.readiness,tr.execution_quality,tr.historical_edge,
                       tr.microstructure_quality
                FROM binance_candidate_evidence e
                JOIN features f ON f.scan_time_utc=e.scan_time_utc AND f.symbol=e.symbol
                LEFT JOIN binance_live_pool p
                  ON p.scan_time_utc=e.scan_time_utc AND p.symbol=e.symbol
                JOIN trade_readiness tr
                  ON tr.source='BINANCE' AND tr.batch_key=e.scan_time_utc
                 AND tr.asset_key=e.symbol
                WHERE e.scan_time_utc=?
                ORDER BY CASE tr.readiness WHEN 'PAPER_ELIGIBLE' THEN 0
                         WHEN 'WATCH' THEN 1 ELSE 2 END,
                         e.evidence_count DESC,e.counter_count ASC""",(ts,)).fetchall()
        ready=[r for r in rows if r["readiness"]=="PAPER_ELIGIBLE"]
        watch=[r for r in rows if r["readiness"]=="WATCH"]

        lines=["🛰 BINANCE AVCI",
               f"• Piyasa: {scan['btc_regime']} | BTC 24s %{float(scan['btc_change_24h']):+.2f}",
               f"• Taranan: {scan['universe_size']} coin | işlem-hazır: {len(ready)}",
               ""]
        if ready:
            for r in ready[:2]:
                sup=arr(r["support_json"]); con=arr(r["counter_json"])
                score=min(int(r["pool_score"] or 0),6) if r["pool_status"] else 0
                lines.append(f"🟢 İŞLEM-HAZIR — {r['symbol']}")
                why=sup[:3]
                if score:
                    why=["15dk canlı teyit geçti"]+why
                lines.append("• Neden: "+"; ".join(dict.fromkeys(why)) if why else "• Neden: çoklu doğrulama geçti")
                if r["historical_candidate_n"]>=8 and r["historical_control_n"]>=8 and r["hit10_rate"] is not None and r["hit10_control"] is not None:
                    lines.append(f"• Benzer geçmiş +10: %{100*float(r['hit10_rate']):.0f} vs kontrol %{100*float(r['hit10_control']):.0f}")
                lines.append("• Risk: "+(con[0] if con else "kritik karşı kanıt yok"))
                lines.append("")
        else:
            lines.append("🚫 Bu tur işlem-hazır aday yok.")
            lines.append("")

        if watch:
            lines.append("🟡 İZLENEN — ÖNERİ DEĞİL")
            for r in watch[:2]:
                lines.append(f"• {r['symbol']}: {pick_reason(r)}")
            lines.append("")

        lines.append("Not: Araştırma/paper modudur; WATCH coinler işlem adayı değildir.")

    msg="\n".join(lines)
    token=(os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        print(msg); return
    chat=resolve_chat_id(token,(os.getenv("TELEGRAM_CHAT_ID") or "").strip(),DB,"Binance Motor")
    send_telegram(token,chat,msg[:3900])
    print("Binance trader Telegram sent")

if __name__=="__main__":
    main()
