#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, sqlite3
from binance_notify import resolve_chat_id, send_telegram

DB=os.getenv("BINANCE_DB","binance_avci2.db")
def main():
    token=(os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_cfg=(os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not os.path.exists(DB): return
    with sqlite3.connect(DB) as c:
        c.row_factory=sqlite3.Row
        scan=c.execute("SELECT scan_time_utc,health_status,data_mode FROM scans ORDER BY scan_time_utc DESC LIMIT 1").fetchone()
        rows=c.execute("""SELECT COALESCE(event_class,'UNKNOWN') cls,COUNT(*) n
            FROM flow_observations GROUP BY COALESCE(event_class,'UNKNOWN')""").fetchall()
        total=c.execute("SELECT COUNT(*) FROM flow_observations").fetchone()[0]
    if not scan: return
    parts=", ".join(f"{r['cls']}={r['n']}" for r in rows) or "henüz yok"
    text=(
      "💸 Money Flow | çalışıyor\n"
      f"• Son tarama: {scan['health_status']} | {scan['data_mode']}\n"
      f"• Akış gözlemi toplam: {total}\n"
      f"• Dağılım: {parts}"
    )
    chat=resolve_chat_id(token,chat_cfg,DB,"Money Flow")
    send_telegram(token,chat,text)
if __name__=="__main__": main()
