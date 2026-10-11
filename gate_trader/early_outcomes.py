"""RAM-only forward measurement of early-watch alerts, not simulated trades."""
from __future__ import annotations
import time
from . import gate_api as G
from . import ledger as L

HORIZONS=(900,3600,10800)
SCHEMA="""
CREATE TABLE IF NOT EXISTS early_observations(
 id INTEGER PRIMARY KEY, contract TEXT NOT NULL, side TEXT NOT NULL,
 signal_ts INTEGER NOT NULL, price REAL NOT NULL, trigger REAL NOT NULL,
 volume_ratio REAL NOT NULL, UNIQUE(contract,side,signal_ts)
);
CREATE TABLE IF NOT EXISTS early_results(
 observation_id INTEGER NOT NULL, horizon_sec INTEGER NOT NULL,
 status TEXT NOT NULL, end_ts INTEGER, end_price REAL,
 return_pct REAL, mfe_pct REAL, mae_pct REAL,
 hit3 INTEGER, hit5 INTEGER, PRIMARY KEY(observation_id,horizon_sec)
);
"""
def init(db):
    db.executescript(SCHEMA)

def record(db,contract,watch,ts):
    init(db)
    db.execute("INSERT OR IGNORE INTO early_observations(contract,side,signal_ts,price,trigger,volume_ratio) VALUES(?,?,?,?,?,?)",
               (contract,watch["side"],int(ts),float(watch["price"]),float(watch["trigger"]),float(watch["volume_ratio"])))

def evaluate_path(side,entry,bars,signal_ts,horizon):
    """Bars: (open_ts, high, low, close); use only CLOSED candles."""
    selected=[b for b in bars if signal_ts<=b[0] and b[0]+300<=signal_ts+horizon]
    if not selected or selected[-1][0]+300<signal_ts+horizon:
        return None
    sign=1 if side=="LONG" else -1
    ret=sign*(selected[-1][3]/entry-1)*100
    ups=[sign*(b[1]/entry-1)*100 if sign==1 else sign*(b[2]/entry-1)*100 for b in selected]
    downs=[sign*(b[2]/entry-1)*100 if sign==1 else sign*(b[1]/entry-1)*100 for b in selected]
    mfe=max(ups);mae=min(downs)
    return (selected[-1][0]+300,selected[-1][3],ret,mfe,mae,int(mfe>=3),int(mfe>=5))

def track(now_ts=None):
    now_ts=time.time() if now_ts is None else now_ts
    resolved=0
    with L.connect() as db:
        init(db)
        pending=db.execute("""SELECT o.* FROM early_observations o
          WHERE EXISTS(SELECT 1 FROM (SELECT 900 AS h UNION ALL SELECT 3600 UNION ALL SELECT 10800) h
            WHERE NOT EXISTS(SELECT 1 FROM early_results r WHERE r.observation_id=o.id AND r.horizon_sec=h.h))
          ORDER BY o.signal_ts""").fetchall()
        for o in pending:
            if now_ts<o["signal_ts"]+900+300:continue
            try:
                series=G.candles_since(o["contract"],"5m",o["signal_ts"])
                bars=list(zip(series.t,series.h,series.l,series.c))
            except Exception as exc:
                print("EARLY_TRACK_ERROR",o["contract"],type(exc).__name__,flush=True)
                continue
            for h in HORIZONS:
                if now_ts<o["signal_ts"]+h+300:continue
                if db.execute("SELECT 1 FROM early_results WHERE observation_id=? AND horizon_sec=?",(o["id"],h)).fetchone():continue
                result=evaluate_path(o["side"],o["price"],bars,o["signal_ts"],h)
                if result is None:continue
                db.execute("""INSERT OR IGNORE INTO early_results
                  (observation_id,horizon_sec,status,end_ts,end_price,return_pct,mfe_pct,mae_pct,hit3,hit5)
                  VALUES(?,?,'RESOLVED',?,?,?,?,?,?,?,?)""",(o["id"],h,*result))
                resolved+=1
        if resolved:print("EARLY_RESULTS_RESOLVED",resolved,flush=True)
    return resolved

def summary(db):
    init(db)
    lines=["📊 ERKEN UYARI SONUÇLARI (işlem değil)"]
    for h,label in ((900,"15 dk"),(3600,"1 saat"),(10800,"3 saat")):
        r=db.execute("""SELECT COUNT(*) n, AVG(return_pct) avg_ret, AVG(hit3)*100 hit3,
          AVG(hit5)*100 hit5, SUM(CASE WHEN return_pct>0 THEN 1 ELSE 0 END) positive
          FROM early_results WHERE horizon_sec=? AND status='RESOLVED'""",(h,)).fetchone()
        if not r["n"]:lines.append(f"{label}: henüz sonuç yok");continue
        lines.append(f"{label}: {r['n']} gözlem | yön doğru {r['positive']}/{r['n']} | ort. %{r['avg_ret']:+.2f} | +%3 erişim %{r['hit3']:.0f} | +%5 erişim %{r['hit5']:.0f}")
    n=db.execute("SELECT COUNT(*) n FROM early_observations").fetchone()["n"]
    lines.append(f"Kaydedilen erken uyarı: {n} | RAM; yeniden başlatılırsa silinir")
    return "\n".join(lines)
