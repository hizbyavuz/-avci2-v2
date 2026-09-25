#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, sqlite3
from binance_notify import resolve_chat_id, send_telegram

DB=os.getenv("HISTORY_DB","history_miner.db")
def main():
    token=(os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_cfg=(os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not os.path.exists(DB): return
    with sqlite3.connect(DB) as c:
        c.row_factory=sqlite3.Row
        run=c.execute("""SELECT attempted_pairs,recovered_pairs,no_archive_pairs,error_pairs,
            recovered_bars,queued_pairs FROM survivorship_runs
            ORDER BY started_utc DESC LIMIT 1""").fetchone()
        collapsed=0
        try:
            collapsed=c.execute("SELECT COUNT(*) FROM collapsed_survivors").fetchone()[0]
        except sqlite3.Error:
            pass
    if not run: return
    text=(
      "🧱 Backfill | çalışıyor\n"
      f"• Bu tur: {run['attempted_pairs']} denendi | {run['recovered_pairs']} kurtarıldı | "
      f"{run['no_archive_pairs']} arşiv yok/kısa | {run['error_pairs']} hata\n"
      f"• Kurtarılan mum: {run['recovered_bars']:,} | sırada: {run['queued_pairs']}\n"
      f"• Çökmüş survivor sınıfı: {collapsed}"
    )
    chat=resolve_chat_id(token,chat_cfg,DB,"History Backfill")
    send_telegram(token,chat,text)
if __name__=="__main__": main()
