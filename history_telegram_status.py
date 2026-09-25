#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import hashlib
import os
import sqlite3
from datetime import datetime, timezone

from binance_notify import resolve_chat_id, send_telegram

DB=os.getenv("HISTORY_DB","history_miner.db")
VERSION="history-v5-activation-v0.4-20260925"

def main():
    if not os.path.exists(DB):
        print("History status: DB yok")
        return
    token=(os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    configured=(os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    if not token:
        print("History status: Telegram token yok")
        return

    with sqlite3.connect(DB,timeout=20) as c:
        c.row_factory=sqlite3.Row
        c.execute("""CREATE TABLE IF NOT EXISTS telegram_status_state(
            channel TEXT PRIMARY KEY,
            fingerprint TEXT NOT NULL,
            sent_utc TEXT NOT NULL
        )""")

        run=c.execute("""SELECT discovery_total,discovery_done,validation_total,validation_done,
            spec_frozen FROM v5_runs WHERE version=? ORDER BY started_utc DESC LIMIT 1""",
            (VERSION,)).fetchone()
        if not run:
            print("History status: V5 run yok")
            return

        good=c.execute("""SELECT COUNT(*) FROM v5_activation_snapshots
            WHERE version=? AND status='DONE'""",(VERSION,)).fetchone()[0]
        err=c.execute("""SELECT COUNT(*) FROM v5_activation_snapshots
            WHERE version=? AND status='ERROR'""",(VERSION,)).fetchone()[0]

        best=c.execute("""SELECT pattern_name,activation_name,horizon_days,threshold_pct,
            signal_n,hit_n,precision,lift_vs_parent,q_value,fdr_pass
            FROM v5_activation_results
            WHERE version=? AND split='VALIDATION' AND activation_name!='BASE'
              AND signal_n>=20
            ORDER BY fdr_pass DESC, COALESCE(q_value,1), COALESCE(lift_vs_parent,0) DESC
            LIMIT 1""",(VERSION,)).fetchone()

        totals=c.execute("""SELECT
            (SELECT COUNT(*) FROM cex_pairs),
            (SELECT COUNT(*) FROM cex_pairs WHERE status='DONE'),
            (SELECT COUNT(*) FROM daily_bars),
            (SELECT COUNT(*) FROM rise_events)""").fetchone()

        dtotal,ddone,vtotal,vdone,spec=map(int,[run["discovery_total"],run["discovery_done"],
                                               run["validation_total"],run["validation_done"],
                                               run["spec_frozen"] or 0])
        pairs,done,bars,events=map(int,totals)

        parts=[
            f"d={ddone}/{dtotal}",
            f"v={vdone}/{vtotal}",
            f"good={good}",
            f"err={err}",
            f"pairs={done}/{pairs}",
            f"bars={bars}",
            f"events={events}",
            f"spec={spec}",
        ]
        if best:
            parts += [f"{best['pattern_name']}:{best['activation_name']}:{best['horizon_days']}:{best['threshold_pct']}",
                      f"n={best['signal_n']}",f"hit={best['hit_n']}",
                      f"p={float(best['precision']):.6f}",
                      f"lift={float(best['lift_vs_parent']):.6f}",
                      f"q={best['q_value']}",f"fdr={best['fdr_pass']}"]
        fp=hashlib.sha256("|".join(parts).encode()).hexdigest()

        prev=c.execute("SELECT fingerprint FROM telegram_status_state WHERE channel='history-v5'").fetchone()
        if prev and prev[0]==fp:
            print("History status unchanged; Telegram skipped")
            return

        lines=[
            "⛏ GEÇMİŞ KAZICI | KISA DURUM",
            f"• Arşiv: {done}/{pairs} parite | {bars:,} mum | {events:,} olay",
            f"• V5: keşif {ddone}/{dtotal} | doğrulama {vdone}/{vtotal}",
            f"• Saatlik veri: {good} hazır | {err} eksik/hata",
        ]
        if best:
            p="P2" if best["pattern_name"]=="P2_FAR_PLUS_VOL" else "P3"
            act={"VOL":"hacim","DIP":"dipten ayrışma","VOL_DIP":"hacim+dip"}.get(best["activation_name"],best["activation_name"])
            fdr="FDR geçti" if int(best["fdr_pass"] or 0) else "FDR geçmedi"
            q="—" if best["q_value"] is None else f"{float(best['q_value']):.3g}"
            lines.append(
                f"• En güçlü V5: {p}+{act} | {best['horizon_days']}g +%{best['threshold_pct']} "
                f"| başarı %{100*float(best['precision']):.1f} | lift {float(best['lift_vs_parent']):.2f}x "
                f"| q={q} {fdr}"
            )
        lines.append("• Değişiklik yoksa tekrar mesaj gönderilmeyecek.")

        chat=resolve_chat_id(token,configured,DB,"History Miner")
        send_telegram(token,chat,"\n".join(lines))
        c.execute("""INSERT INTO telegram_status_state(channel,fingerprint,sent_utc)
            VALUES('history-v5',?,?)
            ON CONFLICT(channel) DO UPDATE SET fingerprint=excluded.fingerprint,sent_utc=excluded.sent_utc""",
            (fp,datetime.now(timezone.utc).isoformat()))
        c.commit()
        print("Short History status sent")

if __name__=="__main__":
    main()
