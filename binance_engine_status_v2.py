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

def fmt_overlay_pct(v):
    return "-" if v is None else f"%{100*float(v):.0f}"

def fmt_pp(v):
    return "-" if v is None else f"{float(v):+.2f} puan"

def dayanak_label(summary):
    # Telegram'da yalnızca üç renk kullanılır.
    if summary in ("DAYANAK_COK_GUCLU","DAYANAK_GUCLU"):
        return "🔴 GÜÇLÜ DAYANAK"
    if summary=="DAYANAK_ORTA":
        return "🟡 ORTA DAYANAK"
    return "⚪ ZAYIF DAYANAK"

def plain_note(text):
    notes={
        "Benzer geçmiş adaylar +10'da kontrolü geçememiş":
            "Geçmişte buna benzeyen sinyaller, +%10 devam etme konusunda normal dönemlerden daha iyi sonuç vermemiş.",
        "Piyasa emirlerinde satış üstün":
            "Şu anda piyasa emriyle satanlar alıcılardan daha baskın.",
        "İlk hareketi koruyamıyor":
            "İlk yükselişin önemli kısmı geri verilmiş.",
        "Hareket fazla uzamış olabilir":
            "Fiyat kısa sürede fazla yükselmiş olabilir; geç kalma riski artmış.",
        "Net gerçek para akışı satış yönünde":
            "Ölçülen net alım-satım akışında para çıkışı baskın.",
        "Order-book satış tarafına eğik":
            "Emir defterinde satış tarafı daha ağır görünüyor.",
        "Geçmiş winner benzerliği zayıf":
            "Geçmişte büyük yükseliş yapan örneklere benzerlik zayıf.",
        "Manipülasyon/deception riski yüksek":
            "Hareketin doğal talep yerine yanıltıcı/manipülatif akıştan gelme riski yüksek.",
        "$1k girişte fiyat etkisi yüksek":
            "Yaklaşık 1.000 dolarlık alım bile fiyatı belirgin oynatabilir; giriş maliyeti yüksek.",
        "BTC'ye göre zayıf":
            "Coin, aynı dönemde BTC'den daha kötü performans gösteriyor.",
    }
    for key,val in notes.items():
        if text.startswith(key):
            return val
    return None

def unknown_note(text):
    notes={
        "OI/funding yok":
            "Vadeli işlemlerde yeni pozisyon açılıyor mu ve kaldıraç hangi tarafta yoğun, bunu doğrulayamıyoruz.",
        "Geçmiş winner karşılaştırması yok":
            "Geçmişte büyük yükseliş yapan örneklerle benzerlik hesabı henüz yok.",
        "Execution ölçümü yok":
            "Gerçek girişte spread ve fiyat etkisi maliyetini henüz ölçemiyoruz.",
        "Para akışı ölçümü yok":
            "Gerçek alım-satım para akışı verisi bu coin için yok.",
        "Yapı/sector ölçümü yok":
            "Coinin kendi sektörü ve piyasa yapısına göre ayrışmasını tam ölçemiyoruz.",
        "Manipülasyon karşı-kontrolü yok":
            "Yanıltıcı/manipülatif hareket ihtimaline karşı ayrı kontrol verisi yok.",
        "Türev verisi yalnız OKX proxy; Binance-native değil":
            "Vadeli işlem teyidi Binance'tan değil, başka borsadan dolaylı geliyor.",
        "Benzer kapanmış olay örneği +10 için yetersiz":
            "Geçmişteki benzer örnek sayısı, +%10 devam oranına güvenmek için az.",
    }
    return notes.get(text)

def hist_line(label, cand, ctrl):
    if cand is None or ctrl is None:
        return f"  • {label}: veri yetersiz"
    c=100*float(cand); k=100*float(ctrl)
    if c > k:
        meaning="benzer sinyaller normal dönemlerden daha iyi"
    elif c < k:
        meaning="normal dönemler daha iyi; burada matematik üstünlük göstermiyor"
    else:
        meaning="iki grup eşit; belirgin avantaj yok"
    return f"  • {label}: %{c:.0f} vs %{k:.0f} ({meaning})"

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
            evrows=c.execute("""SELECT e.*,f.stage,f.engine,f.score,f.change_24h,f.btc_relative_24h,
                       f.wakeup,f.reignition,f.trigger,f.retention,f.climax_risk,
                       p.status AS pool_status,p.confirmation_score AS pool_confirmation_score,
                       tr.readiness AS trade_readiness,tr.passed_count AS ready_passed,
                       tr.failed_count AS ready_failed,tr.unknown_count AS ready_unknown
                FROM binance_candidate_evidence e JOIN features f
                  ON f.scan_time_utc=e.scan_time_utc AND f.symbol=e.symbol
                LEFT JOIN binance_live_pool p
                  ON p.scan_time_utc=e.scan_time_utc AND p.symbol=e.symbol
                LEFT JOIN trade_readiness tr
                  ON tr.source='BINANCE' AND tr.batch_key=e.scan_time_utc
                 AND tr.asset_key=e.symbol
                WHERE e.scan_time_utc=?
                  AND (p.status IN ('CONFIRMED','BORDERLINE') OR p.status IS NULL)
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

        pool_counts=None
        if table(c,"binance_live_pool"):
            pool_counts={r["status"]:r["n"] for r in c.execute("""SELECT status,COUNT(*) n
                FROM binance_live_pool WHERE scan_time_utc=? GROUP BY status""",(ts,))}

        overlays={}
        if table(c,"institutional_signal_overlay"):
            overlays={r["asset_key"]:r for r in c.execute(
                """SELECT * FROM institutional_signal_overlay
                   WHERE source='BINANCE' AND batch_key=?
                   ORDER BY created_at_utc DESC""",(ts,)).fetchall()}

        corr_overlay={}
        if table(c,"correlation_position_overlay"):
            corr_overlay={r["asset_key"]:r for r in c.execute(
                """SELECT * FROM correlation_position_overlay
                   WHERE source='BINANCE' AND batch_key=?
                   ORDER BY created_at_utc DESC""",(ts,)).fetchall()}
        size_curves={}
        if table(c,"execution_size_curve"):
            for rr in c.execute("""SELECT * FROM execution_size_curve
              WHERE source='BINANCE' AND batch_key=? ORDER BY asset_key,size_usd""",(ts,)).fetchall():
                size_curves.setdefault(rr["asset_key"],[]).append(rr)

    mode="ORTA" if scan["data_mode"]=="SPOT_ONLY" else "YÜKSEK"
    lines=[
        "🛰 BINANCE AVCI | DAYANAK RAPORU",
        f"• Piyasa: {scan['btc_regime']} | BTC 24s %{scan['btc_change_24h']:+.2f}",
        f"• Taranan: {scan['universe_size']} coin | ilk aday: {counts.get('CANDIDATE',0)}",
        f"• Veri kalitesi: {mode}" + (" (Binance futures eksik)" if scan["data_mode"]=="SPOT_ONLY" else ""),
        ""
    ]
    if pool_counts is not None:
        total=sum(pool_counts.values())
        lines.append(f"• 15dk havuz: {total} coin | teyit {pool_counts.get('CONFIRMED',0)} | sınırda {pool_counts.get('BORDERLINE',0)} | sönen {pool_counts.get('FADED',0)}")
        lines.append("")

    if not evrows:
        lines.append("🚫 Bu tur dayanağı incelenebilir temiz aday yok.")

    for r in evrows[:5]:
        sup=arr(r["support_json"]); con=arr(r["counter_json"]); unk=arr(r["unknown_json"])

        # Tek renk: canlı havuz rengi ayrıca basılmaz.
        lines.append(f"{dayanak_label(r['summary'])} — {r['symbol']}")
        ov=overlays.get(r["symbol"])
        if ov:
            ci=(f"%{100*float(ov['success_ci_low']):.0f}–%{100*float(ov['success_ci_high']):.0f}"
                if ov["success_ci_low"] is not None and ov["success_ci_high"] is not None else "-")
            if ov["success_probability"] is not None:
                lines.append(f"Geçmişe göre başarı ihtimali: %{100*float(ov['success_probability']):.0f} | örnek sayısı {ov['success_n']} | %95 güven aralığı {ci}")
            else:
                lines.append(f"Geçmişe göre başarı ihtimali: henüz güvenilir değil | örnek sayısı {ov['success_n']}")
            if ov["regime_probability"] is not None:
                lines.append(f"Şu anki piyasa şartlarında başarı: %{100*float(ov['regime_probability']):.0f} | örnek sayısı {ov['regime_n']}")
            lines.append(f"Masraflar sonrası beklenen ortalama getiri: {fmt_pp(ov['candidate_net_expectancy_pct'])} | normal/kontrol grubu {fmt_pp(ov['control_net_expectancy_pct'])}")
            if ov["expectancy_diff_pct"] is not None:
                dci=(f"{float(ov['expectancy_ci_low']):+.2f}…{float(ov['expectancy_ci_high']):+.2f}"
                     if ov["expectancy_ci_low"] is not None and ov["expectancy_ci_high"] is not None else "-")
                lines.append(f"Adayın normale göre avantajı: {float(ov['expectancy_diff_pct']):+.2f} yüzde puanı | %95 güven aralığı {dci}")
            lines.append(f"Sistem modu: {ov['system_mode']} | oynaklık {ov['volatility_regime']} | likidite {ov['liquidity_regime']}")
            if ov["confounders_json"] and ov["confounders_json"]!="[]":
                try:
                    cf=json.loads(ov["confounders_json"])
                    if cf: lines.append("Dış etken uyarısı (haber/makro olay sinyali etkileyebilir): "+", ".join(cf[:2]))
                except Exception:
                    pass
            if ov["crowding_status"] and ov["crowding_status"]!="UNAVAILABLE_TRUE_SOCIAL_FEED":
                lines.append(f"Sosyal kalabalıklaşma (aynı coin'e ilgi birikmesi): {ov['crowding_status']} ({ov['crowding_value'] if ov['crowding_value'] is not None else '-'})")
            lines.append(f"Başka borsalardan teyit: {ov['external_validation_status']}")
            cr=corr_overlay.get(r["symbol"])
            if cr:
                mc="-" if cr["max_peer_corr"] is None else f"{float(cr['max_peer_corr']):.2f}"
                lines.append(f"Benzer hareket riski (diğer açık adaylarla aynı yönde gitme): {cr['risk_label']} | en yüksek ilişki {mc} | kağıt üstü pozisyon boyutu ×{float(cr['size_multiplier']):.2f}")
            curve=size_curves.get(r["symbol"],[])
            if curve:
                txt=[]
                for q in curve:
                    if q["roundtrip_loss_pct"] is not None:
                        txt.append(f"${int(float(q['size_usd']))}:{float(q['roundtrip_loss_pct']):.2f}%")
                if txt: lines.append("Gerçek alım-satım maliyeti (pozisyon büyüdükçe kayıp): "+" | ".join(txt))
        lines.append("")

        if r["pool_status"]:
            ps={"CONFIRMED":"15dk canlı teyit geçti","BORDERLINE":"15dk teyit sınırda","FADED":"15dk içinde söndü"}.get(r["pool_status"],r["pool_status"])
            raw_score=int(r["pool_confirmation_score"] or 0)
            shown_score=min(raw_score,6)
            lines.append(f"Canlı teyit: {shown_score}/6")
            lines.append(f"({ps}; 15 dakikalık kontrolde aranan şartlardan {shown_score} tanesi sağlandı.)")
            lines.append("")

        rr={"PAPER_ELIGIBLE":"KAĞIT ÜSTÜ İŞLEME UYGUN","WATCH":"İZLE","NOT_READY":"HAZIR DEĞİL"}.get(r["trade_readiness"],"DEĞERLENDİRİLMEDİ")
        lines.append(f"İşlem hazırlığı: {rr}")
        lines.append(f"Destek / karşı kanıt: {r['evidence_count']} / {r['counter_count']}")
        lines.append(f"(Destek = hareketi doğrulayan veri; karşı kanıt = hareketin devamına ters düşen veri. Veri kapsamı %{r['coverage_pct']:.0f}.)")
        lines.append("")

        for x in sup[:3]:
            lines.append(f"✅ {x}")
        for x in con[:2]:
            lines.append(f"⚠️ {x}")
            note=plain_note(x)
            if note:
                lines.append(f"   ({note})")

        lines.append("")
        if r["historical_candidate_n"]>=8 and r["historical_control_n"]>=8:
            lines.append(f"Benzer geçmiş: {r['historical_candidate_n']} aday / {r['historical_control_n']} kontrol")
            lines.append("(Aday = buna benzeyen eski sinyaller; kontrol = karşılaştırma için kullanılan normal/sinyalsiz dönemler.)")
            lines.append(hist_line("+%5'e ulaşan",r["hit5_rate"],r["hit5_control"]))
            lines.append(hist_line("+%10'a ulaşan",r["hit10_rate"],r["hit10_control"]))
            lines.append(hist_line("+%15'e ulaşan",r["hit15_rate"],r["hit15_control"]))
        else:
            lines.append("Benzer geçmiş: örnek sayısı güvenilir yüzde vermek için henüz yetersiz.")

        if unk:
            lines.append("")
            lines.append("Eksik veri:")
            for x in unk[:2]:
                lines.append(f"⚠️ {x}")
                note=unknown_note(x)
                if note:
                    lines.append(f"   ({note})")

        lines.append("")
        lines.append(f"SONUÇ: {dayanak_label(r['summary'])} | {rr}")
        lines.append("────────────")

    if math:
        if math["selected_n"] and math["selected_n"]>=8:
            lift="-" if math["lift"] is None else f"{math['lift']:.2f}x"
            q="-" if math["q_value"] is None else f"{math['q_value']:.3f}"
            lines.append(f"🧮 Ayrı matematik testi: {math['combo_label']} | n={math['selected_n']} | lift {lift} | q={q}")
            lines.append("(lift = seçilen grubun kontrole göre kaç kat iyi olduğu; q = bulunan farkın tesadüf olma riskini çoklu testlere göre düzelten ölçü.)")
        else:
            lines.append("🧮 Ayrı matematik testi: frozen OOS örneği henüz yetersiz.")
            lines.append("(OOS = kuralları oluştururken hiç kullanılmamış yeni veride yapılan temiz sınama.)")
    else:
        lines.append("🧮 Ayrı matematik testi: frozen OOS örneği henüz yetersiz.")
        lines.append("(OOS = kuralları oluştururken kullanılmamış yeni veride yapılan gerçek sınama.)")

    lines.append("")
    lines.append("Mimari not: wake-up (ilk uyanış), retention (ilk hareketin korunması) ve trigger (işlem tetikleyicisi) kuralları geçmiş örnekler görülerek tasarlandı; tamamen dokunulmamış temiz test dönemi 7 Ekim 2026'da başlar.")
    lines.append("Not: Dayanak kazanma olasılığı değildir; destek, karşı kanıt ve gerçek geçmiş sonuçların özetidir.")

    token=(os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        print("\n".join(lines)); return
    chat=resolve_chat_id(token,(os.getenv("TELEGRAM_CHAT_ID") or "").strip(),DB,"Binance Motor")
    send_telegram(token,chat,"\n".join(lines)[:3900])
    print("Binance evidence Telegram sent")

if __name__=="__main__": main()
