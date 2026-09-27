#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Trader-facing Binance Telegram summary.

Compresses the latest research state into a simple upward-signal-strength view.
The label is research evidence strength, not a price guarantee or order.
"""
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
            rows=c.execute("""SELECT e.*,f.change_24h,f.btc_relative_24h,
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
                WHERE e.scan_time_utc=?""",(ts,)).fetchall()

        ranked=[]
        for r in rows:
            label,score=signal_strength(r)
            ranked.append(({"GÜÇLÜ":0,"ORTA":1,"ZAYIF":2}[label],-score,-int(r["evidence_count"] or 0),r,label,score))
        ranked.sort(key=lambda x:(x[0],x[1],x[2]))

        lines=["🛰 BINANCE AVCI",
               f"• Piyasa: {scan['btc_regime']} | BTC 24s %{float(scan['btc_change_24h']):+.2f}",
               f"• Taranan: {scan['universe_size']} coin",
               "• Çerçeve: erkenlik + akış + tutunma + yeniden hızlanma + execution",
               ""]
        if not ranked:
            lines.append("🚫 Bu tur yukarı yönlü anlamlı sinyal yok.")
        else:
            icons={"GÜÇLÜ":"🟢","ORTA":"🟡","ZAYIF":"⚪️"}
            shown=0
            for _,_,_,r,label,score in ranked:
                if shown>=4: break
                lines.append(f"{icons[label]} {label} — {r['symbol']}")
                hist_line,_=history_context(r)
                lines.append(hist_line)
                lines.append(f"• 24s %{float(r['change_24h'] or 0):+.1f} | BTC göreli %{float(r['btc_relative_24h'] or 0):+.1f}")
                rs=reasons(r)
                if rs: lines.append("• "+"; ".join(rs))
                if r["historical_candidate_n"]>=8 and r["historical_control_n"]>=8 and r["hit10_rate"] is not None and r["hit10_control"] is not None:
                    lines.append(f"• Benzer geçmiş +10: %{100*float(r['hit10_rate']):.0f} vs kontrol %{100*float(r['hit10_control']):.0f}")
                lines.append("")
                shown+=1

        lines.append("Not: GÜÇLÜ/ORTA/ZAYIF yukarı yönlü araştırma sinyalidir; 15dk havuz tek başına karar vermez.")

    msg="\n".join(lines)
    token=(os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        print(msg); return
    chat=resolve_chat_id(token,(os.getenv("TELEGRAM_CHAT_ID") or "").strip(),DB,"Binance Motor")
    send_telegram(token,chat,msg[:3900])
    print("Binance trader Telegram sent")

if __name__=="__main__":
    main()
