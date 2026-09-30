#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Trader-facing Gate Web3 Telegram summary.

Security is fail-closed and separate from directional signal strength.
Only tokens without a security hard veto can appear in GÜÇLÜ/ORTA/ZAYIF.
"""
import json, os, sqlite3, math
import requests
from datetime import datetime, timezone, timedelta
from binance_notify import resolve_chat_id, send_telegram

DB=os.getenv("AVCI_DB","avci2.db")

# Telegram adaylari eski/agır tarama snapshot'ına körü körüne güvenmez.
# Bu taban scanner.py içindeki frozen MIN_LIQUIDITY=15000 ile aynıdır;
# yeni bir performans eşiği değildir, mesaj katmanını mevcut tradability kuralıyla hizalar.
GECKO_BASE_URL="https://api.geckoterminal.com/api/v2"
LIVE_MIN_LIQUIDITY_USD=15000.0
LIVE_TIMEOUT_SECONDS=12
GATE_TRADER_MAX_SPOT_AGE_SECONDS=int(os.getenv("GATE_TRADER_MAX_SPOT_AGE_SECONDS","900"))
GATE_TRADER_MAX_HEAVY_AGE_SECONDS=int(os.getenv("GATE_TRADER_MAX_HEAVY_AGE_SECONDS","7200"))

NOTIFY_REAPPEAR_HOURS=6

def init_notification_state(c):
    c.execute("""CREATE TABLE IF NOT EXISTS trader_notification_state(
      source TEXT NOT NULL,lane TEXT NOT NULL,asset_key TEXT NOT NULL,
      last_label TEXT,last_score REAL,last_reignition INTEGER NOT NULL DEFAULT 0,
      last_seen_utc TEXT NOT NULL,last_notified_utc TEXT,
      PRIMARY KEY(source,lane,asset_key)
    )""")

def notification_decision(c,source,lane,asset_key,label,score,seen_at,reignition=False):
    """Telegram-only dedupe. Never changes signal selection, scoring, or research logs."""
    init_notification_state(c)
    now_text=seen_at.isoformat()
    old=c.execute("""SELECT * FROM trader_notification_state
        WHERE source=? AND lane=? AND asset_key=?""",(source,lane,asset_key)).fetchone()
    event=None
    if old is None:
        event="NEW"
    else:
        try:
            last_seen=datetime.fromisoformat(str(old["last_seen_utc"]).replace("Z","+00:00"))
            gap_h=max(0.0,(seen_at-last_seen).total_seconds()/3600.0)
        except Exception:
            gap_h=0.0
        rank={"ZAYIF":1,"ORTA":2,"GÜÇLÜ":3}
        old_label=str(old["last_label"] or "")
        if gap_h>=NOTIFY_REAPPEAR_HOURS:
            event="REIGNITED"
        elif label!=old_label:
            if rank.get(label,0)>rank.get(old_label,0):
                event="STRENGTHENED"
            else:
                event="CHANGED"
        elif reignition and not int(old["last_reignition"] or 0):
            event="REIGNITED"
        elif score is not None and old["last_score"] is not None and float(score)-float(old["last_score"])>=3:
            event="STRENGTHENED"

    last_notified=(now_text if event else (old["last_notified_utc"] if old else None))
    c.execute("""INSERT OR REPLACE INTO trader_notification_state
      (source,lane,asset_key,last_label,last_score,last_reignition,last_seen_utc,last_notified_utc)
      VALUES(?,?,?,?,?,?,?,?)""",
      (source,lane,asset_key,label,score,1 if reignition else 0,now_text,last_notified))
    return event

def notification_prefix(event):
    return {
        "NEW":"🆕 YENİ",
        "STRENGTHENED":"⬆️ GÜÇLENDİ",
        "REIGNITED":"🔥 YENİDEN CANLANDI",
        "CHANGED":"↔️ DURUM DEĞİŞTİ",
    }.get(event,"")

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

def record_label(c,batch,network,contract,name,label,price,scan_ts):
    try:t=datetime.fromtimestamp(int(scan_ts),timezone.utc)
    except Exception:t=datetime.now(timezone.utc)
    asset=f"{network}:{contract}"
    c.execute("""INSERT OR IGNORE INTO trader_label_ledger
      (source,batch_key,asset_key,display_name,label,signal_time_utc,entry_price,due_at_utc,status)
      VALUES('GATE',?,?,?,?,?,?,?,'OPEN')""",
      (str(batch),asset,name,label,t.isoformat(),price,(t+timedelta(hours=72)).isoformat()))

def money(v):
    try:
        x=float(v)
        if x>=1_000_000:return "$"+f"{x/1_000_000:.1f}M"
        if x>=1_000:return "$"+f"{x/1_000:.0f}K"
        return "$"+f"{x:.0f}"
    except Exception:
        return "-"

def _live_num(v):
    try:
        return float(v or 0)
    except (TypeError,ValueError):
        return 0.0


def live_contract_snapshot(network,contract):
    """Fresh GeckoTerminal revalidation immediately before Telegram output.

    Fail-closed: if live data cannot be verified, the token is not allowed to
    appear as GÜÇLÜ/ORTA/ZAYIF in the trader-facing message. Multiple pools for
    the same contract are aggregated for liquidity; price/flow comes from the
    deepest current pool.
    """
    try:
        url=f"{GECKO_BASE_URL}/networks/{network}/tokens/{contract}/pools"
        r=requests.get(
            url,
            params={"include":"base_token,quote_token"},
            headers={"accept":"application/json;version=20230203"},
            timeout=LIVE_TIMEOUT_SECONDS,
        )
        r.raise_for_status()
        payload=r.json()
        pools=[]
        for pool in payload.get("data") or []:
            a=pool.get("attributes") or {}
            liq=_live_num(a.get("reserve_in_usd"))
            if liq<=0:
                continue
            changes=a.get("price_change_percentage") or {}
            tx=a.get("transactions") or {}
            m5=tx.get("m5") or {}
            pools.append({
                "liquidity":liq,
                "price":_live_num(a.get("base_token_price_usd")),
                "change_24h":_live_num(changes.get("h24")),
                "buys_5m":_live_num(m5.get("buys")),
                "sells_5m":_live_num(m5.get("sells")),
                "pool_address":a.get("address") or "",
            })
        if not pools:
            return {"ok":False,"reason":"canlı havuz verisi yok"}
        deepest=max(pools,key=lambda x:x["liquidity"])
        return {
            "ok":True,
            "reason":None,
            "liquidity":sum(x["liquidity"] for x in pools),
            "deepest_liquidity":deepest["liquidity"],
            "price":deepest["price"],
            "change_24h":deepest["change_24h"],
            "buys_5m":deepest["buys_5m"],
            "sells_5m":deepest["sells_5m"],
            "pool_count":len(pools),
            "pool_address":deepest["pool_address"],
        }
    except Exception as e:
        return {"ok":False,"reason":f"canlı Web3 doğrulaması başarısız: {type(e).__name__}"}


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

def plain_late(value):
    return {"LOW":"Düşük","MEDIUM":"Orta","HIGH":"Yüksek"}.get(str(value or "").upper(),"Bilinmiyor")

def plain_decision(label):
    return {
        "GÜÇLÜ":("🟢","İZLEMEYE DEĞER"),
        "ORTA":("🟡","BEKLE / TAKİP ET"),
        "ZAYIF":("🔴","ŞİMDİLİK GİRME"),
    }.get(label,("⚪️","BELİRSİZ"))

def plain_security(value):
    return {
        "STRONG":"güvenlik teyidi güçlü",
        "MEDIUM":"bazı güvenlik kontrolleri eksik/uyarı var",
        "WEAK":"güvenlik zayıf",
        "UNKNOWN":"güvenlik verisi eksik",
    }.get(str(value or "").upper(),str(value or "bilinmiyor"))

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
        heavy_age_seconds=int(datetime.now(timezone.utc).timestamp())-int(health["scan_ts"])
        heavy_fresh=(-60 <= heavy_age_seconds <= GATE_TRADER_MAX_HEAVY_AGE_SECONDS)

        rows=[]
        if heavy_fresh and table(c,"gate_candidate_evidence") and table(c,"trade_readiness") and table(c,"gate_security_confidence_history"):
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
                  AND e.version=(SELECT version FROM gate_candidate_evidence
                    WHERE batch_id=? ORDER BY created_at_utc DESC LIMIT 1)
                ORDER BY e.evidence_count DESC,e.counter_count ASC""",(batch,batch)).fetchall()

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
        init_label_ledger(c)
        init_notification_state(c)
        notify_now=datetime.now(timezone.utc)
        cap_status,cap_reason=capital_status(c)
        cap_line="🔒 Gerçek para kapısı kapalı" if cap_status!="OPEN" else "🔓 Gerçek para kapısı açık"
        lines=["🛰 GATE WEB3 AVCI",
               f"• İzlenen: {health['observed_tokens']} token | {cap_line}",
               ""]

        if not heavy_fresh:
            lines.append("⚠️ ANA WEB3 TARAMASI TAZE DEĞİL")
            lines.append("• Eski ağır taramadan aday göstermedim; yeni geçerli Web3 taraması bekleniyor.")
        elif not ranked:
            lines.append("🔴 ALINABİLİR ADAY YOK")
            lines.append("• Şu an güvenlik + çıkış kontrollerini geçen token çıkmadı.")
        else:
            icons={"GÜÇLÜ":"🟢","ORTA":"🟡","ZAYIF":"⚪️"}
            shown=0
            live_rejected=[]
            for _,_,_,r,label,score,dq in ranked:
                if shown>=3: break
                network=r["network_id"]
                contract=r["token_contract"]
                name=(r["symbol"] if "symbol" in r.keys() and r["symbol"] else symbol_for(c,network,contract))
                asset_key=f"{network}:{contract}"

                # Son ağır taramada iyi görünen token dakikalar içinde likidite
                # kaybedebilir. Telegram'a aday basmadan hemen önce canlı Web3
                # revalidation zorunludur. Doğrulanamayan veya mevcut frozen
                # likidite tabanının altına düşen token fail-closed elenir.
                live=live_contract_snapshot(network,contract)
                if not live.get("ok"):
                    live_rejected.append((name,live.get("reason") or "canlı veri yok"))
                    continue
                if float(live.get("liquidity") or 0)<LIVE_MIN_LIQUIDITY_USD:
                    live_rejected.append((name,f"canlı likidite {money(live.get('liquidity'))} < {money(LIVE_MIN_LIQUIDITY_USD)}"))
                    continue

                notify_event=notification_decision(
                    c,"GATE","CANDIDATE",asset_key,label,score,notify_now,False
                )
                if not notify_event:
                    continue

                icon,decision=plain_decision(label)
                prefix=notification_prefix(notify_event)
                lines.append(f"{icon} {prefix} | {name} [{network}] — {decision}")
                obs=c.execute("""SELECT price,change_24h,liquidity,buys_5m,sells_5m,own_volume_ratio
                    FROM gate_early_observations WHERE batch_id=? AND network_id=? AND token_contract=? LIMIT 1""",
                    (batch,network,contract)).fetchone() if table(c,"gate_early_observations") else None

                flow=0.0
                vr="-"
                if obs:
                    entry_price=float(live.get("price") or obs["price"] or 0)
                    record_label(c,batch,network,contract,name,label,entry_price,health["scan_ts"])
                    b=float(live.get("buys_5m") or 0); sv=float(live.get("sells_5m") or 0)
                    flow=(b/max(sv,1.0)) if b+sv else 0
                    vr="-" if obs["own_volume_ratio"] is None else f"{float(obs['own_volume_ratio']):.1f}x"

                if "support_json" in r.keys():
                    sup=arr(r["support_json"]); con=arr(r["counter_json"])
                    why=sup[0] if sup else "birden fazla on-chain veri aynı yönde"
                    risk=con[0] if con else "kritik karşı sinyal yok"
                else:
                    ev=arr(r["evidence_json"]) if "evidence_json" in r.keys() else []
                    why=ev[0] if ev else "erken aktivite görülüyor"
                    risk="güvenlik/çıkış ve devam teyidi tamamlanmadı"

                late="Bilinmiyor"
                if dq:
                    late=plain_late(dq["late_risk"])
                    if dq["late_risk"]!="LOW":
                        risk=str(dq["late_reason"] or risk)

                lines.append(f"• Durum: 24s %{float(live.get('change_24h') or 0):+.1f} | likidite {money(live.get('liquidity'))} | alıcı/satıcı {flow:.1f}x")
                lines.append(f"• Neden: {why}")
                lines.append(f"• Risk: {risk}")
                lines.append(f"• Güvenlik: {plain_security(r['security_label'] if 'security_label' in r.keys() else 'UNKNOWN')} | satış/çıkış: {r['execution_quality'] if 'execution_quality' in r.keys() else 'henüz teyit yok'}")
                lines.append(f"• Geç kalma: {late}")

                hist_line,_=gate_history_context(c,network,contract)
                lines.append(f"• Geçmiş koşu: {hist_line.strip('()')}")
                cmp=None
                if "historical_candidate_n" in r.keys():
                    cmp=historical_control_line(r["historical_candidate_n"],r["historical_control_n"],
                                                r["hit10_rate"],r["hit10_control"])
                if cmp:
                    lines.append(cmp)
                elif dq and dq["empirical_probability"] is not None and int(dq["empirical_n"] or 0)>=30:
                    lines.append(f"• Benzer geçmiş: +%10'a ulaşma %{100*float(dq['empirical_probability']):.0f} "
                                 f"(n={int(dq['empirical_n'])}); uygun kontrol kıyası olmadığı için avantaj yorumu yapılmıyor")
                else:
                    lines.append("• Sistem farkı: güvenilir karşılaştırma için henüz yeterli geçmiş örnek yok")
                lines.append(f"• Kontrat: {contract}")
                lines.append("")
                shown+=1

            if shown==0:
                lines.append("ℹ️ Yeni/değişen aday yok; aynı durumdaki tokenlar Telegram'da tekrar edilmedi.")
            if live_rejected:
                preview="; ".join(f"{n}: {reason}" for n,reason in live_rejected[:3])
                extra=len(live_rejected)-3
                if extra>0:
                    preview+=f"; +{extra} aday daha"
                lines.append(f"• Canlı doğrulamada elenen: {preview}")

        # Observation-only Gate early lane. This is intentionally separate
        # from security-passed GÜÇLÜ/ORTA/ZAYIF candidates.
        early_rows=[]
        spot_fresh=False
        spot_batch=None
        if table(c,"gate_spot_health"):
            latest_spot=c.execute("""SELECT batch_id,scan_ts,status
                FROM gate_spot_health ORDER BY scan_ts DESC LIMIT 1""").fetchone()
            if latest_spot and latest_spot["status"]=="VALID":
                age_seconds=int(datetime.now(timezone.utc).timestamp())-int(latest_spot["scan_ts"])
                spot_fresh=(-60 <= age_seconds <= GATE_TRADER_MAX_SPOT_AGE_SECONDS)
                if spot_fresh:
                    spot_batch=latest_spot["batch_id"]
        if spot_fresh and table(c,"gate_spot_watch"):
            try:
                latest_watch_batch=c.execute("""SELECT w.batch_id,g.scan_ts
                    FROM gate_spot_watch w
                    JOIN gate_spot_health g ON g.batch_id=w.batch_id
                    WHERE w.status='PAPER_WATCH' AND g.status='VALID'
                      AND g.scan_ts>=?
                    ORDER BY g.scan_ts DESC LIMIT 1""",
                    (int(datetime.now(timezone.utc).timestamp())-GATE_TRADER_MAX_SPOT_AGE_SECONDS,)).fetchone()
                if latest_watch_batch:
                    early_rows=c.execute("""SELECT w.pair,w.change_24h,w.rise_pct,
                               w.round_trip_1k_pct,w.entry_path,c.network_id,c.token_contract
                        FROM gate_spot_watch w
                        JOIN gate_spot_contracts c ON c.pair=w.pair
                        WHERE w.status='PAPER_WATCH' AND w.batch_id=?
                          AND w.change_24h < 10
                        ORDER BY w.rowid DESC LIMIT 3""",
                        (latest_watch_batch["batch_id"],)).fetchall()
            except sqlite3.OperationalError:
                early_rows=[]
        if spot_fresh and spot_batch and table(c,"gate_opportunity_observations"):
            cols={row[1] for row in c.execute("PRAGMA table_info(gate_opportunity_observations)")}
            if "early_watch" in cols:
                opportunity_rows=c.execute("""SELECT o.pair,h.change_24h,NULL rise_pct,
                           NULL round_trip_1k_pct,'OPPORTUNITY' entry_path,
                           c.network_id,c.token_contract
                    FROM gate_opportunity_observations o
                    JOIN gate_spot_history h ON h.batch_id=o.batch_id AND h.pair=o.pair
                    JOIN gate_spot_contracts c ON c.pair=o.pair
                    WHERE o.batch_id=? AND o.early_watch=1 AND h.change_24h<10
                    ORDER BY CASE WHEN o.early_watch_reason_json LIKE '%ONCHAIN_ANOMALY%' THEN 0 ELSE 1 END,
                             COALESCE(o.volume_acceleration,0) DESC LIMIT 3""",(spot_batch,)).fetchall()
                existing={(r["network_id"],r["token_contract"]) for r in early_rows}
                for r in opportunity_rows:
                    key=(r["network_id"],r["token_contract"])
                    if key not in existing:
                        early_rows.append(r)
                        existing.add(key)
                early_rows=early_rows[:3]
        if not spot_fresh:
            lines.append("")
            lines.append("⚠️ ERKEN İZLEME VERİSİ TAZE DEĞİL")
            lines.append("• Eski Spot snapshot'ından coin göstermedim; yeni Gate Spot taraması bekleniyor.")
        if early_rows:
            early_lines=[]
            used=set()
            for e in early_rows:
                if e["pair"] in used: continue
                used.add(e["pair"])
                asset_key=f"{e['network_id']}:{e['token_contract']}"
                early_score=float(e["rise_pct"] or e["change_24h"] or 0)
                early_label=str(e["entry_path"] or "EARLY_WATCH")
                notify_event=notification_decision(
                    c,"GATE","EARLY",asset_key,early_label,early_score,notify_now,False
                )
                if not notify_event:
                    continue
                early_lines.append(f"🟡 {notification_prefix(notify_event)} | {e['pair']} — İZLE")
                early_lines.append(f"• Hareket: 24s %{float(e['change_24h'] or 0):+.1f}")
                early_lines.append("• Neden: erken hareket var ama güvenlik ve satılabilirlik henüz tamamlanmadı.")
                early_lines.append("• Karar: Henüz alma; tüm kontrollerin geçmesini bekle.")
                if len(used)>=3: break
            if early_lines:
                lines.append("")
                lines.append("🟡 ERKEN İZLEME")
                lines.extend(early_lines)
                lines.append("• 🟡 = erken izleme; alım sinyali değil.")

        # Full Gate Spot coverage audit. Prefer the all-tradable-pairs audit so
        # low-liquidity movers are still visible as diagnostics; never promote
        # them into GÜÇLÜ/ORTA/ZAYIF or bypass security/tradability gates.
        if spot_fresh and spot_batch and table(c,"gate_full_mover_coverage"):
            cov=c.execute("""SELECT * FROM gate_full_mover_coverage
                WHERE spot_batch_id=? AND current_change_24h>=40
                ORDER BY current_change_24h DESC""",(spot_batch,)).fetchall()
            if cov:
                lines.append("")
                lines.append(f"⚪ KAÇIRILAN / GEÇ YAKALANANLAR — +%40 ({len(cov)})")
                status_text={
                    "OUTSIDE_CORE_LIQUIDITY":"gördü; çekirdek hacim filtresinin dışında",
                    "NO_CONTRACT_MAPPING":"Spot'ta gördü; Web3 kontratı eşleşmedi",
                    "NO_ONCHAIN_HISTORY":"Spot'ta gördü; erken on-chain geçmiş yok",
                    "SEEN_NO_ANOMALY":"Web3 gördü; erken anomali üretmedi",
                    "EARLY_CAUGHT":"erken yakaladı",
                    "CAUGHT":"yakaladı",
                    "LATE_CAUGHT":"geç yakaladı",
                }
                for a in cov[:4]:
                    label=status_text.get(a["coverage_status"],a["coverage_status"])
                    symbol=a["symbol"] or a["pair"]
                    lines.append(f"⚪ {symbol} %{float(a['current_change_24h']):+.1f} — {label}")
                if len(cov)>4:
                    lines.append(f"• +{len(cov)-4} büyük hareket daha DB'de kayıtlı.")
                lines.append("• Bu bölüm sadece sistemin kaçırma/erken yakalama denetimidir.")
        elif spot_fresh and spot_batch and table(c,"gate_top_mover_audit"):
            audits=c.execute("""SELECT * FROM gate_top_mover_audit
                WHERE spot_batch_id=?
                  AND audit_status IN ('MISSED','LATE_CAUGHT','NO_CONTRACT_MAPPING','NO_ONCHAIN_HISTORY')
                ORDER BY current_change_24h DESC""",(spot_batch,)).fetchall()
            if audits:
                lines.append("")
                lines.append(f"⚪ KAÇIRILAN / GEÇ YAKALANANLAR ({len(audits)})")
                status_text={
                    "MISSED":"kaçırdı",
                    "LATE_CAUGHT":"geç gördü",
                    "NO_CONTRACT_MAPPING":"Web3 ile eşleşmedi",
                    "NO_ONCHAIN_HISTORY":"erken geçmiş yok",
                }
                shown_audits=audits[:6]
                for a in shown_audits:
                    label=status_text.get(a["audit_status"],a["audit_status"])
                    symbol=a["symbol"] or a["pair"]
                    lines.append(f"⚪ {symbol} %+{float(a['current_change_24h']):.1f} — {label}")
                if len(audits)>len(shown_audits):
                    lines.append(f"• +{len(audits)-len(shown_audits)} olay daha DB'de kayıtlı.")
                lines.append("• Bunlar öneri değil; sistemin kaçırma denetimidir.")

        try:
            selected_early=[line for line in lines if line.startswith("• ") and " | 24s %" in line]
            print("Gate Telegram early-watch selected:", " || ".join(selected_early[:3]) if selected_early else "NONE")
        except Exception:
            pass
        lines.append("")
        lines.append("Renkler: 🟢 güçlü aday | 🟡 izle/bekle | 🔴 girme | ⚪ kaçırılan/geç")
        lines.append("Not: Teknik ayrıntılar DB'de kalır; sistem otomatik emir vermez.")
        c.commit()

    msg="\n".join(lines)
    token=(os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        print(msg); return
    chat=resolve_chat_id(token,(os.getenv("TELEGRAM_CHAT_ID") or "").strip(),DB,"Gate Web3 Motor")
    send_telegram(token,chat,msg[:3900])
    print("Gate trader Telegram sent")

if __name__=="__main__":
    main()
