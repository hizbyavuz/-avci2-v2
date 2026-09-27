"""Observational opportunity layer for Gate Avci.
Never changes frozen V5 scoring/security/selection.
Adds missed-mover attribution, market leaders/followers, silent accumulation,
and an accountability audit for every liquid Gate Spot mover above +10%.
"""
import json, sqlite3, statistics
DB="avci2.db"
VERSION="gate-opportunity-v0.3-20260927"
MOVER_MIN_CHANGE=10.0
MOVER_MIN_VOLUME=30000.0

def med(xs):
    xs=[float(x) for x in xs if x is not None]
    return statistics.median(xs) if xs else None

def ensure_columns(con):
    cols={row[1] for row in con.execute("PRAGMA table_info(gate_opportunity_observations)")}
    if "miss_reason_json" not in cols:
        con.execute("ALTER TABLE gate_opportunity_observations ADD COLUMN miss_reason_json TEXT NOT NULL DEFAULT '[]'")

def ensure_audit(con):
    con.execute("""CREATE TABLE IF NOT EXISTS gate_top_mover_audit(
        spot_batch_id TEXT NOT NULL,
        pair TEXT NOT NULL,
        symbol TEXT,
        current_change_24h REAL NOT NULL,
        volume_24h REAL,
        network_id TEXT,
        token_contract TEXT,
        first_observed_ts INTEGER,
        first_observed_change_24h REAL,
        first_anomaly_ts INTEGER,
        first_anomaly_change_24h REAL,
        audit_status TEXT NOT NULL,
        audit_reason TEXT NOT NULL,
        created_scan_ts INTEGER NOT NULL,
        PRIMARY KEY(spot_batch_id,pair)
    )""")

def audit_mover(con, health, row, contracts, early_table):
    """Classify whether on-chain Avci saw a Gate Spot mover early enough."""
    pair=row["pair"]
    current=float(row["change_24h"] or 0)
    if not contracts:
        return (None,None,None,None,None,None,
                "NO_CONTRACT_MAPPING",
                "Resmi Gate kontrat eşleşmesi yok; Web3 erken taramayla güvenilir eşleştirme yapılamadı")
    if not early_table:
        return (None,None,None,None,None,None,
                "NO_ONCHAIN_HISTORY",
                "On-chain erken gözlem geçmişi yok")

    since=int(health["scan_ts"])-86400
    best=None
    for network,contract in contracts:
        obs=con.execute("""SELECT scan_ts,change_24h,observed_anomaly
            FROM gate_early_observations
            WHERE network_id=? AND token_contract=? AND scan_ts>=?
            ORDER BY scan_ts ASC""",(network,contract,since)).fetchall()
        if not obs:
            continue
        first=obs[0]
        anomaly=next((x for x in obs if int(x["observed_anomaly"] or 0)==1),None)
        candidate=(network,contract,first,anomaly)
        # Prefer a mapped contract that actually produced an anomaly, then earliest observation.
        if best is None or (candidate[3] is not None and best[3] is None) or (
            candidate[3] is not None and best[3] is not None and candidate[3]["scan_ts"]<best[3]["scan_ts"]):
            best=candidate

    if best is None:
        return (contracts[0][0],contracts[0][1],None,None,None,None,
                "MISSED",
                "Kontrat eşleşti ancak son 24 saatte Web3 erken gözlem kaydı yok")

    network,contract,first,anomaly=best
    if anomaly is None:
        return (network,contract,first["scan_ts"],first["change_24h"],None,None,
                "MISSED",
                "Web3 taraması tokenı gördü fakat erken anomali üretmedi")

    ach=float(anomaly["change_24h"] or 0)
    if ach < 10:
        status="EARLY_CAUGHT"
        reason=f"İlk on-chain anomali 24s hareket yaklaşık %{ach:.1f} iken kaydedildi"
    elif ach < 15:
        status="CAUGHT"
        reason=f"İlk on-chain anomali hareket yaklaşık %{ach:.1f} iken kaydedildi"
    else:
        status="LATE_CAUGHT"
        reason=f"İlk on-chain anomali ancak hareket yaklaşık %{ach:.1f} olduktan sonra geldi"
    return (network,contract,first["scan_ts"],first["change_24h"],
            anomaly["scan_ts"],anomaly["change_24h"],status,reason)

def main(path=DB):
    con=sqlite3.connect(path, timeout=60); con.row_factory=sqlite3.Row
    try:
        health=con.execute("SELECT batch_id,scan_ts,status FROM gate_spot_health ORDER BY scan_ts DESC LIMIT 1").fetchone()
        if not health or health["status"]!="VALID":
            print("Gate opportunity: geçerli Spot snapshot yok"); return
        batch=health["batch_id"]
        rows=con.execute("""SELECT pair,symbol,last,volume_24h,change_24h
            FROM gate_spot_history WHERE batch_id=? AND volume_24h>=?""",
            (batch,MOVER_MIN_VOLUME)).fetchall()
        if not rows:
            print("Gate opportunity: yeterli hacimli pair yok"); return
        con.execute("""CREATE TABLE IF NOT EXISTS gate_opportunity_observations(
            batch_id TEXT NOT NULL,pair TEXT NOT NULL,version TEXT NOT NULL,
            return_rank INTEGER,volume_acceleration REAL,price_acceleration REAL,
            silent_accumulation INTEGER NOT NULL DEFAULT 0,
            possible_follower INTEGER NOT NULL DEFAULT 0,
            missed_mover INTEGER NOT NULL DEFAULT 0,miss_reason_json TEXT NOT NULL,
            flags_json TEXT NOT NULL,PRIMARY KEY(batch_id,pair,version))""")
        ensure_columns(con)
        ensure_audit(con)
        con.execute("DELETE FROM gate_top_mover_audit WHERE spot_batch_id=?",(batch,))

        ranked=sorted(rows,key=lambda r:(float(r["change_24h"]),float(r["volume_24h"])),reverse=True)
        rank={r["pair"]:i+1 for i,r in enumerate(ranked)}
        missed=silent=followers=0
        audit_counts={}
        early_table=con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='gate_early_observations'").fetchone()

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

            contracts=con.execute("""SELECT network_id,token_contract
                FROM gate_spot_contracts WHERE pair=?""",(r["pair"],)).fetchall()
            seen=False
            if contracts and early_table:
                for network,contract in contracts:
                    if con.execute("""SELECT 1 FROM gate_early_observations
                        WHERE network_id=? AND token_contract=? AND scan_ts>=? LIMIT 1""",
                        (network,contract,int(health["scan_ts"])-86400)).fetchone():
                        seen=True; break

            mm=bool(float(r["change_24h"])>=MOVER_MIN_CHANGE and not seen)
            reasons=[]
            if mm:
                flags.append("MISSED_MOVER"); missed+=1
                if not contracts: reasons.append("NO_VERIFIED_CONTRACT_MAPPING")
                elif not early_table: reasons.append("ONCHAIN_EARLY_HISTORY_UNAVAILABLE")
                else: reasons.append("NO_EARLY_ONCHAIN_OBSERVATION_RECORDED")
                if vacc is None: reasons.append("INSUFFICIENT_VOLUME_HISTORY")
                elif vacc<1.25: reasons.append("NO_VOLUME_ACCELERATION")

            con.execute("""INSERT OR REPLACE INTO gate_opportunity_observations
                (batch_id,pair,version,return_rank,volume_acceleration,
                 price_acceleration,silent_accumulation,possible_follower,
                 missed_mover,flags_json,miss_reason_json)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (batch,r["pair"],VERSION,rank[r["pair"]],vacc,pacc,int(sa),
                 int(follower),int(mm),json.dumps(flags),json.dumps(reasons)))

            if float(r["change_24h"])>=MOVER_MIN_CHANGE:
                audit=audit_mover(con,health,r,contracts,early_table)
                network,contract,fo_ts,fo_ch,fa_ts,fa_ch,status,reason=audit
                audit_counts[status]=audit_counts.get(status,0)+1
                con.execute("""INSERT OR REPLACE INTO gate_top_mover_audit(
                    spot_batch_id,pair,symbol,current_change_24h,volume_24h,
                    network_id,token_contract,first_observed_ts,first_observed_change_24h,
                    first_anomaly_ts,first_anomaly_change_24h,audit_status,audit_reason,
                    created_scan_ts)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (batch,r["pair"],r["symbol"],float(r["change_24h"]),
                     float(r["volume_24h"]),network,contract,fo_ts,fo_ch,fa_ts,fa_ch,
                     status,reason,int(health["scan_ts"])))

        con.commit()
        print(f"Gate opportunity: {len(rows)} pair; silent={silent}, follower={followers}, "
              f"missed>=10%={missed}; mover_audit={audit_counts}")
    finally: con.close()
if __name__=="__main__": main()
