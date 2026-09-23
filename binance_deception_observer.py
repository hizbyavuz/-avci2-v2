"""Anti-manipulation evidence layer for Binance Avci.

Observational only: it never changes frozen v1 candidate scores, thresholds,
selection, or simulated execution. CEX data cannot reveal wallet addresses, so
wallet/sybil/holder fields stay UNKNOWN unless a separately verified exact
contract source is attached later.
"""
import json
import sqlite3

DB="binance_avci2.db"
VERSION="binance-deception-evidence-v1-20260923"

def classify_cex_evidence(book_ratio=None, tape=None, cross_venue_confirmed=False):
    tape=tape or {}
    flags=[]
    positives=[]
    repeat=tape.get("repeated_notional_ratio")
    alternation=tape.get("side_alternation_ratio")
    concentration=tape.get("top5_notional_share")
    trades=tape.get("trade_count")
    if book_ratio is not None and (book_ratio >= 3.0 or book_ratio <= 1/3):
        flags.append("EXTREME_BOOK_IMBALANCE")
    if repeat is not None and trades is not None and trades >= 40 and repeat >= 0.35:
        flags.append("REPEATED_SIZE_PATTERN")
    if alternation is not None and trades is not None and trades >= 50 and alternation >= 0.82:
        flags.append("HIGH_SIDE_ALTERNATION")
    if concentration is not None and trades is not None and trades >= 20 and concentration >= 0.65:
        flags.append("TRADE_NOTIONAL_CONCENTRATION")
    if cross_venue_confirmed:
        positives.append("CROSS_VENUE_CONFIRMATION")
    if tape.get("large_buy_share") is not None and tape.get("large_trades",0) >= 5:
        if tape["large_buy_share"] >= 0.65:
            positives.append("LARGE_BUY_SUPPORT")
    risk="HIGH" if len(flags)>=3 else ("MEDIUM" if flags else "LOW_OBSERVED")
    return risk,flags,positives

def _columns(con,table):
    return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}

def main(path=DB):
    con=sqlite3.connect(path,timeout=60); con.row_factory=sqlite3.Row
    try:
        con.execute("""CREATE TABLE IF NOT EXISTS binance_deception_evidence(
          scan_time_utc TEXT NOT NULL,symbol TEXT NOT NULL,version TEXT NOT NULL,
          deception_risk TEXT NOT NULL,risk_flags_json TEXT NOT NULL,
          positive_evidence_json TEXT NOT NULL,book_bid_ask_ratio REAL,
          repeated_notional_ratio REAL,side_alternation_ratio REAL,
          top5_notional_share REAL,large_buy_share REAL,
          wallet_quality_status TEXT NOT NULL,sybil_status TEXT NOT NULL,
          holder_status TEXT NOT NULL,contract_mapping_status TEXT NOT NULL,
          PRIMARY KEY(scan_time_utc,symbol,version))""")
        scan=con.execute("""SELECT scan_time_utc FROM scans
          WHERE health_status!='INVALID' ORDER BY scan_time_utc DESC LIMIT 1""").fetchone()
        if not scan:
            print("Binance deception: geçerli tarama yok"); return 0
        ts=scan[0]
        rows=con.execute("""SELECT f.symbol,
          s.book_bid_ask_ratio
          FROM features f
          LEFT JOIN structure_observations s
            ON s.scan_time_utc=f.scan_time_utc AND s.symbol=f.symbol
          WHERE f.scan_time_utc=? AND (f.is_signal=1 OR f.is_selected=1)
          GROUP BY f.symbol""",(ts,)).fetchall()
        written=0
        for row in rows:
            flow=None
            try:
                flow=con.execute("""SELECT tape_json FROM flow_observations
                  WHERE symbol=? AND scan_time_utc=? ORDER BY rowid DESC LIMIT 1""",
                  (row["symbol"],ts)).fetchone()
            except sqlite3.OperationalError:
                pass
            tape={}
            if flow and flow[0]:
                try: tape=json.loads(flow[0]) or {}
                except (TypeError,json.JSONDecodeError): tape={}
            cross=False
            try:
                cross=bool(con.execute("""SELECT 1 FROM shared_signal_events
                  WHERE binance_symbol=? AND source='GATE_ONCHAIN'
                  AND abs(strftime('%s',signal_time_utc)-strftime('%s',?))<=86400
                  LIMIT 1""",(row["symbol"],ts)).fetchone())
            except sqlite3.OperationalError:
                pass
            risk,flags,pos=classify_cex_evidence(row["book_bid_ask_ratio"],tape,cross)
            con.execute("""INSERT OR REPLACE INTO binance_deception_evidence
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (ts,row["symbol"],VERSION,risk,json.dumps(flags),json.dumps(pos),
               row["book_bid_ask_ratio"],tape.get("repeated_notional_ratio"),
               tape.get("side_alternation_ratio"),tape.get("top5_notional_share"),
               tape.get("large_buy_share"),"UNKNOWN_CEX_NO_WALLETS",
               "UNKNOWN_CEX_NO_WALLETS","UNKNOWN_NEEDS_VERIFIED_CONTRACT",
               "VERIFIED" if cross else "UNKNOWN"))
            written+=1
        con.commit()
        print(f"Binance deception evidence: {written} kayıt; frozen v1 değişmedi.")
        return written
    finally:
        con.close()

if __name__=="__main__":
    main()
