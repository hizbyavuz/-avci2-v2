#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One consolidated Telegram message for the Binance early-signal engine."""

import json
import os
import sqlite3

from binance_notify import resolve_chat_id, send_telegram

DB = os.getenv("BINANCE_DB", "binance_avci2.db")


def table(c, t):
    return c.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)
    ).fetchone() is not None


def j(s):
    try:
        return json.loads(s or "{}")
    except Exception:
        return {}


def f(v, d=2):
    try:
        return f"{float(v):.{d}f}"
    except Exception:
        return "-"


def main():
    if not os.path.exists(DB):
        print("Binance master: DB yok")
        return

    with sqlite3.connect(DB, timeout=30) as c:
        c.row_factory = sqlite3.Row
        scan = c.execute(
            "SELECT * FROM scans WHERE health_status='VALID' "
            "ORDER BY scan_time_utc DESC LIMIT 1"
        ).fetchone()
        if not scan:
            print("Binance master: valid scan yok")
            return

        ts = scan["scan_time_utc"]
        counts = {
            r["selection_class"]: r["n"]
            for r in c.execute("""SELECT selection_class,COUNT(*) n FROM features
                WHERE scan_time_utc=? GROUP BY selection_class""", (ts,))
        }

        events = c.execute("""SELECT * FROM signal_events
            WHERE signal_time_utc=? AND event_class='CANDIDATE'
            ORDER BY score DESC LIMIT 5""", (ts,)).fetchall()

        audit = (
            c.execute(
                "SELECT * FROM validation_audit_runs "
                "ORDER BY audited_at_utc DESC LIMIT 1"
            ).fetchone()
            if table(c, "validation_audit_runs") else None
        )

        rows = []
        for e in events:
            feat = c.execute("""SELECT * FROM features
                WHERE scan_time_utc=? AND symbol=?
                ORDER BY is_selected DESC LIMIT 1""", (ts, e["symbol"])).fetchone()
            flow = (
                c.execute(
                    "SELECT * FROM flow_observations WHERE event_id=?",
                    (e["event_id"],),
                ).fetchone()
                if table(c, "flow_observations") else None
            )
            st = (
                c.execute("""SELECT * FROM structure_observations
                    WHERE scan_time_utc=? AND symbol=?
                    ORDER BY version DESC LIMIT 1""", (ts, e["symbol"])).fetchone()
                if table(c, "structure_observations") else None
            )
            op = (
                c.execute("""SELECT * FROM opportunity_observations
                    WHERE scan_time_utc=? AND symbol=?
                    ORDER BY version DESC LIMIT 1""", (ts, e["symbol"])).fetchone()
                if table(c, "opportunity_observations") else None
            )
            wb = (
                c.execute("""SELECT * FROM winner_bridge_scores
                    WHERE scan_time_utc=? AND symbol=?
                    ORDER BY id DESC LIMIT 1""", (ts, e["symbol"])).fetchone()
                if table(c, "winner_bridge_scores") else None
            )
            fb = (
                c.execute("""SELECT * FROM deriv_fallback_observations
                    WHERE scan_time_utc=? AND symbol=?
                    ORDER BY created_at_utc DESC LIMIT 1""",
                    (ts, e["symbol"])).fetchone()
                if table(c, "deriv_fallback_observations") else None
            )
            rows.append((e, feat, flow, st, op, wb, fb))

        full = c.execute("""SELECT COUNT(*) FROM raw_derivs
            WHERE scan_time_utc=? AND data_mode!='SPOT_ONLY'""", (ts,)).fetchone()[0]
        ob = c.execute(
            "SELECT COUNT(*) FROM orderbook_snap WHERE scan_time_utc=?", (ts,)
        ).fetchone()[0]
        fb_ok = (
            c.execute("""SELECT COUNT(*) FROM deriv_fallback_observations
                WHERE scan_time_utc=? AND status='OK'""", (ts,)).fetchone()[0]
            if table(c, "deriv_fallback_observations") else 0
        )

    lines = [
        "🛰 BINANCE ERKEN SİNYAL MOTORU | TEK RAPOR",
        f"• Veri sağlığı: {scan['health_status']} | mod: {scan['data_mode']} "
        f"| evren: {scan['universe_size']}",
        f"• BTC rejimi: {scan['btc_regime']} | 24s %{scan['btc_change_24h']:+.2f}",
        f"• Candidate: {counts.get('CANDIDATE',0)} | "
        f"near-miss: {counts.get('NEAR_MISS',0)} | "
        f"random: {counts.get('RANDOM_CONTROL',0)}",
        f"• Order-book snapshot: {ob} | Binance native derivatives dolu: {full} "
        f"| OKX fallback: {fb_ok}",
    ]

    lines.append("\n🚨 ERKEN SİNYALLER")
    if not rows:
        lines.append("• Bu tur temiz Candidate yok. 0 aday geçerli sonuçtur.")

    for e, x, flow, st, op, wb, fb in rows[:3]:
        if not x:
            continue
        lines.append(
            f"• {e['symbol']} | {x['stage']} / {x['engine']} | skor {x['score']}"
        )
        lines.append(
            f"  Fiyat: 15dk %{x['change_15m']:+.2f} | 1s %{x['change_1h']:+.2f} "
            f"| 24s %{x['change_24h']:+.2f} | BTC'ye göre {x['btc_relative_24h']:+.2f}"
        )
        lines.append(
            f"  Wake: vol-z {f(x['volume_z_15m'])}, trade-z {f(x['trade_z_15m'])}, "
            f"ret-z {f(x['return_z_15m'])} | retention {f(x['retention_proxy'])} "
            f"| taker-buy {f((x['taker_buy_ratio_15m'] or 0)*100,1)}% "
            f"| rarity {f(x['cross_sectional_rarity_pct'],1)}"
        )

        if flow:
            tape = j(flow["tape_json"])
            lines.append(
                f"  Akış: book {f(flow['book_imbalance'])} Δ {f(flow['imbalance_change'])} "
                f"| büyük alış payı {f((tape.get('large_buy_share') or 0)*100,1)}% "
                f"| taker net USD {f(tape.get('net_taker_notional'),0)}"
            )

        if x["oi_change_1h_pct"] is not None or x["funding_rate"] is not None:
            lines.append(
                f"  Türev(Binance): OI 1s %{f(x['oi_change_1h_pct'])} "
                f"| funding {f(x['funding_rate'],6)}"
            )
        elif fb and fb["status"] == "OK":
            lines.append(
                f"  Türev proxy(OKX, Binance değil): OI Δ %{f(fb['oi_change_since_prev_pct'])} "
                f"| funding {f(fb['funding_rate'],6)} | basis %{f(fb['basis_pct'],3)}"
            )
        else:
            lines.append("  Türev: OI/funding alınamadı; UNKNOWN bırakıldı.")

        if st:
            flags = j(st["structure_flags_json"])
            lines.append(
                f"  Yapı: sector {st['sector'] or '-'} | OI-z {f(st['oi_anomaly_z'])} "
                f"| book ratio {f(st['book_bid_ask_ratio'])} | "
                f"flags {','.join(flags) if isinstance(flags,list) else '-'}"
            )

        if op:
            flags = j(op["flags_json"])
            lines.append(
                f"  Fırsat: leader rank {op['leader_rank'] or '-'} "
                f"| accel {f(op['acceleration_ratio'])} | silent {op['silent_accumulation']} "
                f"| flags {','.join(flags) if isinstance(flags,list) else '-'}"
            )

        if wb:
            lines.append(
                f"  Geçmiş winner benzerliği: %{f(wb['winner_similarity_pct'],1)} "
                f"| sınıf {wb['classification']} "
                f"| örnek {wb['winner_sample_count']}/{wb['control_sample_count']}"
            )

    lines.append("\n📡 VERİ SAĞLIĞI")
    if audit:
        lines.append(
            f"• Event: {audit['total_events']} | SPOT_ONLY {audit['spot_only_events']} "
            f"| FULL {audit['full_data_events']} | erken giriş ihlali "
            f"{audit['entry_timing_violations']} | kontrolsüz candidate scan "
            f"{audit['unmatched_candidate_scans']}"
        )
    lines.append(
        "• Binance-native türev verisi yoksa OKX yalnızca cross-venue proxy olarak "
        "kullanılır; Binance verisi diye gösterilmez."
    )
    lines.append(
        "• Spot akışı + order-book + market structure + fırsat/missed-mover + "
        "geçmiş winner bridge birlikte okunuyor."
    )
    lines.append("• Bu erken araştırma sinyalidir; otomatik emir açmaz.")

    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    cfg = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    if not token:
        print("Binance master: Telegram token yok")
        return
    chat = resolve_chat_id(token, cfg, DB, "Binance Motor")
    send_telegram(token, chat, "\n".join(lines)[:3900])
    print("Binance master Telegram sent")


if __name__ == "__main__":
    main()
