#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, sqlite3
from binance_notify import resolve_chat_id, send_telegram

DB=os.getenv("BINANCE_DB","binance_avci2.db")

def table_exists(c, name):
    return c.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,)
    ).fetchone() is not None

def main():
    token=(os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_cfg=(os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not os.path.exists(DB):
        return
    with sqlite3.connect(DB) as c:
        c.row_factory=sqlite3.Row
        scan=c.execute(
            "SELECT scan_time_utc,health_status,data_mode FROM scans "
            "ORDER BY scan_time_utc DESC LIMIT 1"
        ).fetchone()
        rows=c.execute(
            """SELECT COALESCE(NULLIF(event_class,''),'UNKNOWN') cls,COUNT(*) n
               FROM flow_observations
               GROUP BY COALESCE(NULLIF(event_class,''),'UNKNOWN')"""
        ).fetchall()
        total=c.execute("SELECT COUNT(*) FROM flow_observations").fetchone()[0]

        audit=None
        if table_exists(c, "validation_audit_runs"):
            audit=c.execute(
                """SELECT * FROM validation_audit_runs
                   ORDER BY id DESC LIMIT 1"""
            ).fetchone()

    if not scan:
        return

    parts=", ".join(f"{r['cls']}={r['n']}" for r in rows) or "henüz yok"
    extra=""
    if audit:
        extra=(
            "\n🧪 Doğrulama kontrolü"
            f"\n• UNKNOWN: {audit['flow_unknown_before']} → {audit['flow_unknown_after']}"
            f" | orphan={audit['flow_orphan_unknown']}"
            f"\n• Cooldown ihlali: {audit['cooldown_violations']}"
            f" | erken giriş ihlali: {audit['entry_timing_violations']}"
            f"\n• Kontrolsüz candidate taraması: {audit['unmatched_candidate_scans']}"
            f"\n• Event veri modu: SPOT_ONLY={audit['spot_only_events']}"
            f" | FULL={audit['full_data_events']}"
        )

    text=(
      "💸 Money Flow | çalışıyor\n"
      f"• Son tarama: {scan['health_status']} | {scan['data_mode']}\n"
      f"• Akış gözlemi toplam: {total}\n"
      f"• Dağılım: {parts}"
      f"{extra}"
    )
    chat=resolve_chat_id(token,chat_cfg,DB,"Money Flow")
    send_telegram(token,chat,text)

if __name__=="__main__":
    main()
