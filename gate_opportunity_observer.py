"""Observational opportunity layer for Gate Avci.
Never changes frozen V5 scoring/security/selection. Uses Gate Spot history only.
Adds missed-mover audit, market leader/follower context, and silent accumulation.
"""
import json, sqlite3, statistics
DB="avci2.db"
VERSION="gate-opportunity-v0.1-20260923"

def med(xs):
    xs=[float(x) for x in xs if x is not None]
    return statistics.median(xs) if xs else None

def main(path=DB):
    con=sqlite3.connect(path, timeout=60); con.row_factory=sqlite3.Row
    try:
        health=con.execute("SELECT batch_id,scan_ts,status FROM gate_spot_health ORDER BY scan_ts DESC LIMIT 1").fetchone()
        if not health or health["status"]!="VALID":
            print("Gate opportunity: geçerli Spot snapshot yok"); return
        batch=health["batch_id"]
        rows=con.execute("""SELECT pair,symbol,last,volume_24h,change_24h
            FROM gate_spot_history WHERE batch_id=? AND volume_24h>=30000""",(batch,)).fetchall()
        if not rows:
            print("Gate opportunity: yeterli hacimli pair yok"); return
        con.execute("""CREATE TABLE IF NOT EXISTS gate_opportunity_observations(
            batch_id TEXT NOT NULL,pair TEXT NOT NULL,version TEXT NOT NULL,
            return_rank INTEGER,volume_acceleration REAL,price_acceleration REAL,
            silent_accumulation INTEGER NOT NULL DEFAULT 0,
            possible_follower INTEGER NOT NULL DEFAULT 0,
            missed_mover INTEGER NOT NULL DEFAULT 0,flags_json TEXT NOT NULL,
            PRIMARY KEY(batch_id,pair,version))""")
        ranked=sorted(rows,key=lambda r:(float(r["change_24h"]),float(r["volume_24h"])),reverse=True)
        rank={r["pair"]:i+1 for i,r in enumerate(ranked)}
        missed=silent=followers=0
        for r in rows:
            hist=con.execute("""SELECT h.volume_24h,h.change_24h,h.last
                FROM gate_spot_history h JOIN gate_spot_health s ON s.batch_id=h.batch_id
                WHERE h.pair=? AND h.batch_id<>? AND s.status='VALID'
                ORDER BY s.scan_ts DESC LIMIT 8""",(r["pair"],batch)).fetchall()
            vmed=med([x[0] for x in hist]); cmed=med([x[1] for x in hist])
            vacc=(float(r["volume_24h"])/vmed) if vmed and vmed>0 else None
            pacc=(float(r["change_24h"])-cmed) if cmed is not None else None
            flags=[]
            if rank[r["pair"]]<=10 and float(r["change_24h"])>0: flags.append("MARKET_LEADER")
            if vacc is not None and vacc>=1.35: flags.append("VOLUME_ACCELERATION")
            sa=bool(vacc is not None and vacc>=1.35 and -2<=float(r["change_24h"])<=12)
            if sa: flags.append("SILENT_ACCUMULATION"); silent+=1
            follower=bool(rank[r["pair"]]>10 and vacc is not None and vacc>=1.25
                          and pacc is not None and pacc>=2 and float(r["change_24h"])>0)
            if follower: flags.append("POSSIBLE_FOLLOWER"); followers+=1
            mapped=con.execute("SELECT 1 FROM gate_spot_contracts WHERE pair=? LIMIT 1",(r["pair"],)).fetchone()
            early_table=con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='gate_early_observations'").fetchone()
            seen=False
            if mapped and early_table:
                seen=bool(con.execute("""SELECT 1 FROM gate_early_observations e
                    JOIN gate_spot_contracts c ON c.network_id=e.network_id
                    AND c.token_contract=e.token_contract
                    WHERE c.pair=? AND e.scan_ts>=? LIMIT 1""",
                    (r["pair"],int(health["scan_ts"])-86400)).fetchone())
            mm=bool(float(r["change_24h"])>=15 and not seen)
            if mm: flags.append("MISSED_MOVER"); missed+=1
            con.execute("""INSERT OR REPLACE INTO gate_opportunity_observations
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (batch,r["pair"],VERSION,rank[r["pair"]],vacc,pacc,int(sa),int(follower),int(mm),json.dumps(flags)))
        con.commit()
        print(f"Gate opportunity: {len(rows)} pair; silent={silent}, follower={followers}, missed>=15%={missed}")
    finally:
        con.close()
if __name__=="__main__": main()
