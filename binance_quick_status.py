#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, sqlite3
from binance_notify import resolve_chat_id, send_telegram

DB=os.getenv("BINANCE_DB","binance_avci2.db")

def table(c,t):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone() is not None

def main():
    if not os.path.exists(DB):
        print("quick status: DB yok"); return
    with sqlite3.connect(DB,timeout=30) as c:
        c.row_factory=sqlite3.Row
        scan=c.execute("""SELECT * FROM scans WHERE health_status!='INVALID'
          ORDER BY scan_time_utc DESC LIMIT 1""").fetchone()
        if not scan:
            print("quick status: scan yok"); return
        ts=scan["scan_time_utc"]
        cand=c.execute("""SELECT COUNT(*) n FROM features
          WHERE scan_time_utc=? AND selection_class='CANDIDATE'""",(ts,)).fetchone()["n"]
        rows=[]
        if table(c,"binance_live_pool"):
            rows=c.execute("""SELECT p.symbol,p.status,p.confirmation_score,
                       f.stage,f.engine,f.score,f.change_24h
                FROM binance_live_pool p
                LEFT JOIN features f ON f.scan_time_utc=p.scan_time_utc AND f.symbol=p.symbol
                WHERE p.scan_time_utc=?
                ORDER BY CASE p.status WHEN 'CONFIRMED' THEN 0 WHEN 'BORDERLINE' THEN 1 ELSE 2 END,
                         p.confirmation_score DESC
                LIMIT 5""",(ts,)).fetchall()
        counts={}
        if table(c,"binance_live_pool"):
            counts={r["status"]:r["n"] for r in c.execute(
                """SELECT status,COUNT(*) n FROM binance_live_pool
                   WHERE scan_time_utc=? GROUP BY status""",(ts,)).fetchall()}
    lines=[
        "🛰 BINANCE AVCI | HIZLI TUR SONUCU",
        f"• Taranan: {scan['universe_size']} coin | ilk aday: {cand}",
        f"• BTC: {scan['btc_regime']} | 24s %{scan['btc_change_24h']:+.2f}",
        f"• 15dk teyit: {counts.get('CONFIRMED',0)} geçti | {counts.get('BORDERLINE',0)} sınırda | {counts.get('FADED',0)} söndü",
        ""
    ]
    good=[r for r in rows if r["status"] in ("CONFIRMED","BORDERLINE")]
    if not good:
        lines += [
            "🚫 Bu tur 15 dakikalık kontrolden geçen temiz aday yok.",
            "(Bu mesaj kısa tur sonucudur; uzun matematik raporu ayrıca hazırlanır.)"
        ]
    else:
        lines.append("İzlenebilir adaylar:")
        for r in good[:5]:
            st="TEYİT GEÇTİ" if r["status"]=="CONFIRMED" else "SINIRDA"
            lines.append(f"• {r['symbol']} — {st} | canlı teyit {min(int(r['confirmation_score'] or 0),6)}/6")
            if r["change_24h"] is not None:
                lines.append(f"  24s hareket %{float(r['change_24h']):+.1f} | aşama {r['stage'] or '-'}")
        lines.append("")
        lines.append("Not: Bu erken bildirimdir; geçmiş başarı, kontrol grubu ve maliyet sonrası matematik uzun raporda gelir.")
    token=(os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        print("\n".join(lines)); return
    chat=resolve_chat_id(token,(os.getenv("TELEGRAM_CHAT_ID") or "").strip(),DB,"Binance Motor")
    send_telegram(token,chat,"\n".join(lines)[:3900])
    print("Binance quick Telegram sent")

if __name__=="__main__":
    main()
