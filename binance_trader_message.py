#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Trader-facing Binance Telegram summary.

Compresses the latest research state into a simple upward-signal-strength view.
The label is research evidence strength, not a price guarantee or order.
"""
import json, os, sqlite3, math
from datetime import datetime, timezone, timedelta
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

def historical_control_line(candidate_n,control_n,candidate_rate,control_rate):
    """Plain comparison with a 95% uncertainty interval; descriptive only."""
    try:
        n1=int(candidate_n or 0); n0=int(control_n or 0)
        p1=float(candidate_rate); p0=float(control_rate)
    except (TypeError,ValueError):
        return None
    if n1<8 or n0<8:
        return None
    diff=p1-p0
    se=math.sqrt(max(0.0,p1*(1-p1)/n1 + p0*(1-p0)/n0))
    lo=diff-1.96*se; hi=diff+1.96*se
    if lo>0:
        return (f"• Sistem farkı: benzer adaylarda +%10 %{100*p1:.0f} | "
                f"benzer seçilmeyenlerde %{100*p0:.0f} | fark {100*diff:+.0f} puan "
                f"(yaklaşık %95 aralık {100*lo:+.0f}..{100*hi:+.0f})")
    return (f"• Geçmiş karşılaştırma: aday %{100*p1:.0f} | kontrol %{100*p0:.0f} | "
            f"fark {100*diff:+.0f} puan; henüz belirsiz "
            f"(%95 aralık {100*lo:+.0f}..{100*hi:+.0f}, 0'ı kapsıyor)")

def init_label_ledger(c):
    c.execute("""CREATE TABLE IF NOT EXISTS trader_label_ledger(
      source TEXT NOT NULL,batch_key TEXT NOT NULL,asset_key TEXT NOT NULL,
      display_name TEXT NOT NULL,label TEXT NOT NULL,signal_time_utc TEXT NOT NULL,
      entry_price REAL,due_at_utc TEXT NOT NULL,latest_price REAL,peak_price REAL,
      trough_price REAL,mfe_pct REAL,mae_pct REAL,final_return_pct REAL,
      status TEXT NOT NULL DEFAULT 'OPEN',closed_at_utc TEXT,
      PRIMARY KEY(source,batch_key,asset_key)
    )""")

def record_label(c,batch,asset,label,price):
    try:t=datetime.fromisoformat(str(batch).replace("Z","+00:00"))
    except Exception:t=datetime.now(timezone.utc)
    c.execute("""INSERT OR IGNORE INTO trader_label_ledger
      (source,batch_key,asset_key,display_name,label,signal_time_utc,entry_price,due_at_utc,status)
      VALUES('BINANCE',?,?,?,?,?,?,?,'OPEN')""",
      (str(batch),asset,asset,label,t.isoformat(),price,(t+timedelta(hours=72)).isoformat()))

def signal_strength(r):
    """Independent evidence ensemble; 15m live pool is weighted, never a sole veto."""
    evidence=int(r["evidence_count"] or 0)
    counter=int(r["counter_count"] or 0)
    coverage=float(r["coverage_pct"] or 0)
    readiness=r["readiness"] or "NOT_READY"
    live=r["pool_status"] or "UNKNOWN"
    hist=r["historical_edge"] or "UNKNOWN"
    micro=r["microstructure_quality"] or "NEUTRAL"

    score=0
    if readiness=="PAPER_ELIGIBLE": score+=3
    elif readiness=="WATCH": score+=2
    if evidence>=7: score+=3
    elif evidence>=5: score+=2
    elif evidence>=3: score+=1
    if counter==0: score+=2
    elif counter==1: score+=1
    elif counter>=3: score-=2
    if coverage>=75: score+=1
    elif coverage<55: score-=1
    if live=="CONFIRMED": score+=2
    elif live=="BORDERLINE": score+=0
    elif live in ("FADED","INSUFFICIENT"): score-=2
    if hist=="GOOD": score+=2
    elif hist=="BAD": score-=2
    if micro=="GOOD": score+=1
    elif micro=="BAD": score-=1
    _,runner_penalty=history_context(r)
    score+=runner_penalty

    # Hard research blocks remain stronger than the aggregate.
    blockers=" | ".join(arr(r["counter_json"])).lower()
    if "climax" in blockers or "veri kapsamı düşük" in blockers:
        return "ZAYIF",score
    if score>=8 and evidence>counter:
        return "GÜÇLÜ",score
    if score>=4 and evidence>=counter:
        return "ORTA",score
    return "ZAYIF",score

def history_context(r):
    try:
        raw=json.loads(r["feature_raw_json"] or "{}")
    except Exception:
        raw={}
    gain=raw.get("history_gain_90d_pct")
    if gain is None:
        return "(Son 90g büyük yükseliş geçmişi doğrulanamadı.)",0
    gain=float(gain)
    if gain>=100:
        return f"(Son 90g dipten +%{gain:.0f} yaptı — tekrar güçlü yükseliş için daha fazla teyit gerekiyor.)",-2
    if gain>=50:
        return f"(Son 90g dipten +%{gain:.0f} yaptı — yakın geçmişte büyük koşu var.)",-1
    return f"(Son 90g dipten +%{gain:.0f} — erkenlik açısından daha temiz.)",0

def plain_late(value):
    return {"LOW":"Düşük","MEDIUM":"Orta","HIGH":"Yüksek"}.get(str(value or "").upper(),"Bilinmiyor")

def plain_decision(label):
    return {
        "GÜÇLÜ":("🟢","İZLEMEYE DEĞER"),
        "ORTA":("🟡","BEKLE / TAKİP ET"),
        "ZAYIF":("🔴","ŞİMDİLİK GİRME"),
    }.get(label,("⚪️","BELİRSİZ"))

def plain_live(value):
    return {
        "CONFIRMED":"güçlü kaldı",
        "BORDERLINE":"sınırda",
        "FADED":"hareket söndü",
        "INSUFFICIENT":"veri yetersiz",
    }.get(value,"veri yok")

def plain_early_reason(values):
    mapping={
        "SILENT_ACCUMULATION":"hacim/fiyat sessizce güçleniyor",
        "POSSIBLE_FOLLOWER":"önde giden coinleri takip etme ihtimali var",
        "WAKE_UP":"normaline göre erken hareket başladı",
        "REIGNITION":"ilk hareketten sonra yeniden hızlanıyor",
        "FOLLOWER":"sektör hareketini takip ediyor",
        "LEADER":"grubunda öne çıkıyor",
    }
    out=[]
    for value in values:
        key=str(value or "").strip().upper()
        text=mapping.get(key)
        if text and text not in out:
            out.append(text)
    return ", ".join(out[:2]) if out else "erken hareket izi var"

def reasons(r):
    sup=arr(r["support_json"])
    con=arr(r["counter_json"])
    out=[]
    if r["pool_status"]=="CONFIRMED": out.append("15dk canlı havuz teyitli")
    elif r["pool_status"]=="FADED": out.append("15dk canlı havuzda söndü")
    elif r["pool_status"]=="BORDERLINE": out.append("15dk canlı havuz sınırda")
    out.extend(sup[:2])
    if con: out.append("Risk: "+con[0])
    return list(dict.fromkeys(out))[:4]

def decision_quality(c,batch,asset):
    if not table(c,"decision_quality"): return None
    return c.execute("""SELECT * FROM decision_quality
        WHERE source='BINANCE' AND batch_key=? AND asset_key=?
        ORDER BY created_at_utc DESC LIMIT 1""",(str(batch),asset)).fetchone()

def capital_status(c):
    if not table(c,"capital_trust_status"):
        return "CLOSED","kanıt kapısı henüz hesaplanmadı"
    r=c.execute("""SELECT * FROM capital_trust_status
        WHERE source='BINANCE' ORDER BY created_at_utc DESC LIMIT 1""").fetchone()
    if not r:
        return "CLOSED","kanıt kapısı henüz hesaplanmadı"
    blockers=arr(r["blockers_json"])
    reason=blockers[0] if blockers else "tüm sermaye kriterleri geçti"
    return r["status"],reason

def main():
    if not os.path.exists(DB):
        print("Binance trader message: DB yok"); return
    with sqlite3.connect(DB,timeout=30) as c:
        c.row_factory=sqlite3.Row
        scan=c.execute("""SELECT * FROM scans WHERE health_status!='INVALID'
            ORDER BY scan_time_utc DESC LIMIT 1""").fetchone()
        if not scan:
            print("Binance trader message: scan yok"); return
        ts=scan["scan_time_utc"]
        rows=[]
        if table(c,"binance_candidate_evidence") and table(c,"trade_readiness"):
            rows=c.execute("""SELECT e.*,f.price,f.change_24h,f.btc_relative_24h,
                       f.taker_buy_ratio_15m,f.retention,f.persistence,f.reignition,
                       f.oi_change_1h_pct,f.funding_rate,
                       p.status pool_status,p.confirmation_score pool_score,
                       tr.readiness,tr.execution_quality,tr.historical_edge,
                       tr.microstructure_quality,f.raw_json feature_raw_json
                FROM binance_candidate_evidence e
                JOIN features f ON f.scan_time_utc=e.scan_time_utc AND f.symbol=e.symbol
                LEFT JOIN binance_live_pool p
                  ON p.scan_time_utc=e.scan_time_utc AND p.symbol=e.symbol
                JOIN trade_readiness tr
                  ON tr.source='BINANCE' AND tr.batch_key=e.scan_time_utc
                 AND tr.asset_key=e.symbol
                WHERE e.scan_time_utc=?
                  AND e.version=(SELECT version FROM binance_candidate_evidence
                    WHERE scan_time_utc=? ORDER BY created_at_utc DESC LIMIT 1)""",(ts,ts)).fetchall()

        ranked=[]
        for r in rows:
            label,score=signal_strength(r)
            dq=decision_quality(c,ts,r["symbol"])
            if dq and dq["quality_status"]=="BLOCK":
                continue
            if dq and label=="GÜÇLÜ" and dq["quality_status"]=="WATCH":
                label="ORTA"
            ranked.append(({"GÜÇLÜ":0,"ORTA":1,"ZAYIF":2}[label],-score,-int(r["evidence_count"] or 0),r,label,score,dq))
        ranked.sort(key=lambda x:(x[0],x[1],x[2]))

        init_label_ledger(c)
        cap_status,cap_reason=capital_status(c)
        cap_line="🔒 Gerçek para kapısı kapalı" if cap_status!="OPEN" else "🔓 Gerçek para kapısı açık"
        lines=["🛰 BINANCE AVCI",
               f"• BTC: %{float(scan['btc_change_24h']):+.2f} | Piyasa: {scan['btc_regime']}",
               f"• {cap_line}",
               ""]
        if not ranked:
            lines.append("🔴 ALINABİLİR ADAY YOK")
            lines.append("• Şu an ana kuralları geçen coin çıkmadı.")
        else:
            icons={"GÜÇLÜ":"🟢","ORTA":"🟡","ZAYIF":"⚪️"}
            shown=0
            for _,_,_,r,label,score,dq in ranked:
                if shown>=3: break
                record_label(c,ts,r["symbol"],label,r["price"])
                icon,decision=plain_decision(label)
                sup=arr(r["support_json"]); con=arr(r["counter_json"])
                why=sup[0] if sup else "birden fazla veri aynı yönde"
                risk=con[0] if con else "kritik karşı sinyal yok"
                late="Bilinmiyor"
                if dq:
                    late=plain_late(dq["late_risk"])
                    if dq["late_risk"]!="LOW":
                        risk=str(dq["late_reason"] or risk)

                lines.append(f"{icon} {r['symbol']} — {decision}")
                lines.append(f"• Durum: 24s %{float(r['change_24h'] or 0):+.1f} | BTC'ye göre %{float(r['btc_relative_24h'] or 0):+.1f} | 15dk: {plain_live(r['pool_status'])}")
                lines.append(f"• Neden: {why}")
                lines.append(f"• Risk: {risk}")
                lines.append(f"• Geç kalma: {late}")

                hist_line,_=history_context(r)
                lines.append(f"• Geçmiş koşu: {hist_line.strip('()')}")
                cmp=historical_control_line(r["historical_candidate_n"],r["historical_control_n"],
                                            r["hit10_rate"],r["hit10_control"])
                if cmp:
                    lines.append(cmp)
                elif dq and dq["empirical_probability"] is not None and int(dq["empirical_n"] or 0)>=30:
                    lines.append(f"• Benzer geçmiş: +%10'a ulaşma %{100*float(dq['empirical_probability']):.0f} "
                                 f"(n={int(dq['empirical_n'])}); uygun kontrol kıyası olmadığı için avantaj yorumu yapılmıyor")
                else:
                    lines.append("• Sistem farkı: güvenilir karşılaştırma için henüz yeterli geçmiş örnek yok")
                lines.append("")
                shown+=1

        # Observation-only early lane: visible before a coin becomes a full candidate.
        # Never promoted to GÜÇLÜ/ORTA/ZAYIF and never recorded as a recommendation.
        if table(c,"opportunity_observations"):
            cols={row[1] for row in c.execute("PRAGMA table_info(opportunity_observations)")}
            if "early_watch" in cols:
                early=c.execute("""SELECT o.symbol,o.early_watch_reason_json,
                           o.acceleration_ratio,o.leader_rank,o.sector_rank,
                           f.change_15m,f.change_1h,f.change_24h,f.volume_mult_15m,
                           f.taker_buy_ratio_15m,f.retention_proxy,f.stage
                    FROM opportunity_observations o
                    JOIN features f ON f.scan_time_utc=o.scan_time_utc AND f.symbol=o.symbol
                    WHERE o.scan_time_utc=? AND o.early_watch=1
                    ORDER BY COALESCE(o.acceleration_ratio,0) DESC,
                             COALESCE(f.taker_buy_ratio_15m,0) DESC LIMIT 3""",(ts,)).fetchall()
                if early:
                    lines.append("")
                    lines.append("🟡 ERKEN İZLEME")
                    for e in early:
                        why=arr(e["early_watch_reason_json"])
                        why_text=plain_early_reason(why)
                        lines.append(f"🟡 {e['symbol']} — İZLE")
                        lines.append(f"• Neden: {why_text}.")
                        lines.append(f"• Hareket: 15dk %{float(e['change_15m'] or 0):+.1f} | 24s %{float(e['change_24h'] or 0):+.1f}")
                        lines.append("• Karar: Henüz alma; ana teyit bekleniyor.")
                    lines.append("• 🟡 = erken izleme; alım sinyali değil.")

        # Accountability: show strong Spot movers even when core Avci did not recommend them.
        if table(c,"top_mover_audit"):
            audits=c.execute("""SELECT * FROM top_mover_audit
                WHERE scan_time_utc=?
                  AND audit_status IN ('MISSED','LATE_CAUGHT','OUTSIDE_CORE_UNIVERSE','NOT_IN_SNAPSHOT')
                ORDER BY current_change_24h DESC""",(ts,)).fetchall()
            if audits:
                lines.append("")
                lines.append(f"⚪ KAÇIRILAN / GEÇ YAKALANANLAR ({len(audits)})")
                status_text={
                    "LATE_CAUGHT":"geç gördü",
                    "OUTSIDE_CORE_UNIVERSE":"evren dışında kaldı",
                    "MISSED":"kaçırdı",
                    "NOT_IN_SNAPSHOT":"evren kaydı yok",
                }
                for a in audits[:5]:
                    label=status_text.get(a["audit_status"],a["audit_status"])
                    lines.append(f"⚪ {a['symbol']} %{float(a['current_change_24h']):+.1f} — {label}")
                if len(audits)>5:
                    lines.append(f"• +{len(audits)-5} coin daha kayda alındı; Telegram'a taşınmadı.")

        lines.append("")
        lines.append("Renkler: 🟢 güçlü aday | 🟡 izle/bekle | 🔴 girme | ⚪ kaçırılan/geç")
        lines.append("Not: Teknik ayrıntılar DB'de tutulur; sistem otomatik emir vermez.")
        c.commit()

    msg="\n".join(lines)
    token=(os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        print(msg); return
    chat=resolve_chat_id(token,(os.getenv("TELEGRAM_CHAT_ID") or "").strip(),DB,"Binance Motor")
    send_telegram(token,chat,msg[:3900])
    print("Binance trader Telegram sent")

if __name__=="__main__":
    main()
