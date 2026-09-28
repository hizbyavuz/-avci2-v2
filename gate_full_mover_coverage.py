#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Full Gate Spot mover coverage audit.

Observation-only. Audits every tradable Gate Spot mover in the latest VALID
snapshot, including low-liquidity names that the core opportunity layer
intentionally excludes. Never changes V5/V5.1 thresholds, candidate selection,
security gates, or Telegram buy/sell messaging.
"""
import os, sqlite3, time

DB=os.getenv("AVCI_DB","avci2.db")
VERSION="gate-full-mover-coverage-v1-20260929"
MOVER_MIN_CHANGE=10.0
CORE_MIN_VOLUME=30000.0

def table(c,name):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone() is not None

def ensure(c):
    c.execute("""CREATE TABLE IF NOT EXISTS gate_full_mover_coverage(
      spot_batch_id TEXT NOT NULL,
      pair TEXT NOT NULL,
      symbol TEXT,
      current_change_24h REAL NOT NULL,
      volume_24h REAL,
      move_bucket TEXT NOT NULL,
      core_liquidity_eligible INTEGER NOT NULL,
      official_contract_mapped INTEGER NOT NULL,
      network_id TEXT,
      token_contract TEXT,
      first_spot_seen_ts INTEGER,
      first_onchain_seen_ts INTEGER,
      first_anomaly_ts INTEGER,
      first_anomaly_change_24h REAL,
      core_audit_status TEXT,
      coverage_status TEXT NOT NULL,
      coverage_reason TEXT NOT NULL,
      created_scan_ts INTEGER NOT NULL,
      version TEXT NOT NULL,
      PRIMARY KEY(spot_batch_id,pair,version)
    )""")

def bucket(ch):
    if ch>=100:return "100_PLUS"
    if ch>=50:return "50_100"
    if ch>=40:return "40_50"
    if ch>=20:return "20_40"
    return "10_20"

def main():
    if not os.path.exists(DB):
        print("Gate full coverage: DB yok"); return
    with sqlite3.connect(DB,timeout=60) as c:
        c.row_factory=sqlite3.Row; ensure(c)
        if not table(c,"gate_spot_health") or not table(c,"gate_spot_history"):
            print("Gate full coverage: Spot tabloları yok"); return
        h=c.execute("""SELECT batch_id,scan_ts,status FROM gate_spot_health
                       ORDER BY scan_ts DESC LIMIT 1""").fetchone()
        if not h or h["status"]!="VALID":
            print("Gate full coverage: geçerli Spot snapshot yok"); return
        batch=h["batch_id"]; ts=int(h["scan_ts"] or time.time())
        rows=c.execute("""SELECT pair,symbol,change_24h,volume_24h
                          FROM gate_spot_history
                          WHERE batch_id=? AND COALESCE(change_24h,0)>=?
                          ORDER BY change_24h DESC""",(batch,MOVER_MIN_CHANGE)).fetchall()
        counts={}
        for r in rows:
            pair=r["pair"]; ch=float(r["change_24h"] or 0); vol=float(r["volume_24h"] or 0)
            mapped=[]
            if table(c,"gate_spot_contracts"):
                mapped=c.execute("""SELECT network_id,token_contract FROM gate_spot_contracts
                                    WHERE pair=?""",(pair,)).fetchall()
            network=mapped[0]["network_id"] if mapped else None
            contract=mapped[0]["token_contract"] if mapped else None

            first_spot=c.execute("""SELECT MIN(s.scan_ts) ts
              FROM gate_spot_health s JOIN gate_spot_history x ON x.batch_id=s.batch_id
              WHERE x.pair=? AND s.status='VALID'""",(pair,)).fetchone()["ts"]

            first_seen=first_anom=first_anom_ch=None
            if network and contract and table(c,"gate_early_observations"):
                q=c.execute("""SELECT scan_ts,change_24h,observed_anomaly
                  FROM gate_early_observations
                  WHERE network_id=? AND token_contract=? AND scan_ts<=?
                  ORDER BY scan_ts ASC""",(network,contract,ts)).fetchall()
                if q:
                    first_seen=q[0]["scan_ts"]
                    a=next((x for x in q if int(x["observed_anomaly"] or 0)==1),None)
                    if a:
                        first_anom=a["scan_ts"]; first_anom_ch=a["change_24h"]

            core_status=None
            if table(c,"gate_top_mover_audit"):
                q=c.execute("""SELECT audit_status FROM gate_top_mover_audit
                               WHERE spot_batch_id=? AND pair=?""",(batch,pair)).fetchone()
                core_status=q["audit_status"] if q else None

            core_ok=int(vol>=CORE_MIN_VOLUME)
            if not core_ok:
                status="OUTSIDE_CORE_LIQUIDITY"
                reason=f"Spot mover görüldü; 24s hacim {vol:.0f}, çekirdek audit eşiği {CORE_MIN_VOLUME:.0f} altında"
            elif not mapped:
                status="NO_CONTRACT_MAPPING"
                reason="Spot mover görüldü; resmi Gate kontrat eşleşmesi yok"
            elif first_seen is None:
                status="NO_ONCHAIN_HISTORY"
                reason="Spot mover görüldü ve kontrat eşleşti; on-chain erken gözlem geçmişi yok"
            elif first_anom is None:
                status="SEEN_NO_ANOMALY"
                reason="On-chain tarama tokenı gördü fakat erken anomali üretmedi"
            elif float(first_anom_ch or 0)<10:
                status="EARLY_CAUGHT"
                reason=f"İlk on-chain anomali hareket %{float(first_anom_ch or 0):.1f} iken geldi"
            elif float(first_anom_ch or 0)<15:
                status="CAUGHT"
                reason=f"İlk on-chain anomali hareket %{float(first_anom_ch or 0):.1f} iken geldi"
            else:
                status="LATE_CAUGHT"
                reason=f"İlk on-chain anomali hareket %{float(first_anom_ch or 0):.1f} olduktan sonra geldi"

            counts[status]=counts.get(status,0)+1
            c.execute("""INSERT OR REPLACE INTO gate_full_mover_coverage VALUES(
              ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(
              batch,pair,r["symbol"],ch,vol,bucket(ch),core_ok,int(bool(mapped)),
              network,contract,first_spot,first_seen,first_anom,first_anom_ch,
              core_status,status,reason,ts,VERSION))
        c.commit()
        big=sum(1 for r in rows if float(r["change_24h"] or 0)>=40)
        print(f"Gate full coverage: movers>=10%={len(rows)} movers>=40%={big} {counts}")

if __name__=="__main__":
    main()
