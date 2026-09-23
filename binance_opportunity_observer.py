"""Observational opportunity layer for Binance Avci.
Does not change frozen candidate scores, thresholds, or selection.
Adds: missed-mover audit, leader/follower context, silent accumulation/acceleration.
"""
import json, sqlite3, statistics
DB="binance_avci2.db"
VERSION="opportunity-v0.1-20260923"

def med(xs):
    xs=[float(x) for x in xs if x is not None]
    return statistics.median(xs) if xs else None

def main(path=DB):
    con=sqlite3.connect(path, timeout=60)
    con.row_factory=sqlite3.Row
    try:
        scan=con.execute("SELECT scan_time_utc FROM scans WHERE health_status!='INVALID' ORDER BY scan_time_utc DESC LIMIT 1").fetchone()
        if not scan:
            print("Opportunity layer: geçerli Binance taraması yok"); return
        ts=scan[0]
        rows=con.execute("""SELECT symbol,stage,is_signal,is_selected,selection_class,
            change_15m,change_1h,change_24h,volume_mult_15m,volume_mult_1h,
            volume_rarity_pct,cross_sectional_rarity_pct,taker_buy_ratio_15m,
            retention_proxy,persistence,reignition,climax_risk
            FROM features WHERE scan_time_utc=?""",(ts,)).fetchall()
        if not rows:
            print("Opportunity layer: feature yok"); return
        con.execute("""CREATE TABLE IF NOT EXISTS opportunity_observations(
            scan_time_utc TEXT NOT NULL,symbol TEXT NOT NULL,version TEXT NOT NULL,
            market_excess_15m REAL,leader_rank INTEGER,acceleration_ratio REAL,
            silent_accumulation INTEGER NOT NULL DEFAULT 0,
            missed_mover INTEGER NOT NULL DEFAULT 0,flags_json TEXT NOT NULL,
            PRIMARY KEY(scan_time_utc,symbol,version))""")
        market=med([r["change_15m"] for r in rows])
        ranked=sorted(rows,key=lambda r:(float(r["change_15m"] or -999),float(r["volume_rarity_pct"] or -999)),reverse=True)
        rank={r["symbol"]:i+1 for i,r in enumerate(ranked)}
        written=missed=silent=0
        for r in rows:
            hist=con.execute("""SELECT volume_mult_15m FROM features
                WHERE symbol=? AND scan_time_utc<? AND volume_mult_15m IS NOT NULL
                ORDER BY scan_time_utc DESC LIMIT 6""",(r["symbol"],ts)).fetchall()
            hmed=med([x[0] for x in hist])
            accel=(float(r["volume_mult_15m"])/hmed) if hmed and hmed>0 and r["volume_mult_15m"] is not None else None
            excess=(float(r["change_15m"])-market) if market is not None and r["change_15m"] is not None else None
            flags=[]
            if rank[r["symbol"]]<=5 and float(r["change_15m"] or 0)>0: flags.append("MARKET_LEADER")
            if accel is not None and accel>=1.5: flags.append("VOLUME_ACCELERATION")
            sa=bool(accel is not None and accel>=1.5
                    and -1.0 <= float(r["change_15m"] or 0) <= 5.0
                    and float(r["taker_buy_ratio_15m"] or 0)>=0.55
                    and float(r["retention_proxy"] or 0)>=0.50
                    and not int(r["climax_risk"] or 0))
            if sa: flags.append("SILENT_ACCUMULATION"); silent+=1
            mm=bool(float(r["change_24h"] or 0)>=15.0 and not int(r["is_signal"] or 0))
            if mm: flags.append("MISSED_MOVER"); missed+=1
            # follower = positive but not top leader, with accelerating participation
            if rank[r["symbol"]]>5 and accel is not None and accel>=1.35 and float(r["change_15m"] or 0)>0:
                flags.append("POSSIBLE_FOLLOWER")
            con.execute("""INSERT OR REPLACE INTO opportunity_observations
                VALUES(?,?,?,?,?,?,?,?,?)""",
                (ts,r["symbol"],VERSION,excess,rank[r["symbol"]],accel,int(sa),int(mm),json.dumps(flags)))
            written+=1
        con.commit()
        print(f"Binance opportunity: {written} coin; silent={silent}, missed>=15%={missed}")
    finally:
        con.close()
if __name__=="__main__": main()
