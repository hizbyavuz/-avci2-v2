#!/usr/bin/env python3
import os
import sqlite3
import requests


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("Telegram secrets absent; notification skipped")
        return
    conn = sqlite3.connect("binance_avci2.db")
    conn.row_factory = sqlite3.Row
    scan = conn.execute("SELECT * FROM scans ORDER BY scan_time_utc DESC LIMIT 1").fetchone()
    candidates = conn.execute(
        """SELECT symbol, stage, score, validation_tier FROM signal_events
           WHERE event_class='CANDIDATE' AND signal_time_utc=?
           ORDER BY score DESC""", (scan["scan_time_utc"],)
    ).fetchall() if scan else []
    lines = ["Binance Avci 2", f"Health: {scan['health_status']}",
             f"BTC regime: {scan['btc_regime']}", f"Universe: {scan['universe_size']}"]
    lines += [f"{r['symbol']} | {r['stage']} | {r['score']} | {r['validation_tier']}"
              for r in candidates]
    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": "\n".join(lines)}, timeout=20,
    )
    response.raise_for_status()
    print("Telegram notification sent")


if __name__ == "__main__":
    main()
