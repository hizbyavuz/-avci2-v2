#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Binance candidate evidence research engine.

Builds a transparent evidence dossier for every current candidate.
It is descriptive/research-only: no order placement, no frozen-rule changes,
and no probability is invented when the historical peer sample is too small.
"""
import json, os, sqlite3
from datetime import datetime, timezone

DB=os.getenv("BINANCE_DB","binance_avci2.db")
VERSION="binance-evidence-research-v1.1-20260926"
TARGETS=(5,10,15)
MIN_PEER_N=8

def table(c,t):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone() is not None

def obj(s):
    try: return json.loads(s or "{}")
    except Exception: return {}

def target_value(d,target):
    for k in (str(target), f"{float(target):.1f}"):
        if k in d:
            return d[k]
    return None

def reached(row,target):
    try:
        d=json.loads(row["reach_json"] or "{}")
        v=target_value(d,target)
        if isinstance(v,bool): return v
        if isinstance(v,(int,float)): return bool(v)
    except Exception: pass
    return False

def historical_context(c,feat):
    # Strict peer cohort first: same engine + stage + BTC regime.
    rows=c.execute("""SELECT s.event_class,s.stage,s.engine,s.btc_regime,o.reach_json,o.label_status
        FROM signal_events s JOIN outcome_labels o ON o.event_id=s.event_id
        WHERE o.label_status='CLOSED'
          AND s.event_class IN ('CANDIDATE','NEAR_MISS','RANDOM_CONTROL')
          AND s.engine=? AND s.stage=? AND s.btc_regime=?""",
        (feat["engine"],feat["stage"],feat["btc_regime"])).fetchall()
    strict=True
    cand=[r for r in rows if r["event_class"]=="CANDIDATE"]
    ctrl=[r for r in rows if r["event_class"] in ("NEAR_MISS","RANDOM_CONTROL")]
    if len(cand)<MIN_PEER_N or len(ctrl)<MIN_PEER_N:
        strict=False
        rows=c.execute("""SELECT s.event_class,s.stage,s.engine,s.btc_regime,o.reach_json,o.label_status
            FROM signal_events s JOIN outcome_labels o ON o.event_id=s.event_id
            WHERE o.label_status='CLOSED'
              AND s.event_class IN ('CANDIDATE','NEAR_MISS','RANDOM_CONTROL')
              AND s.engine=?""",(feat["engine"],)).fetchall()
        cand=[r for r in rows if r["event_class"]=="CANDIDATE"]
        ctrl=[r for r in rows if r["event_class"] in ("NEAR_MISS","RANDOM_CONTROL")]
    out={"peer_mode":"STRICT_STAGE_ENGINE_REGIME" if strict else "ENGINE_ONLY",
         "candidate_n":len(cand),"control_n":len(ctrl),"rates":{}}
    for t in TARGETS:
        cr=sum(reached(r,t) for r in cand)/len(cand) if len(cand)>=MIN_PEER_N else None
        br=sum(reached(r,t) for r in ctrl)/len(ctrl) if len(ctrl)>=MIN_PEER_N else None
        out["rates"][str(t)]={"candidate_rate":cr,"control_rate":br,
                              "lift":(cr/br if cr is not None and br not in (None,0) else None)}
    return out

def main():
    if not os.path.exists(DB):
        print("Binance evidence: DB yok"); return
    with sqlite3.connect(DB,timeout=30) as c:
        c.row_factory=sqlite3.Row
        c.execute("""CREATE TABLE IF NOT EXISTS binance_candidate_evidence(
            scan_time_utc TEXT NOT NULL,symbol TEXT NOT NULL,version TEXT NOT NULL,
            evidence_count INTEGER NOT NULL,counter_count INTEGER NOT NULL,
            unknown_count INTEGER NOT NULL,coverage_pct REAL NOT NULL,
            historical_candidate_n INTEGER,historical_control_n INTEGER,
            peer_mode TEXT,hit5_rate REAL,hit5_control REAL,hit5_lift REAL,
            hit10_rate REAL,hit10_control REAL,hit10_lift REAL,
            hit15_rate REAL,hit15_control REAL,hit15_lift REAL,
            support_json TEXT NOT NULL,counter_json TEXT NOT NULL,unknown_json TEXT NOT NULL,
            summary TEXT NOT NULL,created_at_utc TEXT NOT NULL,
            PRIMARY KEY(scan_time_utc,symbol,version))""")
        scan=c.execute("""SELECT * FROM scans WHERE health_status!='INVALID'
            ORDER BY scan_time_utc DESC LIMIT 1""").fetchone()
        if not scan: print("Binance evidence: valid scan yok"); return
        ts=scan["scan_time_utc"]
        if table(c,"binance_live_pool"):
            candidates=c.execute("""SELECT f.* FROM features f
                JOIN binance_live_pool p
                  ON p.scan_time_utc=f.scan_time_utc AND p.symbol=f.symbol
                WHERE f.scan_time_utc=?
                  AND p.status IN ('CONFIRMED','BORDERLINE')
                ORDER BY CASE p.status WHEN 'CONFIRMED' THEN 0 ELSE 1 END,
                         p.confirmation_score DESC,p.pool_score DESC
                LIMIT 10""",(ts,)).fetchall()
        else:
            candidates=c.execute("""SELECT * FROM features WHERE scan_time_utc=?
                AND selection_class='CANDIDATE'
                ORDER BY score DESC,cross_sectional_rarity_pct DESC""",(ts,)).fetchall()
        for f in candidates:
            support=[]; counter=[]; unknown=[]
            pool=None
            if table(c,"binance_live_pool"):
                pool=c.execute("""SELECT status,confirmation_score,reason_json
                    FROM binance_live_pool WHERE scan_time_utc=? AND symbol=?
                    ORDER BY version DESC LIMIT 1""",(ts,f["symbol"])).fetchone()
            if pool:
                if pool["status"]=="CONFIRMED":
                    support.append("15dk canlı izleme teyidi geçti")
                elif pool["status"]=="BORDERLINE":
                    unknown.append("15dk canlı izleme sınırda kaldı")
            # Historical / structural
            if float(f["btc_relative_24h"] or 0)>=2: support.append("BTC'den belirgin güçlü")
            elif float(f["btc_relative_24h"] or 0)<=-1: counter.append("BTC'ye göre zayıf")
            if float(f["cross_sectional_rarity_pct"] or 0)>=75: support.append("Piyasaya göre sıra dışı hareket")
            elif f["cross_sectional_rarity_pct"] is None: unknown.append("Piyasa rarity verisi yok")
            if int(f["retention"] or 0): support.append("İlk hareketi koruyor")
            elif float(f["retention_proxy"] or 0)<0.35: counter.append("İlk hareketi koruyamıyor")
            if int(f["reignition"] or 0): support.append("Yeniden hızlanma var")
            if int(f["trigger"] or 0): support.append("Canlı tetik şartları oluşmuş")
            if int(f["climax_risk"] or 0): counter.append("Hareket fazla uzamış olabilir")
            if float(f["taker_buy_ratio_15m"] or 0)>=0.58: support.append("Piyasa emirlerinde alış üstün")
            elif float(f["taker_buy_ratio_15m"] or 0)<=0.45: counter.append("Piyasa emirlerinde satış üstün")

            flow=c.execute("""SELECT * FROM flow_observations WHERE scan_time_utc=? AND symbol=?
                ORDER BY rowid DESC LIMIT 1""",(ts,f["symbol"])).fetchone() if table(c,"flow_observations") else None
            if flow:
                tape=obj(flow["tape_json"])
                ni=tape.get("net_taker_notional")
                lbuy=tape.get("large_buy_share")
                if ni is not None:
                    if float(ni)>5000: support.append("Net gerçek alım akışı güçlü")
                    elif float(ni)<-5000: counter.append("Net gerçek para akışı satış yönünde")
                if lbuy is not None and float(lbuy)>=0.6: support.append("Büyük işlemlerde alış baskısı")
                if flow["book_imbalance"] is not None:
                    bi=float(flow["book_imbalance"])
                    if bi>=0.08: support.append("Order-book alış tarafına eğik")
                    elif bi<=-0.08: counter.append("Order-book satış tarafına eğik")
            else: unknown.append("Para akışı ölçümü yok")

            st=c.execute("""SELECT * FROM structure_observations WHERE scan_time_utc=? AND symbol=?
                ORDER BY version DESC LIMIT 1""",(ts,f["symbol"])).fetchone() if table(c,"structure_observations") else None
            if st:
                flags=obj(st["structure_flags_json"])
                if isinstance(flags,list):
                    if "MARKET_OUTPERFORMANCE" in flags: support.append("Piyasa geneline göre ayrışıyor")
                    if "SECTOR_OUTPERFORMANCE" in flags: support.append("Sektörüne göre ayrışıyor")
                    if "ORDERBOOK_PRESSURE" in flags: support.append("Derinlikte alış baskısı var")
                    if "SPOT_LEAD" in flags: support.append("Hareket spot liderliğinde")
            else: unknown.append("Yapı/sector ölçümü yok")

            op=c.execute("""SELECT * FROM opportunity_observations WHERE scan_time_utc=? AND symbol=?
                ORDER BY version DESC LIMIT 1""",(ts,f["symbol"])).fetchone() if table(c,"opportunity_observations") else None
            if op:
                if int(op["silent_accumulation"] or 0): support.append("Fiyat kaçmadan hacim birikimi var")
                if int(op["possible_follower"] or 0): support.append("Liderleri takip eden erken hızlanma izi")
                if int(op["missed_mover"] or 0): counter.append("Hareketin önemli kısmı önceden olmuş olabilir")

            wb=c.execute("""SELECT * FROM winner_bridge_scores WHERE scan_time_utc=? AND symbol=?
                ORDER BY id DESC LIMIT 1""",(ts,f["symbol"])).fetchone() if table(c,"winner_bridge_scores") else None
            if wb:
                sim=float(wb["winner_similarity_pct"] or 0)
                if sim>=60: support.append(f"Geçmiş büyük hareket profiline %{sim:.0f} benziyor")
                elif sim<40: counter.append(f"Geçmiş winner benzerliği zayıf (%{sim:.0f})")
            else: unknown.append("Geçmiş winner karşılaştırması yok")

            deception=c.execute("""SELECT deception_risk,risk_flags_json,positive_evidence_json
                FROM binance_deception_evidence WHERE scan_time_utc=? AND symbol=?
                ORDER BY version DESC LIMIT 1""",(ts,f["symbol"])).fetchone() if table(c,"binance_deception_evidence") else None
            if deception:
                risk=(deception["deception_risk"] or "").upper()
                if risk in ("HIGH","YUKSEK"): counter.append("Manipülasyon/deception riski yüksek")
                elif risk in ("LOW","DUSUK"): support.append("Manipülasyon karşı-kanıtı olumlu")
            else: unknown.append("Manipülasyon karşı-kontrolü yok")

            ev=c.execute("""SELECT spread_bps,buy_impact_1k_bps,buy_impact_5k_bps
                FROM signal_events WHERE symbol=? AND event_class='CANDIDATE'
                AND signal_time_utc<=? ORDER BY signal_time_utc DESC LIMIT 1""",
                (f["symbol"],ts)).fetchone()
            if ev:
                if ev["spread_bps"] is not None and float(ev["spread_bps"])<=15: support.append("Spread düşük")
                if ev["buy_impact_1k_bps"] is not None and float(ev["buy_impact_1k_bps"])>35:
                    counter.append("$1k girişte fiyat etkisi yüksek")
            else: unknown.append("Execution ölçümü yok")

            if f["oi_change_1h_pct"] is not None:
                oi=float(f["oi_change_1h_pct"])
                if oi>0 and float(f["change_1h"] or 0)>0: support.append("OI ve fiyat birlikte yükseliyor")
                elif oi<0 and float(f["change_1h"] or 0)>0: support.append("Yükseliş short kapanışıyla destekleniyor")
            else:
                fb=c.execute("""SELECT * FROM deriv_fallback_observations WHERE scan_time_utc=? AND symbol=?
                    AND status='OK' ORDER BY created_at_utc DESC LIMIT 1""",(ts,f["symbol"])).fetchone() if table(c,"deriv_fallback_observations") else None
                if fb: unknown.append("Türev verisi yalnız OKX proxy; Binance-native değil")
                else: unknown.append("OI/funding yok")

            hist=historical_context(c,f)
            rates=hist["rates"]
            r10=rates["10"]
            if r10["candidate_rate"] is not None and r10["control_rate"] is not None:
                if r10["candidate_rate"]>r10["control_rate"]*1.25:
                    support.append("Benzer geçmiş adaylar +10'da kontrolden belirgin iyi")
                elif r10["candidate_rate"]<=r10["control_rate"]:
                    counter.append("Benzer geçmiş adaylar +10'da kontrolü geçememiş")
            else:
                unknown.append("Benzer kapanmış olay örneği +10 için yetersiz")

            total=len(support)+len(counter)+len(unknown)
            coverage=100*(len(support)+len(counter))/total if total else 0
            # This is a coverage-backed evidence balance, NOT a win probability.
            hist_known=(r10["candidate_rate"] is not None and r10["control_rate"] is not None
                        and hist["candidate_n"]>=8 and hist["control_n"]>=8)
            hist_adverse=hist_known and r10["candidate_rate"]<=r10["control_rate"]
            hist_strong=(hist_known and r10["candidate_rate"]>=0.45
                         and r10["candidate_rate"]>=1.5*max(r10["control_rate"],0.0001))
            if hist_adverse:
                summary="DAYANAK_ORTA" if len(support)>=3 and len(support)>len(counter) else "DAYANAK_ZAYIF"
            elif len(support)>=7 and len(counter)<=1 and coverage>=75 and hist_strong:
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
            c.execute("""INSERT OR REPLACE INTO binance_candidate_evidence VALUES
                (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (ts,f["symbol"],VERSION,len(support),len(counter),len(unknown),coverage,
                 hist["candidate_n"],hist["control_n"],hist["peer_mode"],
                 *vals,json.dumps(support,ensure_ascii=False),json.dumps(counter,ensure_ascii=False),
                 json.dumps(unknown,ensure_ascii=False),summary,datetime.now(timezone.utc).isoformat()))
            print(f"{f['symbol']} | {summary} | +{len(support)} / -{len(counter)} / ?{len(unknown)} | coverage %{coverage:.0f}")
        c.commit()

if __name__=="__main__": main()
