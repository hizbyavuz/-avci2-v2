#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Trader-facing Gate Web3 Telegram summary.

Security remains fail-closed. A token is never shown as trade-ready unless
existing trade-readiness says PAPER_ELIGIBLE, security has no hard veto, and
execution quality is GOOD. Weighted discovery stays watch-only.
"""
import json, os, sqlite3
from binance_notify import resolve_chat_id, send_telegram

DB=os.getenv("AVCI_DB","avci2.db")

def table(c,t):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone() is not None

def arr(s):
    try:
        x=json.loads(s or "[]")
        return x if isinstance(x,list) else []
    except Exception:
        return []

def money(v):
    try:
        x=float(v)
        if x>=1_000_000:return "$"+f"{x/1_000_000:.1f}M"
        if x>=1_000:return "$"+f"{x/1_000:.0f}K"
        return "$"+f"{x:.0f}"
    except Exception:
        return "-"

def symbol_for(c,network,contract):
    if table(c,"snapshots"):
        r=c.execute("""SELECT raw_json FROM snapshots
            WHERE network_id=? AND token_contract=? ORDER BY id DESC LIMIT 1""",
            (network,contract)).fetchone()
        if r:
            try:
                x=json.loads(r[0] or "{}")
                return x.get("symbol") or x.get("name") or contract[:8]
            except Exception:
                pass
    return contract[:8]

def main():
    if not os.path.exists(DB):
        print("Gate trader message: DB yok"); return
    with sqlite3.connect(DB,timeout=30) as c:
        c.row_factory=sqlite3.Row
        health=c.execute("""SELECT * FROM gate_scan_health WHERE status='VALID'
            ORDER BY scan_ts DESC LIMIT 1""").fetchone()
        if not health:
            print("Gate trader message: valid scan yok"); return
        batch=health["batch_id"]

        ready=[]
        if table(c,"gate_candidate_evidence") and table(c,"trade_readiness") and table(c,"gate_security_confidence_history"):
            ready=c.execute("""SELECT e.*,tr.execution_quality,
                       s.label security_label,s.hard_veto
                FROM gate_candidate_evidence e
                JOIN trade_readiness tr
                  ON tr.source='GATE' AND tr.batch_key=e.batch_id
                 AND tr.asset_key=e.token_contract
                JOIN gate_security_confidence_history s
                  ON s.batch_id=e.batch_id AND s.network_id=e.network_id
                 AND s.token_contract=e.token_contract
                WHERE e.batch_id=?
                  AND tr.readiness='PAPER_ELIGIBLE'
                  AND tr.execution_quality='GOOD'
                  AND s.hard_veto=0
                  AND s.label IN ('STRONG','MEDIUM')
                ORDER BY e.evidence_count DESC,e.counter_count ASC
                LIMIT 2""",(batch,)).fetchall()

        watch=[]
        if table(c,"gate_weighted_discovery"):
            watch=c.execute("""SELECT * FROM gate_weighted_discovery
                WHERE batch_id=? AND status='SAFE_DISCOVERY'
                  AND score>=55 AND change_24h BETWEEN -5 AND 40
                ORDER BY score DESC,liquidity DESC LIMIT 2""",(batch,)).fetchall()

        lines=["🛰 GATE WEB3 AVCI",
               f"• İzlenen token: {health['observed_tokens']} | güvenlik: FAIL-CLOSED",
               ""]
        if ready:
            for r in ready:
                name=symbol_for(c,r["network_id"],r["token_contract"])
                obs=c.execute("""SELECT change_24h,liquidity,buys_5m,sells_5m,own_volume_ratio
                    FROM gate_early_observations WHERE batch_id=? AND network_id=? AND token_contract=? LIMIT 1""",
                    (batch,r["network_id"],r["token_contract"])).fetchone() if table(c,"gate_early_observations") else None
                lines.append(f"🟢 GÜVENLİ İŞLEM-HAZIR — {name} [{r['network_id']}]")
                if obs:
                    b=float(obs["buys_5m"] or 0); sv=float(obs["sells_5m"] or 0)
                    flow=(b/max(sv,1.0)) if b+sv else 0
                    vr="-" if obs["own_volume_ratio"] is None else f"{float(obs['own_volume_ratio']):.1f}x"
                    lines.append(f"• 24s %{float(obs['change_24h'] or 0):+.1f} | likidite {money(obs['liquidity'])} | hacim anomalisi {vr}")
                    lines.append(f"• 5dk alıcı/satıcı: {flow:.1f}x")
                sup=arr(r["support_json"])
                if sup: lines.append("• Neden: "+"; ".join(sup[:2]))
                lines.append(f"• Güvenlik: {r['security_label']} | çıkış testi: geçti")
                lines.append("")
        else:
            lines.append("🚫 Bu tur güvenli işlem-hazır aday yok.")
            lines.append("")

        if watch:
            lines.append("🟡 ERKEN İZLEME — İŞLEM ADAYI DEĞİL")
            for r in watch:
                ev=arr(r["evidence_json"])
                name=r["symbol"] or r["token_contract"][:8]
                why="; ".join(ev[:2]) if ev else "erken aktivite"
                lines.append(f"• {name} [{r['network_id']}]: puan {float(r['score']):.0f}/100 | 24s %{float(r['change_24h']):+.1f} | {why}")
            lines.append("")

        lines.append("Not: Hard-veto, satılabilirlik veya güvenlik sorunu olan token burada aday gösterilmez.")

    msg="\n".join(lines)
    token=(os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        print(msg); return
    chat=resolve_chat_id(token,(os.getenv("TELEGRAM_CHAT_ID") or "").strip(),DB,"Gate Web3 Motor")
    send_telegram(token,chat,msg[:3900])
    print("Gate trader Telegram sent")

if __name__=="__main__":
    main()
