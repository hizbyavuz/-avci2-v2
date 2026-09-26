#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Trade-readiness research gate for Avci.

Separates discovery/evidence from execution readiness. Research-only: it does
not place orders and does not modify frozen scanner thresholds.
"""
import json, os, sqlite3, sys
from datetime import datetime, timezone

VERSION="trade-readiness-v1-20260926"

def now(): return datetime.now(timezone.utc).isoformat()
def table(c,t):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone() is not None
def cols(c,t):
    return {r[1] for r in c.execute(f"PRAGMA table_info({t})")} if table(c,t) else set()
def obj(s):
    try: return json.loads(s or "{}")
    except Exception: return {}

def init(c):
    c.execute("""CREATE TABLE IF NOT EXISTS trade_readiness(
      source TEXT NOT NULL,
      batch_key TEXT NOT NULL,
      asset_key TEXT NOT NULL,
      display_name TEXT NOT NULL,
      readiness TEXT NOT NULL,
      passed_count INTEGER NOT NULL,
      failed_count INTEGER NOT NULL,
      unknown_count INTEGER NOT NULL,
      live_confirm TEXT,
      evidence_tier TEXT,
      historical_edge TEXT,
      execution_quality TEXT,
      regime_quality TEXT,
      data_quality TEXT,
      microstructure_quality TEXT,
      reasons_json TEXT NOT NULL,
      blockers_json TEXT NOT NULL,
      unknowns_json TEXT NOT NULL,
      version TEXT NOT NULL,
      created_at_utc TEXT NOT NULL,
      PRIMARY KEY(source,batch_key,asset_key,version)
    )""")

def classify(passed,failed,unknown,critical_fail=False):
    if critical_fail: return "NOT_READY"
    if failed==0 and passed>=5 and unknown<=2: return "PAPER_ELIGIBLE"
    if passed>=3 and failed<=1: return "WATCH"
    return "NOT_READY"

def binance(c):
    scan=c.execute("""SELECT * FROM scans WHERE health_status!='INVALID'
      ORDER BY scan_time_utc DESC LIMIT 1""").fetchone()
    if not scan or not table(c,"binance_candidate_evidence"): return 0
    ts=scan["scan_time_utc"]
    rows=c.execute("""SELECT e.*,f.price,f.change_24h,f.btc_relative_24h,f.climax_risk,
      f.taker_buy_ratio_15m,f.retention,f.reignition,f.trigger,
      p.status pool_status,p.confirmation_score pool_score
      FROM binance_candidate_evidence e
      JOIN features f ON f.scan_time_utc=e.scan_time_utc AND f.symbol=e.symbol
      LEFT JOIN binance_live_pool p ON p.scan_time_utc=e.scan_time_utc AND p.symbol=e.symbol
      WHERE e.scan_time_utc=?""",(ts,)).fetchall()
    n=0
    for r in rows:
        good=[]; bad=[]; unk=[]
        # 1) live persistence
        if r["pool_status"]=="CONFIRMED": good.append("15dk canlı teyit geçti")
        elif r["pool_status"]=="BORDERLINE": unk.append("15dk canlı teyit sınırda")
        elif r["pool_status"] in ("FADED","INSUFFICIENT"): bad.append("15dk canlı teyit geçmedi")
        else: unk.append("15dk canlı teyit yok")
        # 2) evidence quality
        if r["summary"] in ("DAYANAK_COK_GUCLU","DAYANAK_GUCLU"): good.append("çoklu dayanak güçlü")
        elif r["summary"]=="DAYANAK_ORTA": unk.append("dayanak orta")
        else: bad.append("dayanak zayıf")
        # 3) historical edge
        hist_known=(r["historical_candidate_n"] or 0)>=8 and (r["historical_control_n"] or 0)>=8
        if hist_known and r["hit10_rate"] is not None and r["hit10_control"] is not None:
            if float(r["hit10_rate"])>float(r["hit10_control"])*1.25:
                good.append("benzer geçmiş +10 kontrolün üstünde")
            elif float(r["hit10_rate"])<=float(r["hit10_control"]):
                bad.append("benzer geçmiş +10 kontrolü geçemiyor")
            else: unk.append("geçmiş edge zayıf")
        else: unk.append("geçmiş örnek yetersiz")
        # 4) execution quality from event/depth
        ev=c.execute("""SELECT spread_bps,buy_impact_1k_bps,buy_impact_5k_bps
          FROM signal_events WHERE symbol=? AND signal_time_utc<=?
          ORDER BY signal_time_utc DESC LIMIT 1""",(r["symbol"],ts)).fetchone() if table(c,"signal_events") else None
        if ev and ev["spread_bps"] is not None:
            spread=float(ev["spread_bps"])
            imp=float(ev["buy_impact_1k_bps"]) if ev["buy_impact_1k_bps"] is not None else None
            if spread<=20 and (imp is None or imp<=35): good.append("spread/price-impact uygun")
            elif spread>30 or (imp is not None and imp>50): bad.append("execution maliyeti yüksek")
            else: unk.append("execution sınırda")
        else: unk.append("execution ölçümü eksik")
        # 5) market/microstructure
        if int(r["climax_risk"] or 0): bad.append("climax riski")
        else: good.append("climax riski görünmüyor")
        taker=float(r["taker_buy_ratio_15m"] or 0)
        if taker>=0.55: good.append("15dk taker alış akışı olumlu")
        elif taker and taker<=0.45: bad.append("15dk taker satış baskısı")
        else: unk.append("taker akışı nötr")
        # 6) multi-scan context / market breadth
        ctx=c.execute("""SELECT * FROM binance_context_observations
          WHERE scan_time_utc=? AND symbol=? ORDER BY version DESC LIMIT 1""",
          (ts,r["symbol"])).fetchone() if table(c,"binance_context_observations") else None
        if ctx:
            if ctx["trajectory"]=="STRENGTHENING" and ctx["timeframe_alignment"]=="ALIGNED_UP":
                good.append("çoklu zaman dilimi ve son taramalar güçleniyor")
            elif ctx["trajectory"]=="WEAKENING":
                bad.append("son taramalarda momentum zayıflıyor")
            else:
                unk.append("çoklu zaman dilimi karışık")
            breadth=float(ctx["breadth_1h_positive_pct"] or 0)
            if breadth>=65:
                unk.append("piyasa geneli güçlü; coin ayrışması daha az seçici")
            elif breadth<=35 and float(r["btc_relative_24h"] or 0)>0:
                good.append("zayıf breadth içinde göreceli güç")
        else:
            unk.append("çoklu tarama bağlamı yok")

        # 7) cross-venue spot confirmation
        xv=c.execute("""SELECT summary,direction_agreement,confirmed_sources
          FROM crossvenue_spot_summary WHERE scan_time_utc=? AND symbol=?
          ORDER BY version DESC LIMIT 1""",(ts,r["symbol"])).fetchone() if table(c,"crossvenue_spot_summary") else None
        if xv:
            if xv["summary"]=="CONFIRMED" and int(xv["confirmed_sources"] or 0)>=2:
                good.append("OKX+Gate spot hareketi teyit ediyor")
            elif xv["summary"]=="DIVERGENT":
                bad.append("diğer spot borsalar hareketi teyit etmiyor")
            elif xv["summary"]=="PARTIAL":
                unk.append("çapraz-borsa teyidi kısmi")
            else:
                unk.append("çapraz-borsa verisi yetersiz")
        else:
            unk.append("çapraz-borsa spot teyidi yok")

        # 8) timestamped catalyst/news context
        cat=c.execute("""SELECT catalyst_state,headline_count,positive_count,negative_count
          FROM catalyst_observations WHERE scan_time_utc=? AND symbol=?
          ORDER BY version DESC LIMIT 1""",(ts,r["symbol"])).fetchone() if table(c,"catalyst_observations") else None
        if cat:
            if cat["catalyst_state"]=="RISK_CATALYST":
                bad.append("yakın zamanda risk/hack/delist/unlock haber izi var")
            elif cat["catalyst_state"]=="POSITIVE_CATALYST":
                good.append("timestamp'li pozitif katalizör/haber izi var")
            elif cat["catalyst_state"]=="NEWS_PRESENT":
                unk.append("haber var ama yönü net değil")
            else:
                unk.append("katalizör teyidi yok")
        else:
            unk.append("katalizör verisi yok")

        # 9) data quality
        cov=float(r["coverage_pct"] or 0)
        if cov>=75: good.append("veri kapsamı yüksek")
        elif cov<55: bad.append("veri kapsamı düşük")
        else: unk.append("veri kapsamı orta")
        if scan["data_mode"]=="SPOT_ONLY": unk.append("Binance-native futures eksik")
        else: good.append("spot+futures veri tam")
        critical=bool(int(r["climax_risk"] or 0)) or cov<40
        readiness=classify(len(good),len(bad),len(unk),critical)
        hist="GOOD" if any("geçmiş +10" in x for x in good) else ("BAD" if any("geçmiş +10" in x for x in bad) else "UNKNOWN")
        exe="GOOD" if any("spread/price-impact uygun"==x for x in good) else ("BAD" if any("execution maliyeti" in x for x in bad) else "UNKNOWN")
        live=r["pool_status"] or "UNKNOWN"
        micro="GOOD" if any("taker alış" in x for x in good) else ("BAD" if any("taker satış" in x for x in bad) else "NEUTRAL")
        dq="GOOD" if cov>=75 else ("BAD" if cov<55 else "MEDIUM")
        if int(r["climax_risk"] or 0):
            regime="CLIMAX"
        elif ctx and float(ctx["breadth_1h_positive_pct"] or 0)<=35 and float(r["btc_relative_24h"] or 0)>0:
            regime="SELECTIVE_STRENGTH"
        elif ctx and float(ctx["breadth_1h_positive_pct"] or 0)>=65:
            regime="BROAD_RALLY"
        else:
            regime="NORMAL"
        c.execute("""INSERT OR REPLACE INTO trade_readiness VALUES
          (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          ("BINANCE",ts,r["symbol"],r["symbol"],readiness,len(good),len(bad),len(unk),
           live,r["summary"],hist,exe,regime,dq,micro,
           json.dumps(good,ensure_ascii=False),json.dumps(bad,ensure_ascii=False),
           json.dumps(unk,ensure_ascii=False),VERSION,now()))
        n+=1
    c.commit(); return n

def gate(c):
    health=c.execute("""SELECT * FROM gate_scan_health WHERE status='VALID'
      ORDER BY scan_ts DESC LIMIT 1""").fetchone() if table(c,"gate_scan_health") else None
    if not health or not table(c,"gate_candidate_evidence"): return 0
    batch=health["batch_id"]
    rows=c.execute("""SELECT * FROM gate_candidate_evidence WHERE batch_id=?""",(batch,)).fetchall()
    n=0
    for r in rows:
        good=[]; bad=[]; unk=[]
        if r["summary"] in ("DAYANAK_COK_GUCLU","DAYANAK_GUCLU"): good.append("çoklu dayanak güçlü")
        elif r["summary"]=="DAYANAK_ORTA": unk.append("dayanak orta")
        else: bad.append("dayanak zayıf")
        hist_known=(r["historical_candidate_n"] or 0)>=8 and (r["historical_control_n"] or 0)>=8
        if hist_known and r["hit10_rate"] is not None and r["hit10_control"] is not None:
            if float(r["hit10_rate"])>float(r["hit10_control"])*1.25: good.append("benzer geçmiş +10 kontrolün üstünde")
            elif float(r["hit10_rate"])<=float(r["hit10_control"]): bad.append("benzer geçmiş +10 kontrolü geçemiyor")
            else: unk.append("geçmiş edge zayıf")
        else: unk.append("geçmiş örnek yetersiz")
        cov=float(r["coverage_pct"] or 0)
        if cov>=75: good.append("veri kapsamı yüksek")
        elif cov<55: bad.append("veri kapsamı düşük")
        else: unk.append("veri kapsamı orta")
        # Security / deception / exit evidence is already folded into Gate evidence.
        # Here we surface critical blockers directly when present in counter text.
        counter=[]
        try: counter=json.loads(r["counter_json"] or "[]")
        except Exception: pass
        joined=" | ".join(counter).lower()
        critical=False
        if "hard-veto" in joined or "güvenlik kontrolleri zayıf" in joined:
            bad.append("güvenlik engeli"); critical=True
        if "çıkış maliyeti yüksek" in joined:
            bad.append("gerçek çıkış maliyeti yüksek"); critical=True
        if "wash" in joined or "sybil" in joined:
            bad.append("manipülasyon/sybil riski")
        unknown=[]
        try: unknown=json.loads(r["unknown_json"] or "[]")
        except Exception: pass
        if any("exit quote" in str(x).lower() for x in unknown): unk.append("gerçek exit quote eksik")
        if any("wallet" in str(x).lower() for x in unknown): unk.append("wallet/funding graph eksik")
        readiness=classify(len(good),len(bad),len(unk),critical)
        hist="GOOD" if any("geçmiş +10" in x for x in good) else ("BAD" if any("geçmiş +10" in x for x in bad) else "UNKNOWN")
        dq="GOOD" if cov>=75 else ("BAD" if cov<55 else "MEDIUM")
        exe="BAD" if any("çıkış maliyeti" in x for x in bad) else ("UNKNOWN" if any("exit quote" in x for x in unk) else "GOOD")
        c.execute("""INSERT OR REPLACE INTO trade_readiness VALUES
          (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          ("GATE",str(batch),r["token_contract"],r["token_contract"][:8],readiness,
           len(good),len(bad),len(unk),"NOT_IMPLEMENTED",r["summary"],hist,exe,
           "UNKNOWN",dq,"UNKNOWN",
           json.dumps(good,ensure_ascii=False),json.dumps(bad,ensure_ascii=False),
           json.dumps(unk,ensure_ascii=False),VERSION,now()))
        n+=1
    c.commit(); return n

def main():
    mode=(sys.argv[1] if len(sys.argv)>1 else "").lower()
    db=os.getenv("BINANCE_DB","binance_avci2.db") if mode=="binance" else os.getenv("AVCI_DB","avci2.db")
    if mode not in ("binance","gate"): raise SystemExit("usage: trade_readiness_layer.py binance|gate")
    if not os.path.exists(db): print("readiness DB yok"); return
    with sqlite3.connect(db,timeout=30) as c:
        c.row_factory=sqlite3.Row; init(c)
        n=binance(c) if mode=="binance" else gate(c)
    print(f"trade readiness | {mode} | {n} asset")

if __name__=="__main__": main()
