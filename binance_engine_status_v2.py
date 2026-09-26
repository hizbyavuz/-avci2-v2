#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json, os, sqlite3
from binance_notify import resolve_chat_id, send_telegram

DB=os.getenv("BINANCE_DB","binance_avci2.db")

def table(c,t):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone() is not None

def arr(s):
    try:
        x=json.loads(s or "[]")
        return x if isinstance(x,list) else []
    except Exception:
        return []

def pct(v):
    return "-" if v is None else f"%{100*float(v):.0f}"

def main():
    if not os.path.exists(DB):
        print("Binance human report: DB yok"); return
    with sqlite3.connect(DB,timeout=30) as c:
        c.row_factory=sqlite3.Row
        scan=c.execute("""SELECT * FROM scans WHERE health_status!='INVALID'
            ORDER BY scan_time_utc DESC LIMIT 1""").fetchone()
        if not scan:
            print("Binance human report: scan yok"); return
        ts=scan["scan_time_utc"]
        counts={r["selection_class"]:r["n"] for r in c.execute(
            """SELECT selection_class,COUNT(*) n FROM features WHERE scan_time_utc=?
               GROUP BY selection_class""",(ts,))}
        evrows=[]
        if table(c,"binance_candidate_evidence"):
            evrows=c.execute("""SELECT e.*,f.stage,f.engine,f.score,f.change_24h,f.btc_relative_24h
                FROM binance_candidate_evidence e JOIN features f
                  ON f.scan_time_utc=e.scan_time_utc AND f.symbol=e.symbol
                WHERE e.scan_time_utc=?
                ORDER BY CASE e.summary
                    WHEN 'DAYANAK_COK_GUCLU' THEN 0 WHEN 'DAYANAK_GUCLU' THEN 1
                    WHEN 'DAYANAK_ORTA' THEN 2 ELSE 3 END,
                    e.evidence_count DESC,e.counter_count ASC LIMIT 5""",(ts,)).fetchall()
        audit=c.execute("SELECT * FROM validation_audit_runs ORDER BY audited_at_utc DESC LIMIT 1").fetchone() if table(c,"validation_audit_runs") else None
        math=None
        if table(c,"binance_activation_math_results"):
            math=c.execute("""SELECT * FROM binance_activation_math_results
                WHERE split='VALIDATION' ORDER BY CASE WHEN q_value IS NULL THEN 1 ELSE 0 END,
                q_value ASC,lift DESC LIMIT 1""").fetchone()

    mode="ORTA" if scan["data_mode"]=="SPOT_ONLY" else "YÜKSEK"
    lines=[
        "🛰 BINANCE AVCI | DAYANAK RAPORU",
        f"• Piyasa: {scan['btc_regime']} | BTC 24s %{scan['btc_change_24h']:+.2f}",
        f"• Taranan: {scan['universe_size']} coin | aday: {counts.get('CANDIDATE',0)}",
        f"• Veri kalitesi: {mode}" + (" (Binance futures eksik)" if scan["data_mode"]=="SPOT_ONLY" else ""),
        ""
    ]
    if not evrows:
        lines.append("🚫 Bu tur dayanağı incelenebilir temiz aday yok.")
    labels={"DAYANAK_COK_GUCLU":"🟣 ÇOK GÜÇLÜ DAYANAK","DAYANAK_GUCLU":"🟢 GÜÇLÜ DAYANAK",
            "DAYANAK_ORTA":"🟡 ORTA DAYANAK","DAYANAK_ZAYIF":"⚪ ZAYIF DAYANAK"}
    for i,r in enumerate(evrows[:5],1):
        sup=arr(r["support_json"]); con=arr(r["counter_json"]); unk=arr(r["unknown_json"])
        lines.append(f"{labels.get(r['summary'],'⚪ DAYANAK BELİRSİZ')} — {r['symbol']}")
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

    if math:
        if math["selected_n"] and math["selected_n"]>=8:
            lift="-" if math["lift"] is None else f"{math['lift']:.2f}x"
            q="-" if math["q_value"] is None else f"{math['q_value']:.3f}"
            lines.append(f"🧮 Ayrı matematik testi: {math['combo_label']} | n={math['selected_n']} | lift {lift} | q={q}")
        else:
            lines.append("🧮 Ayrı matematik testi: frozen OOS örneği henüz yetersiz.")
    else:
        lines.append("🧮 Ayrı matematik testi: frozen OOS örneği henüz yetersiz.")

    lines.append("Not: 'dayanak' kazanma olasılığı değildir; destek/karşı-kanıt ve gerçek geçmiş sonuç özetidir.")
    token=(os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        print("\n".join(lines)); return
    chat=resolve_chat_id(token,(os.getenv("TELEGRAM_CHAT_ID") or "").strip(),DB,"Binance Motor")
    send_telegram(token,chat,"\n".join(lines)[:3900])
    print("Binance evidence Telegram sent")

if __name__=="__main__": main()
