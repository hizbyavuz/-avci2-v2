"""Anti-manipulation / counter-evidence layer for Gate Avci 2.

Observational only. It combines already collected on-chain safety evidence and
keeps unavailable wallet-age/funding-graph evidence explicitly UNKNOWN.
Frozen V5 rules are not changed.
"""
import json
import sqlite3
import time
from gate_early_observer import contract_key

DB="avci2.db"
VERSION="gate-deception-evidence-v1-20260923"

def classify_gate_evidence(top10=None,lp=None,wash=None,creator=None,buyer_ratio=None):
    flags=[]; positives=[]
    if top10 is not None:
        if top10 >= 85: flags.append("TOP10_CONCENTRATION_HIGH")
        elif top10 <= 60: positives.append("HOLDER_DISTRIBUTION_BETTER")
    if lp is not None:
        if lp < 50: flags.append("LP_PROTECTION_LOW")
        elif lp >= 80: positives.append("LP_PROTECTION_STRONG")
    if wash is True: flags.append("WASH_PROXY")
    if creator=="FLAGGED": flags.append("CREATOR_RISK_FLAGGED")
    if buyer_ratio is not None and buyer_ratio >= 1.5:
        positives.append("BUYER_BREADTH_GROWTH")
    risk="HIGH" if any(x in flags for x in ("CREATOR_RISK_FLAGGED","WASH_PROXY")) and len(flags)>=2 else ("MEDIUM" if flags else "LOW_OBSERVED")
    return risk,flags,positives

def main(path=DB):
    con=sqlite3.connect(path,timeout=60); con.row_factory=sqlite3.Row
    try:
        con.execute("""CREATE TABLE IF NOT EXISTS gate_deception_evidence(
          batch_id TEXT NOT NULL,scan_ts INTEGER NOT NULL,network_id TEXT NOT NULL,
          token_contract TEXT NOT NULL,version TEXT NOT NULL,deception_risk TEXT NOT NULL,
          risk_flags_json TEXT NOT NULL,positive_evidence_json TEXT NOT NULL,
          top10_adjusted_pct REAL,lp_protected_pct REAL,wash_proxy INTEGER,
          creator_risk_status TEXT,buyer_ratio REAL,wallet_age_status TEXT NOT NULL,
          funding_graph_status TEXT NOT NULL,sybil_status TEXT NOT NULL,
          PRIMARY KEY(batch_id,network_id,token_contract,version))""")
        health=con.execute("""SELECT batch_id,scan_ts FROM gate_scan_health
          WHERE status='VALID' ORDER BY scan_ts DESC LIMIT 1""").fetchone()
        if not health:
            print("Gate deception: geçerli tarama yok"); return 0
        batch,scan_ts=health["batch_id"],health["scan_ts"]
        try:
            risks=con.execute("""SELECT network_id,token_contract,top10_adjusted_pct,
              lp_protected_pct FROM gate_candidate_risk_history WHERE batch_id=?""",
              (batch,)).fetchall()
        except sqlite3.OperationalError:
            risks=[]
        written=0
        for r in risks:
            network=r["network_id"]; contract=contract_key(network,r["token_contract"])
            optional=None
            try:
                optional=con.execute("""SELECT creator_risk_status,sampled_wash_proxy
                  FROM gate_optional_context WHERE batch_id=? AND network_id=?
                  AND token_contract=?""",(batch,network,contract)).fetchone()
            except sqlite3.OperationalError:
                pass
            buyer_ratio=None
            try:
                cur=con.execute("""SELECT scan_ts,buyers_5m FROM gate_buyer_observations
                  WHERE network_id=? AND token_contract=? AND scan_ts<=?
                  ORDER BY scan_ts DESC LIMIT 1""",(network,contract,scan_ts)).fetchone()
                old=con.execute("""SELECT buyers_5m FROM gate_buyer_observations
                  WHERE network_id=? AND token_contract=? AND scan_ts<=?
                  AND buyers_5m>0 ORDER BY scan_ts DESC LIMIT 6""",
                  (network,contract,scan_ts-1200)).fetchall()
                vals=[x[0] for x in old if x[0]]
                if cur and cur[1] is not None and len(vals)>=3:
                    vals=sorted(float(x) for x in vals)
                    med=vals[len(vals)//2]
                    if med>0: buyer_ratio=float(cur[1])/med
            except sqlite3.OperationalError:
                pass
            wash=(bool(optional["sampled_wash_proxy"]) if optional and optional["sampled_wash_proxy"] is not None else None)
            creator=(optional["creator_risk_status"] if optional else None)
            risk,flags,pos=classify_gate_evidence(r["top10_adjusted_pct"],r["lp_protected_pct"],wash,creator,buyer_ratio)
            con.execute("""INSERT OR REPLACE INTO gate_deception_evidence
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (batch,scan_ts,network,contract,VERSION,risk,json.dumps(flags),
               json.dumps(pos),r["top10_adjusted_pct"],r["lp_protected_pct"],
               int(wash) if wash is not None else None,creator,buyer_ratio,
               "UNKNOWN_NOT_YET_SOURCED","UNKNOWN_NOT_YET_SOURCED",
               "PROXY_ONLY" if wash is not None else "UNKNOWN"))
            written+=1
        con.commit()
        print(f"Gate deception evidence: {written} kayıt; frozen V5 değişmedi.")
        return written
    finally:
        con.close()

if __name__=="__main__":
    main()
