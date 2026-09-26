#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json, os, sqlite3
from binance_notify import resolve_chat_id, send_telegram

DB=os.getenv("AVCI_DB","avci2.db")
VAL=os.getenv("AVCI_VALIDATION_DB","avci_validation_v5.db")

def table(c,t):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone() is not None

def arr(s):
    try:
        x=json.loads(s or "[]")
        return x if isinstance(x,list) else []
    except Exception:
        return []

def obj(s):
    try: return json.loads(s or "{}")
    except Exception: return {}

def pct(v):
    return "-" if v is None else f"%{100*float(v):.0f}"

def early_dot(obs):
    if not obs:
        return "🟢"
    vr=obs["own_volume_ratio"]
    buys=float(obs["buys_5m"] or 0)
    sells=float(obs["sells_5m"] or 0)
    day=float(obs["change_24h"] or 0)
    volume_ok=vr is not None and float(vr)>=2.0
    buyers_ok=(buys+sells)>=5 and buys>=1.3*max(sells,1)
    price_early=(-2<=day<=20)
    score=sum((volume_ok,buyers_ok,price_early))
    if score==3:
        return "🔴"
    if score>=2:
        return "🟡"
    return "🟢"

def main():
    if not os.path.exists(DB) or not os.path.exists(VAL):
        print("Gate human report: DB eksik"); return
    with sqlite3.connect(DB,timeout=30) as c, sqlite3.connect(VAL,timeout=30) as v:
        c.row_factory=v.row_factory=sqlite3.Row
        health=c.execute("""SELECT * FROM gate_scan_health WHERE status='VALID'
            ORDER BY scan_ts DESC LIMIT 1""").fetchone()
        if not health:
            print("Gate human report: scan yok"); return
        batch=health["batch_id"]
        evrows=[]
        if table(c,"gate_candidate_evidence"):
            evrows=c.execute("""SELECT * FROM gate_candidate_evidence
                WHERE batch_id=? ORDER BY CASE summary
                    WHEN 'DAYANAK_COK_GUCLU' THEN 0 WHEN 'DAYANAK_GUCLU' THEN 1
                    WHEN 'DAYANAK_ORTA' THEN 2 ELSE 3 END,
                    evidence_count DESC,counter_count ASC LIMIT 5""",(batch,)).fetchall()
        events={r["id"]:r for r in v.execute("""SELECT * FROM validation_events
            WHERE batch_id=?""",(batch,)).fetchall()}
        btc=c.execute("SELECT * FROM gate_btc_context WHERE batch_id=?",(batch,)).fetchone() if table(c,"gate_btc_context") else None
        math=c.execute("""SELECT * FROM gate_activation_math_results WHERE split='VALIDATION'
            ORDER BY CASE WHEN q_value IS NULL THEN 1 ELSE 0 END,q_value ASC,lift DESC LIMIT 1""").fetchone() if table(c,"gate_activation_math_results") else None

        readiness={}
        if table(c,"trade_readiness"):
            readiness={x["asset_key"]:x for x in c.execute("""SELECT * FROM trade_readiness
                WHERE source='GATE' AND batch_key=?""",(str(batch),)).fetchall()}

        overlays={}
        if table(c,"institutional_signal_overlay"):
            overlays={x["asset_key"]:x for x in c.execute("""SELECT * FROM institutional_signal_overlay
                WHERE source='GATE' AND batch_key=? ORDER BY created_at_utc DESC""",(str(batch),)).fetchall()}

        corr_overlay={}
        if table(c,"correlation_position_overlay"):
            corr_overlay={x["asset_key"]:x for x in c.execute("""SELECT * FROM correlation_position_overlay
                WHERE source='GATE' AND batch_key=? ORDER BY created_at_utc DESC""",(str(batch),)).fetchall()}
        size_curves={}
        if table(c,"execution_size_curve"):
            for x in c.execute("""SELECT * FROM execution_size_curve
              WHERE source='GATE' AND batch_key=? ORDER BY asset_key,size_usd""",(str(batch),)).fetchall():
                size_curves.setdefault(x["asset_key"],[]).append(x)
        enriched=[]
        for r in evrows:
            e=events.get(r["validation_id"])
            sym=None
            if e and table(c,"snapshots"):
                snap=c.execute("""SELECT raw_json FROM snapshots WHERE network_id=? AND token_contract=?
                    AND zaman_utc>=? ORDER BY id ASC LIMIT 1""",
                    (e["network_id"],e["token_contract"],e["signal_iso"])).fetchone()
                item=obj(snap[0]) if snap else {}
                sym=item.get("symbol") or item.get("name")
            obs=None
            if e and table(c,"gate_early_observations"):
                obs=c.execute("""SELECT own_volume_ratio,buys_5m,sells_5m,change_24h
                    FROM gate_early_observations WHERE batch_id=? AND network_id=?
                    AND token_contract=? LIMIT 1""",
                    (batch,e["network_id"],e["token_contract"])).fetchone()
            enriched.append((r,e,sym,obs,readiness.get(r["token_contract"])))

    lines=["🛰 GATE WEB3 AVCI | DAYANAK RAPORU"]
    if btc:
        lines.append(f"• BTC: {btc['btc_regime']} | 15dk %{btc['btc_15m_pct']:+.2f} | 1s %{btc['btc_1h_pct']:+.2f}")
    lines.append(f"• Gözlenen token: {health['observed_tokens']} | veri sağlığı: {health['status']}")
    lines.append("")
    if not enriched:
        lines.append("🚫 Bu tur dayanağı incelenebilir temiz aday yok.")
    labels={"DAYANAK_COK_GUCLU":"🟣 ÇOK GÜÇLÜ DAYANAK","DAYANAK_GUCLU":"🟢 GÜÇLÜ DAYANAK",
            "DAYANAK_ORTA":"🟡 ORTA DAYANAK","DAYANAK_ZAYIF":"⚪ ZAYIF DAYANAK"}
    for r,e,sym,obs,ready in enriched[:5]:
        name=sym or (e["token_contract"][:8] if e else r["token_contract"][:8])
        net=e["network_id"] if e else r["network_id"]
        sup=arr(r["support_json"]); con=arr(r["counter_json"]); unk=arr(r["unknown_json"])
        lines.append(f"{early_dot(obs)} {labels.get(r['summary'],'⚪ DAYANAK BELİRSİZ')} — {name} [{net}]")
        ov=overlays.get(f"{net}:{e['token_contract']}") if e else None
        if ov:
            if ov["success_probability"] is not None:
                ci=(f"%{100*float(ov['success_ci_low']):.0f}–%{100*float(ov['success_ci_high']):.0f}"
                    if ov["success_ci_low"] is not None and ov["success_ci_high"] is not None else "-")
                lines.append(f"• Kalibre başarı: %{100*float(ov['success_probability']):.0f} | N={ov['success_n']} | %95 CI {ci}")
            else:
                lines.append(f"• Kalibre başarı: örnek yetersiz | N={ov['success_n']}")
            if ov["regime_probability"] is not None:
                lines.append(f"• Bu rejimde: %{100*float(ov['regime_probability']):.0f} | N={ov['regime_n']} | {ov['regime_key']}")
            if ov["candidate_net_expectancy_pct"] is not None:
                lines.append(f"• Net beklenti: {float(ov['candidate_net_expectancy_pct']):+.2f}% | kontrol {float(ov['control_net_expectancy_pct']):+.2f}%")
            if ov["expectancy_diff_pct"] is not None:
                lines.append(f"• Aday-kontrol farkı: {float(ov['expectancy_diff_pct']):+.2f} puan")
            lines.append(f"• Mod: {ov['system_mode']} | vol {ov['volatility_regime']} | likidite {ov['liquidity_regime']}")
            if ov["crowding_status"]!="UNAVAILABLE":
                lines.append(f"• Sosyal crowding: {ov['crowding_status']} ({ov['crowding_value'] if ov['crowding_value'] is not None else '-'})")
            key=f"{net}:{e['token_contract']}" if e else None
            cr=corr_overlay.get(key) if key else None
            if cr:
                mc="-" if cr["max_peer_corr"] is None else f"{float(cr['max_peer_corr']):.2f}"
                lines.append(f"• Korelasyon riski: {cr['risk_label']} | max corr {mc} | paper boyut ×{float(cr['size_multiplier']):.2f}")
            curve=size_curves.get(key,[]) if key else []
            if curve:
                txt=[f"${int(float(q['size_usd']))}:{float(q['roundtrip_loss_pct']):.2f}%"
                     for q in curve if q["roundtrip_loss_pct"] is not None]
                if txt: lines.append("• Execution eğrisi: "+" | ".join(txt))
        if ready:
            rr={"PAPER_ELIGIBLE":"KAĞIT ÜSTÜ İŞLEME UYGUN","WATCH":"İZLE","NOT_READY":"HAZIR DEĞİL"}.get(ready["readiness"],"DEĞERLENDİRİLMEDİ")
            lines.append(f"• İşlem hazırlığı: {rr}")
        lines.append(f"• Destek: {r['evidence_count']} | karşı kanıt: {r['counter_count']} | veri kapsamı: %{r['coverage_pct']:.0f}")
        for x in sup[:3]: lines.append(f"  ✅ {x}")
        for x in con[:2]: lines.append(f"  ⚠️ {x}")
        if r["historical_candidate_n"]>=8 and r["historical_control_n"]>=8:
            lines.append(
                f"• Benzer geçmiş ({r['historical_candidate_n']} aday / {r['historical_control_n']} kontrol): "
                f"+5 {pct(r['hit5_rate'])} vs {pct(r['hit5_control'])} | "
                f"+10 {pct(r['hit10_rate'])} vs {pct(r['hit10_control'])} | "
                f"+15 {pct(r['hit15_rate'])} vs {pct(r['hit15_control'])}"
            )
        else:
            lines.append("• Benzer geçmiş: güvenilir yüzde vermek için örnek henüz yetersiz.")
        if unk:
            lines.append(f"• Eksik veri: {', '.join(unk[:2])}")
        lines.append("")
    if math and math["selected_n"] and math["selected_n"]>=8:
        lift="-" if math["lift"] is None else f"{math['lift']:.2f}x"
        q="-" if math["q_value"] is None else f"{math['q_value']:.3f}"
        lines.append(f"🧮 Ayrı matematik testi: {math['combo_label']} | n={math['selected_n']} | lift {lift} | q={q}")
    else:
        lines.append("🧮 Ayrı matematik testi: frozen OOS örneği henüz yetersiz.")
    lines.append("Mimari not: wake-up/retention/trigger mantığı genesis holdout öncesi tasarlandı; nihai temiz OOS dönem 7 Ekim 2026'da başlar.")
    lines.append("Not: 'dayanak' kazanma olasılığı değildir; destek/karşı-kanıt ve gerçek geçmiş sonuç özetidir.")
    token=(os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        print("\n".join(lines)); return
    chat=resolve_chat_id(token,(os.getenv("TELEGRAM_CHAT_ID") or "").strip(),DB,"Gate Web3 Motor")
    send_telegram(token,chat,"\n".join(lines)[:3900])
    print("Gate evidence Telegram sent")

if __name__=="__main__": main()
