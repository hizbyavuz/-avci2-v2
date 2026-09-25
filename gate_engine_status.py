#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One consolidated Telegram message for the Gate Web3 engine."""

import json
import os
import sqlite3
from pathlib import Path

from binance_notify import resolve_chat_id, send_telegram
from gate_notify import pending_alerts

DB = os.getenv("AVCI_DB", "avci2.db")
VAL = os.getenv("AVCI_VALIDATION_DB", "avci_validation_v5.db")
HISTORY = os.getenv("HISTORY_DB", ".history-state/history_miner.db")
TWIN = os.getenv("TWIN_DB", ".history-twin/history_miner.db")


def fnum(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fmt(v, d=1):
    return "-" if v is None else f"{v:.{d}f}"


def exists_table(c, t):
    return c.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)
    ).fetchone() is not None


def json_obj(s):
    try:
        return json.loads(s or "{}")
    except Exception:
        return {}


def latest_history_line():
    if not Path(HISTORY).exists():
        return "Geçmiş bağlantısı: veri yok"
    try:
        with sqlite3.connect(f"file:{HISTORY}?mode=ro", uri=True) as c:
            c.row_factory = sqlite3.Row
            best = c.execute("""SELECT pattern_name,activation_name,horizon_days,threshold_pct,
                signal_n,precision,lift_vs_parent,q_value,fdr_pass
                FROM v5_activation_results WHERE split='VALIDATION' AND activation_name!='BASE'
                ORDER BY fdr_pass DESC,COALESCE(q_value,1),COALESCE(lift_vs_parent,0) DESC LIMIT 1""").fetchone()
            twin = None
        if Path(TWIN).exists():
            try:
                with sqlite3.connect(f"file:{TWIN}?mode=ro", uri=True) as tc:
                    tc.row_factory = sqlite3.Row
                    if exists_table(tc, "twin_results"):
                        twin = tc.execute("""SELECT pattern_name,target_pct,feature,pair_n,winner_median,
                            near_median,paired_effect FROM twin_results
                            WHERE split='VALIDATION' AND pair_n>=20
                            ORDER BY ABS(paired_effect) DESC LIMIT 1""").fetchone()
            except Exception:
                twin = None
        parts = []
        if best:
            parts.append(
                f"V5 {best['pattern_name'].split('_')[0]}+{best['activation_name']} "
                f"{best['horizon_days']}g +%{best['threshold_pct']}: "
                f"%{100*best['precision']:.1f}, {best['lift_vs_parent']:.2f}x, "
                f"q={fmt(best['q_value'],3)}"
            )
        if twin:
            parts.append(
                f"Twin {twin['feature']}: winner {fmt(twin['winner_median'],2)} / "
                f"benzer kaybeden {fmt(twin['near_median'],2)} (n={twin['pair_n']})"
            )
        return " | ".join(parts) if parts else "Geçmiş bağlantısı: sonuç yok"
    except Exception as exc:
        return f"Geçmiş bağlantısı: okunamadı ({type(exc).__name__})"


def main():
    if not os.path.exists(DB) or not os.path.exists(VAL):
        print("Gate master status: DB eksik")
        return

    # Populate primary candidate audit decisions without sending individual alerts.
    try:
        pending_alerts(DB, VAL)
    except Exception as exc:
        print("Gate candidate audit warning", type(exc).__name__)

    with sqlite3.connect(DB, timeout=30) as c, sqlite3.connect(
        f"file:{VAL}?mode=ro", uri=True
    ) as v:
        c.row_factory = v.row_factory = sqlite3.Row
        health = c.execute(
            "SELECT * FROM gate_scan_health ORDER BY scan_ts DESC LIMIT 1"
        ).fetchone()
        if not health:
            print("Gate master status: scan yok")
            return
        batch = health["batch_id"]

        early = c.execute("""SELECT COUNT(*) n,SUM(observed_anomaly) a,
            AVG(liquidity) liq,AVG(volume_5m) v5
            FROM gate_early_observations WHERE batch_id=?""", (batch,)).fetchone()

        buyers = (
            c.execute("""SELECT COUNT(*) n,AVG(buyers_5m) b5,AVG(buyers_1h) b1
                FROM gate_buyer_observations WHERE batch_id=?""", (batch,)).fetchone()
            if exists_table(c, "gate_buyer_observations") else None
        )

        events = v.execute(
            "SELECT * FROM validation_events WHERE batch_id=? ORDER BY id", (batch,)
        ).fetchall()
        candidates = [
            e for e in events
            if e["group_type"] in ("CANDIDATE", "EXPANDED_CANDIDATE")
        ]
        near = sum(e["group_type"] == "NEAR_MISS" for e in events)
        rnd = sum(e["group_type"] == "RANDOM_CONTROL" for e in events)

        btc = (
            c.execute("SELECT * FROM gate_btc_context WHERE batch_id=?", (batch,)).fetchone()
            if exists_table(c, "gate_btc_context") else None
        )

        engines = []
        for table_name, label in (
            ("gate_activity_alert_audit", "ACTIVITY"),
            ("gate_volume_alert_audit", "VOLUME"),
            ("gate_cross_venue_alert_audit", "CROSS"),
        ):
            if exists_table(c, table_name):
                for r in c.execute(
                    f"""SELECT network_id,token_contract,status,reason,message
                        FROM {table_name} WHERE batch_id=? AND status='PENDING'""",
                    (batch,),
                ):
                    engines.append((label, dict(r)))

        if exists_table(c, "gate_spot_bridge_audit"):
            for r in c.execute("""SELECT network_id,token_contract,status,reason,message
                FROM gate_spot_bridge_audit WHERE status='PENDING'
                ORDER BY decided_at DESC LIMIT 3"""):
                engines.append(("SPOT_BRIDGE", dict(r)))

        details = []
        for e in candidates[:5]:
            snap = c.execute("""SELECT raw_json FROM snapshots
                WHERE network_id=? AND token_contract=? AND zaman_utc>=?
                ORDER BY id ASC LIMIT 1""",
                (e["network_id"], e["token_contract"], e["signal_iso"])).fetchone()
            item = json_obj(snap[0]) if snap else {}
            obs = c.execute("""SELECT own_volume_ratio,buys_5m,sells_5m,volume_5m,
                    liquidity,change_24h FROM gate_early_observations
                WHERE batch_id=? AND network_id=? AND token_contract=? LIMIT 1""",
                (batch, e["network_id"], e["token_contract"])).fetchone()
            wi = (
                c.execute("""SELECT * FROM gate_wallet_intelligence
                    WHERE batch_id=? AND network_id=? AND token_contract=?""",
                    (batch, e["network_id"], e["token_contract"])).fetchone()
                if exists_table(c, "gate_wallet_intelligence") else None
            )
            sec = (
                c.execute("""SELECT label,coverage_pct,pass_rate_pct,hard_veto
                    FROM gate_security_confidence_history
                    WHERE batch_id=? AND network_id=? AND token_contract=?
                    ORDER BY scan_ts DESC LIMIT 1""",
                    (batch, e["network_id"], e["token_contract"])).fetchone()
                if exists_table(c, "gate_security_confidence_history") else None
            )
            details.append((e, item, obs, wi, sec))

        candidate_keys = {
            (e["network_id"], str(e["token_contract"]).lower()) for e in candidates
        }
        extra = []
        for label, r in engines:
            key = (r["network_id"], str(r["token_contract"]).lower())
            if key not in candidate_keys:
                extra.append((label, r))

        wallet_rows = (
            c.execute("""SELECT COUNT(*) n,
                SUM(CASE WHEN status='OK' THEN 1 ELSE 0 END) ok,
                SUM(sybil_proxy) syb FROM gate_wallet_intelligence
                WHERE batch_id=?""", (batch,)).fetchone()
            if exists_table(c, "gate_wallet_intelligence") else None
        )
        source_errors = str(health["source_errors"] or "")

    lines = [
        "🛰 GATE WEB3 MOTOR | TEK RAPOR",
        f"• Veri sağlığı: {health['status']} | gözlenen token: {health['observed_tokens']} "
        f"| erken anomali: {int(early['a'] or 0)}/{early['n']}",
        f"• V5/expanded aday: {len(candidates)} | near-miss: {near} | random: {rnd}",
    ]
    if btc:
        lines.append(
            f"• BTC: 15dk %{btc['btc_15m_pct']:+.2f} | "
            f"1s %{btc['btc_1h_pct']:+.2f} | rejim {btc['btc_regime']}"
        )
    if buyers and buyers["n"]:
        lines.append(
            f"• Buyer akışı: {buyers['n']} ölçüm | ort. 5dk buyer {fmt(buyers['b5'])} "
            f"| 1s {fmt(buyers['b1'])}"
        )
    if wallet_rows:
        lines.append(
            f"• Wallet graph: {wallet_rows['ok'] or 0}/{wallet_rows['n']} Helius hazır "
            f"| sybil-proxy: {wallet_rows['syb'] or 0}"
        )

    lines.append("\n🚨 ERKEN SİNYALLER")
    if not details and not extra:
        lines.append("• Bu tur temiz/izlenebilir erken sinyal yok. 0 aday geçerli sonuçtur.")

    for e, item, obs, wi, sec in details[:3]:
        sym = item.get("symbol") or item.get("name") or e["token_contract"][:8]
        rules = e["rulesets"] or "HICBIRI"
        parts = [f"• {sym} [{e['network_id']}] {rules}"]
        if obs:
            parts.append(
                f"24s %{obs['change_24h']:+.1f}, vol-x {fmt(obs['own_volume_ratio'],1)}, "
                f"5dk {int(obs['buys_5m'] or 0)}/{int(obs['sells_5m'] or 0)} al/sat"
            )
        holder = item.get("adjusted_holder") or {}
        lp = item.get("lp_protection") or {}
        q1 = (
            item.get("exit_1k") if e["network_id"] == "solana"
            else item.get("evm_exit_1k")
        ) or {}
        parts.append(
            f"güvenlik {sec['label'] if sec else item.get('risk_band','?')} | "
            f"top10 %{fmt(fnum(holder.get('top10_pct')),0)} | "
            f"LP %{fmt(fnum(lp.get('protected_pct')),0)} | "
            f"$1k çıkış kaybı %{fmt(fnum(q1.get('loss_pct')),1)}"
        )
        if wi:
            parts.append(
                f"wallet yaş medyan {fmt(wi['wallet_age_median_days'],1)}g | "
                f"fresh<30g %{fmt((wi['fresh_30d_ratio'] or 0)*100,0)} | "
                f"ortak funder %{fmt((wi['common_funder_ratio'] or 0)*100,0)} | "
                f"sybil-proxy {'EVET' if wi['sybil_proxy'] else 'hayır'}"
            )
        lines.append("\n  ".join(parts))

    for label, r in extra[:2]:
        lines.append(
            f"• {label}: {r['network_id']} {str(r['token_contract'])[:12]}… | "
            "V5 dışında ayrı erken aktivasyon izi, güvenlik kapısından geçmiş."
        )

    lines.append("\n🧮 ARAŞTIRMA MATEMATİĞİ")
    math_rows = []
    if exists_table(c, "gate_activation_math_results"):
        math_rows = c.execute("""SELECT combo_label,target_pct,selected_n,selected_rate,
            baseline_n,baseline_rate,lift,q_value FROM gate_activation_math_results
            WHERE split='VALIDATION' ORDER BY CASE WHEN q_value IS NULL THEN 1 ELSE 0 END,
            q_value ASC,lift DESC LIMIT 3""").fetchall()
    if math_rows:
        r = math_rows[0]
        lift_txt = "-" if r["lift"] is None else f"{r['lift']:.2f}x"
        q_txt = "-" if r["q_value"] is None else f"{r['q_value']:.3f}"
        lines.append(
            f"• Canlı motoru ETKİLEMEZ. En iyi frozen OOS araştırma sonucu: "
            f"{r['combo_label']} | +%{r['target_pct']} | n={r['selected_n']} | "
            f"lift {lift_txt} | q={q_txt}"
        )
        lines.append("• Geçerse yalnızca sonraki sürüm (V5.1/V6) için kural adayı olur.")
    else:
        lines.append("• Frozen OOS doğrulama için kapanmış olay örneklemi henüz yetersiz.")

    lines.append("\n🧬 GEÇMİŞ MATEMATİĞİ")
    lines.append("• " + latest_history_line())

    lines.append("\n📡 VERİ KAPSAMI")
    lines.append(
        "• GeckoTerminal + Gate Spot + Jupiter/0x + GoPlus + Helius (varsa) + "
        "LP/holder + buyer/liquidity + BTC ayrışma + cross-venue birlikte okunuyor."
    )
    if source_errors and source_errors not in ("[]", "{}", "None"):
        lines.append(f"• Kaynak hataları: {source_errors[:350]}")
    if not os.getenv("HELIUS_API_KEY"):
        lines.append(
            "• Helius anahtarı yoksa wallet-age/funding graph eksik kalır; "
            "sinyalde bu alan UNKNOWN tutulur."
        )
    lines.append("• Bu rapor erken araştırma sinyalidir; otomatik emir açmaz.")

    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    cfg = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    if not token:
        print("Gate master status: Telegram token yok")
        return
    chat = resolve_chat_id(token, cfg, DB, "Gate Web3 Motor")
    send_telegram(token, chat, "\n".join(lines)[:3900])
    print("Gate master Telegram sent")


if __name__ == "__main__":
    main()
