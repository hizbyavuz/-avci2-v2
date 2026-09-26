#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gate candidate evidence research engine.

Builds a transparent multi-layer evidence dossier for current Gate candidates.
No trading, no frozen-rule changes, and no invented probability when historical
peer samples are too small.
"""
import json, os, sqlite3
from datetime import datetime, timezone

OBS_DB=os.getenv("AVCI_DB","avci2.db")
VAL_DB=os.getenv("AVCI_VALIDATION_DB","avci_validation_v5.db")
VERSION="gate-evidence-research-v1.1-20260926"
TARGETS=(5,10,15)
MIN_PEER_N=8

def table(c,t):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone() is not None

def obj(s):
    try: return json.loads(s or "{}")
    except Exception: return {}

def historical_context(v,e):
    # Strict peer: same ruleset + BTC regime, then fallback to ruleset only.
    rows=v.execute("""SELECT group_type,rulesets,btc_regime,result_5,result_10,result_15,status
        FROM validation_events WHERE status='CLOSED_72H'
          AND group_type IN ('CANDIDATE','EXPANDED_CANDIDATE','NEAR_MISS','RANDOM_CONTROL')
          AND COALESCE(rulesets,'')=? AND COALESCE(btc_regime,'')=?""",
        (e["rulesets"] or "",e["btc_regime"] or "")).fetchall()
    strict=True
    cand=[r for r in rows if r["group_type"] in ("CANDIDATE","EXPANDED_CANDIDATE")]
    ctrl=[r for r in rows if r["group_type"] in ("NEAR_MISS","RANDOM_CONTROL")]
    if len(cand)<MIN_PEER_N or len(ctrl)<MIN_PEER_N:
        strict=False
        rows=v.execute("""SELECT group_type,rulesets,btc_regime,result_5,result_10,result_15,status
            FROM validation_events WHERE status='CLOSED_72H'
              AND group_type IN ('CANDIDATE','EXPANDED_CANDIDATE','NEAR_MISS','RANDOM_CONTROL')
              AND COALESCE(rulesets,'')=?""",(e["rulesets"] or "",)).fetchall()
        cand=[r for r in rows if r["group_type"] in ("CANDIDATE","EXPANDED_CANDIDATE")]
        ctrl=[r for r in rows if r["group_type"] in ("NEAR_MISS","RANDOM_CONTROL")]
    out={"peer_mode":"STRICT_RULESET_REGIME" if strict else "RULESET_ONLY",
         "candidate_n":len(cand),"control_n":len(ctrl),"rates":{}}
    for t in TARGETS:
        col=f"result_{t}"
        cr=sum(r[col]=="TARGET_FIRST" for r in cand)/len(cand) if len(cand)>=MIN_PEER_N else None
        br=sum(r[col]=="TARGET_FIRST" for r in ctrl)/len(ctrl) if len(ctrl)>=MIN_PEER_N else None
        out["rates"][str(t)]={"candidate_rate":cr,"control_rate":br,
                              "lift":(cr/br if cr is not None and br not in (None,0) else None)}
    return out

def main():
    if not os.path.exists(OBS_DB) or not os.path.exists(VAL_DB):
        print("Gate evidence: DB eksik"); return
    with sqlite3.connect(OBS_DB,timeout=30) as c, sqlite3.connect(VAL_DB,timeout=30) as v:
        c.row_factory=v.row_factory=sqlite3.Row
        c.execute("""CREATE TABLE IF NOT EXISTS gate_candidate_evidence(
            batch_id TEXT NOT NULL,validation_id INTEGER NOT NULL,network_id TEXT NOT NULL,
            token_contract TEXT NOT NULL,version TEXT NOT NULL,evidence_count INTEGER NOT NULL,
            counter_count INTEGER NOT NULL,unknown_count INTEGER NOT NULL,coverage_pct REAL NOT NULL,
            historical_candidate_n INTEGER,historical_control_n INTEGER,peer_mode TEXT,
            hit5_rate REAL,hit5_control REAL,hit5_lift REAL,
            hit10_rate REAL,hit10_control REAL,hit10_lift REAL,
            hit15_rate REAL,hit15_control REAL,hit15_lift REAL,
            support_json TEXT NOT NULL,counter_json TEXT NOT NULL,unknown_json TEXT NOT NULL,
            summary TEXT NOT NULL,created_at_utc TEXT NOT NULL,
            PRIMARY KEY(batch_id,validation_id,version))""")
        health=c.execute("""SELECT * FROM gate_scan_health WHERE status='VALID'
            ORDER BY scan_ts DESC LIMIT 1""").fetchone()
        if not health: print("Gate evidence: valid scan yok"); return
        batch=health["batch_id"]
        events=v.execute("""SELECT * FROM validation_events WHERE batch_id=?
            AND group_type IN ('CANDIDATE','EXPANDED_CANDIDATE') ORDER BY id""",(batch,)).fetchall()
        for e in events:
            support=[]; counter=[]; unknown=[]
            obs=c.execute("""SELECT * FROM gate_early_observations WHERE batch_id=?
                AND network_id=? AND token_contract=? LIMIT 1""",
                (batch,e["network_id"],e["token_contract"])).fetchone()
            if obs:
                vr=obs["own_volume_ratio"]
                buys=float(obs["buys_5m"] or 0); sells=float(obs["sells_5m"] or 0)
                day=float(obs["change_24h"] or 0)
                if vr is not None and float(vr)>=2.0: support.append("Kendi geçmişine göre hacim belirgin hızlanmış")
                elif vr is None: unknown.append("Hacim anomali oranı için geçmiş yetersiz")
                if buys>=1.3*max(sells,1) and buys+sells>=5: support.append("5dk gerçek alış işlemleri üstün")
                elif sells>=1.3*max(buys,1) and buys+sells>=5: counter.append("5dk satış işlemleri üstün")
                if day>40: counter.append("24s hareket çok ilerlemiş; geç kalma riski")
                elif -2<=day<=20: support.append("Fiyat henüz aşırı kaçmamış")
            else: unknown.append("Erken on-chain gözlem yok")

            buyers=c.execute("""SELECT buyers_5m,buyers_1h FROM gate_buyer_observations
                WHERE batch_id=? AND network_id=? AND token_contract=? LIMIT 1""",
                (batch,e["network_id"],e["token_contract"])).fetchone() if table(c,"gate_buyer_observations") else None
            if buyers and buyers["buyers_5m"] is not None and int(buyers["buyers_5m"])>=8:
                support.append("Yeni alıcı aktivitesi anlamlı")
            elif not buyers: unknown.append("Unique buyer verisi yok")

            wi=c.execute("""SELECT * FROM gate_wallet_intelligence WHERE batch_id=?
                AND network_id=? AND token_contract=? LIMIT 1""",
                (batch,e["network_id"],e["token_contract"])).fetchone() if table(c,"gate_wallet_intelligence") else None
            if wi:
                if wi["sybil_proxy"]: counter.append("Ortak funding/fresh-wallet kümesi şüpheli")
                elif wi["status"]=="OK": support.append("Wallet/funding graph belirgin sybil izi vermiyor")
                if wi["fresh_30d_ratio"] is not None and float(wi["fresh_30d_ratio"])>0.75:
                    counter.append("Alıcıların çoğu çok yeni cüzdan")
            else: unknown.append("Wallet/funding graph yok")

            sec=c.execute("""SELECT label,coverage_pct,pass_rate_pct,hard_veto FROM gate_security_confidence_history
                WHERE batch_id=? AND network_id=? AND token_contract=?
                ORDER BY scan_ts DESC LIMIT 1""",
                (batch,e["network_id"],e["token_contract"])).fetchone() if table(c,"gate_security_confidence_history") else None
            if sec:
                if sec["hard_veto"]: counter.append("Güvenlikte hard-veto var")
                elif (sec["pass_rate_pct"] or 0)>=70: support.append("Kontrat/LP/holder güvenlik kontrolleri güçlü")
                elif (sec["pass_rate_pct"] or 0)<50: counter.append("Güvenlik kontrolleri zayıf")
            else: unknown.append("Güvenlik confidence verisi yok")

            de=c.execute("""SELECT * FROM gate_deception_evidence WHERE batch_id=? AND network_id=?
                AND token_contract=? ORDER BY version DESC LIMIT 1""",
                (batch,e["network_id"],e["token_contract"])).fetchone() if table(c,"gate_deception_evidence") else None
            if de:
                risk=(de["deception_risk"] or "").upper()
                if risk in ("HIGH","YUKSEK"): counter.append("Manipülasyon/wash/sybil riski yüksek")
                elif risk in ("LOW","DUSUK"): support.append("Manipülasyon karşı-kontrolü olumlu")
            else: unknown.append("Manipülasyon karşı-kontrolü yok")

            snap=c.execute("""SELECT raw_json FROM snapshots WHERE network_id=? AND token_contract=?
                AND zaman_utc>=? ORDER BY id ASC LIMIT 1""",
                (e["network_id"],e["token_contract"],e["signal_iso"])).fetchone() if table(c,"snapshots") else None
            item=obj(snap[0]) if snap else {}
            q=(item.get("exit_1k") if e["network_id"]=="solana" else item.get("evm_exit_1k")) or {}
            if q.get("loss_pct") is not None:
                loss=float(q["loss_pct"])
                if loss<=1.5: support.append("$1k giriş/çıkış maliyeti makul")
                elif loss>3: counter.append("$1k çıkış maliyeti yüksek")
            else: unknown.append("Gerçek exit quote yok")
            holder=item.get("adjusted_holder") or {}
            lp=item.get("lp_protection") or {}
            if holder.get("top10_pct") is not None and float(holder["top10_pct"])>60:
                counter.append("Top10 holder yoğunluğu yüksek")
            if lp.get("protected_pct") is not None and float(lp["protected_pct"])>=80:
                support.append("LP koruması güçlü")

            op=None
            # Map Gate spot opportunity via symbol/pair only when snapshot exposes a symbol.
            sym=item.get("symbol")
            if sym and table(c,"gate_opportunity_observations"):
                op=c.execute("""SELECT * FROM gate_opportunity_observations
                    WHERE batch_id=? AND pair LIKE ? ORDER BY version DESC LIMIT 1""",
                    (batch,f"{sym}%")).fetchone()
            if op:
                if int(op["silent_accumulation"] or 0): support.append("Fiyat kaçmadan hacim birikimi var")
                if int(op["possible_follower"] or 0): support.append("Erken takipçi hızlanması var")
                if int(op["missed_mover"] or 0): counter.append("Hareketin önemli kısmı önceden olmuş olabilir")

            hist=historical_context(v,e)
            rates=hist["rates"]; r10=rates["10"]
            if r10["candidate_rate"] is not None and r10["control_rate"] is not None:
                if r10["candidate_rate"]>r10["control_rate"]*1.25:
                    support.append("Benzer geçmiş olaylar +10'da kontrolden belirgin iyi")
                elif r10["candidate_rate"]<=r10["control_rate"]:
                    counter.append("Benzer geçmiş olaylar +10'da kontrolü geçememiş")
            else: unknown.append("Benzer kapanmış olay örneği +10 için yetersiz")

            total=len(support)+len(counter)+len(unknown)
            coverage=100*(len(support)+len(counter))/total if total else 0
            hist_strong=(r10["candidate_rate"] is not None and r10["control_rate"] is not None
                         and r10["candidate_rate"]>=0.45
                         and r10["candidate_rate"]>=1.5*max(r10["control_rate"],0.0001)
                         and hist["candidate_n"]>=8 and hist["control_n"]>=8)
            if len(support)>=7 and len(counter)<=1 and coverage>=75 and hist_strong:
                summary="DAYANAK_COK_GUCLU"
            elif len(support)>=5 and len(support)>=2*max(1,len(counter)) and coverage>=60:
                summary="DAYANAK_GUCLU"
            elif len(support)>=3 and len(support)>len(counter):
                summary="DAYANAK_ORTA"
            else:
                summary="DAYANAK_ZAYIF"
            vals=[]
            for t in TARGETS:
                rr=rates[str(t)]
                vals.extend([rr["candidate_rate"],rr["control_rate"],rr["lift"]])
            c.execute("""INSERT OR REPLACE INTO gate_candidate_evidence VALUES
                (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(
                batch,e["id"],e["network_id"],e["token_contract"],VERSION,
                len(support),len(counter),len(unknown),coverage,
                hist["candidate_n"],hist["control_n"],hist["peer_mode"],*vals,
                json.dumps(support,ensure_ascii=False),json.dumps(counter,ensure_ascii=False),
                json.dumps(unknown,ensure_ascii=False),summary,datetime.now(timezone.utc).isoformat()))
            print(e["token_contract"][:10],summary,f"+{len(support)} -{len(counter)} ?{len(unknown)} coverage %{coverage:.0f}")
        c.commit()

if __name__=="__main__": main()
