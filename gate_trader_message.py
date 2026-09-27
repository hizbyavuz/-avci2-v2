#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Trader-facing Gate Web3 Telegram summary.

Security is fail-closed and separate from directional signal strength.
Only tokens without a security hard veto can appear in GÜÇLÜ/ORTA/ZAYIF.
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

def gate_history_context(c,network,contract):
    if not table(c,"snapshots"):
        return "(90g geçmiş verisi henüz yetersiz.)",0
    rows=c.execute("""SELECT zaman_utc,raw_json FROM snapshots
        WHERE network_id=? AND token_contract=?
          AND zaman_utc>=datetime('now','-90 day')
        ORDER BY zaman_utc ASC""",(network,contract)).fetchall()
    prices=[]
    times=[]
    for row in rows:
        try:
            x=json.loads(row["raw_json"] or "{}")
        except Exception:
            x={}
        p=x.get("price_usd") or x.get("price")
        try:
            p=float(p)
        except Exception:
            p=0
        if p>0:
            prices.append(p); times.append(row["zaman_utc"])
    if len(prices)<2:
        return "(90g geçmiş verisi henüz yetersiz.)",0
    floor=min(prices)
    current=prices[-1]
    gain=((current/floor)-1.0)*100.0 if floor>0 else 0.0
    # Do not pretend a partial archive is a full 90-day record.
    if len(times)<20:
        return f"(Kayıtlı geçmişte dipten +%{gain:.0f}; 90g tam geçmiş henüz yetersiz.)",(-1 if gain>=50 else 0)
    if gain>=100:
        return f"(Kayıtlı 90g geçmişte dipten +%{gain:.0f} — tekrar güçlü yükseliş için daha fazla teyit gerekiyor.)",-2
    if gain>=50:
        return f"(Kayıtlı 90g geçmişte dipten +%{gain:.0f} — yakın geçmişte büyük koşu var.)",-1
    return f"(Kayıtlı 90g geçmişte dipten +%{gain:.0f} — erkenlik daha temiz.)",0

def signal_strength(r):
    evidence=int(r["evidence_count"] or 0)
    counter=int(r["counter_count"] or 0)
    coverage=float(r["coverage_pct"] or 0)
    readiness=r["readiness"] or "NOT_READY"
    hist=r["historical_edge"] or "UNKNOWN"
    exe=r["execution_quality"] or "UNKNOWN"
    sec=(r["security_label"] or "UNKNOWN").upper()

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
    if hist=="GOOD": score+=2
    elif hist=="BAD": score-=2
    if exe=="GOOD": score+=2
    elif exe=="BAD": score-=3
    if sec=="STRONG": score+=2
    elif sec=="MEDIUM": score+=1

    if int(r["hard_veto"] or 0):
        return None,score
    joined=" | ".join(arr(r["counter_json"])).lower()
    if "güvenlik" in joined and ("hard-veto" in joined or "zayıf" in joined):
        return None,score
    if score>=9 and evidence>counter and exe=="GOOD" and sec in ("STRONG","MEDIUM"):
        return "GÜÇLÜ",score
    if score>=5 and evidence>=counter and sec in ("STRONG","MEDIUM"):
        return "ORTA",score
    return "ZAYIF",score

def decision_quality(c,batch,contract):
    if not table(c,"decision_quality"): return None
    return c.execute("""SELECT * FROM decision_quality
        WHERE source='GATE' AND batch_key=? AND asset_key=?
        ORDER BY created_at_utc DESC LIMIT 1""",(str(batch),contract)).fetchone()

def capital_status(c):
    if not table(c,"capital_trust_status"):
        return "CLOSED","kanıt kapısı henüz hesaplanmadı"
    r=c.execute("""SELECT * FROM capital_trust_status
        WHERE source='GATE' ORDER BY created_at_utc DESC LIMIT 1""").fetchone()
    if not r:
        return "CLOSED","kanıt kapısı henüz hesaplanmadı"
    blockers=arr(r["blockers_json"])
    reason=blockers[0] if blockers else "tüm sermaye kriterleri geçti"
    return r["status"],reason

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

        rows=[]
        if table(c,"gate_candidate_evidence") and table(c,"trade_readiness") and table(c,"gate_security_confidence_history"):
            rows=c.execute("""SELECT e.*,tr.readiness,tr.execution_quality,tr.historical_edge,
                       s.label security_label,s.hard_veto
                FROM gate_candidate_evidence e
                JOIN trade_readiness tr
                  ON tr.source='GATE' AND tr.batch_key=e.batch_id
                 AND tr.asset_key=e.token_contract
                JOIN gate_security_confidence_history s
                  ON s.batch_id=e.batch_id AND s.network_id=e.network_id
                 AND s.token_contract=e.token_contract
                WHERE e.batch_id=?
                ORDER BY e.evidence_count DESC,e.counter_count ASC""",(batch,)).fetchall()

        ranked=[]
        seen=set()
        for r in rows:
            label,score=signal_strength(r)
            if label is None: continue
            _,runner_penalty=gate_history_context(c,r["network_id"],r["token_contract"])
            score+=runner_penalty
            dq=decision_quality(c,batch,r["token_contract"])
            if dq and dq["quality_status"]=="BLOCK":
                continue
            if label=="GÜÇLÜ" and (runner_penalty<=-2 or (dq and dq["quality_status"]=="WATCH")):
                label="ORTA"
            key=(r["network_id"],r["token_contract"])
            seen.add(key)
            ranked.append(({"GÜÇLÜ":0,"ORTA":1,"ZAYIF":2}[label],-score,-int(r["evidence_count"] or 0),r,label,score,dq))

        # Safe early discovery can be shown as ZAYIF only; never upgrades security.
        if table(c,"gate_weighted_discovery"):
            extra=c.execute("""SELECT * FROM gate_weighted_discovery
                WHERE batch_id=? AND status='SAFE_DISCOVERY'
                  AND score>=55 AND change_24h BETWEEN -5 AND 40
                ORDER BY score DESC,liquidity DESC LIMIT 5""",(batch,)).fetchall()
            for r in extra:
                key=(r["network_id"],r["token_contract"])
                if key not in seen:
                    ranked.append((2,-float(r["score"] or 0),0,r,"ZAYIF",float(r["score"] or 0),None))

        ranked.sort(key=lambda x:(x[0],x[1],x[2]))
        cap_status,cap_reason=capital_status(c)
        cap_line="🔒 Gerçek para kapısı kapalı" if cap_status!="OPEN" else "🔓 Gerçek para kapısı açık"
        lines=["🛰 GATE WEB3 AVCI",
               f"• İzlenen: {health['observed_tokens']} token | {cap_line}",
               ""]

        if not ranked:
            lines.append("🚫 Bu taramada güvenliği geçen alınabilir görünen token yok.")
        else:
            icons={"GÜÇLÜ":"🟢","ORTA":"🟡","ZAYIF":"⚪️"}
            shown=0
            for _,_,_,r,label,score,dq in ranked:
                if shown>=3: break
                network=r["network_id"]
                contract=r["token_contract"]
                name=(r["symbol"] if "symbol" in r.keys() and r["symbol"] else symbol_for(c,network,contract))
                lines.append(f"{icons[label]} {label} — {name} [{network}]")
                hist_line,_=gate_history_context(c,network,contract)
                lines.append(hist_line)
                obs=c.execute("""SELECT change_24h,liquidity,buys_5m,sells_5m,own_volume_ratio
                    FROM gate_early_observations WHERE batch_id=? AND network_id=? AND token_contract=? LIMIT 1""",
                    (batch,network,contract)).fetchone() if table(c,"gate_early_observations") else None
                if obs:
                    b=float(obs["buys_5m"] or 0); sv=float(obs["sells_5m"] or 0)
                    flow=(b/max(sv,1.0)) if b+sv else 0
                    vr="-" if obs["own_volume_ratio"] is None else f"{float(obs['own_volume_ratio']):.1f}x"
                    lines.append(f"• Gerçek veri: 24s %{float(obs['change_24h'] or 0):+.1f} | likidite {money(obs['liquidity'])} | hacim anomalisi (kendi normaline göre): {vr} | 5dk alıcı/satıcı: {flow:.1f}x")
                if "support_json" in r.keys():
                    sup=arr(r["support_json"]); con=arr(r["counter_json"])
                    why=sup[0] if sup else "birden fazla on-chain veri aynı yöne bakıyor"
                    risk=con[0] if con else "kritik karşı kanıt yok"
                    if dq:
                        lines.append(f"• Bağımsız dayanak: {int(dq['independent_families'])} veri grubu | Geç kalma riski: {dq['late_risk']}")
                        if dq["late_risk"]!="LOW":
                            risk=str(dq["late_reason"] or risk)
                    lines.append(f"• Neden girilebilir: {why}")
                    lines.append(f"• Neden girilmez/beklenir: {risk}")
                    lines.append(f"• Güvenlik: {r['security_label']} | Exit (satış/çıkış uygulanabilirliği): {r['execution_quality']}")
                    if dq and dq["empirical_probability"] is not None and int(dq["empirical_n"] or 0)>=8:
                        lo=float(dq["empirical_ci_low"] or 0)*100
                        hi=float(dq["empirical_ci_high"] or 0)*100
                        lines.append(f"• Geçmiş olasılık: +%10 hedefi %{100*float(dq['empirical_probability']):.0f} (n={int(dq['empirical_n'])}, %95 aralık %{lo:.0f}–%{hi:.0f})")
                else:
                    ev=arr(r["evidence_json"]) if "evidence_json" in r.keys() else []
                    why=ev[0] if ev else "erken aktivite görülüyor"
                    lines.append(f"• Neden izlenir: {why}")
                    lines.append("• Neden henüz girilmez: güvenlik/çıkış ve devam teyidi tamamlanmadı")
                lines.append("")
                shown+=1

        lines.append("Not: GÜÇLÜ = yön + güvenlik + çıkış tarafında en çok dayanak; otomatik al emri değildir.")

    msg="\n".join(lines)
    token=(os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        print(msg); return
    chat=resolve_chat_id(token,(os.getenv("TELEGRAM_CHAT_ID") or "").strip(),DB,"Gate Web3 Motor")
    send_telegram(token,chat,msg[:3900])
    print("Gate trader Telegram sent")

if __name__=="__main__":
    main()
