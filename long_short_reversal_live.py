#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Separate reversal live watcher.
Research/paper-alert only. Never places orders.
Sequence:
WATCH -> APPROACHING -> SWEEP_SEEN -> FAILED_BREAK_CONFIRMED
-> STRUCTURE_BREAK -> RETESTING -> TRIGGERED / INVALIDATED
"""
from __future__ import annotations
import json, os, sqlite3, time
from datetime import datetime, timezone
import requests
from binance_notify import resolve_chat_id

ANALYST_DB=os.getenv("LS_ANALYST_DB","long_short_analyst.db")
DB=os.getenv("LS_REVERSAL_DB","long_short_reversal_live.db")
POLL_SECONDS=float(os.getenv("LS_LIVE_POLL_SECONDS","15"))
RUN_SECONDS=float(os.getenv("LS_LIVE_RUN_SECONDS","250"))
MAX_WATCH=int(os.getenv("LS_REVERSAL_MAX_WATCH","12"))
APPROACH_PCT=float(os.getenv("LS_REVERSAL_APPROACH_PCT","0.35"))
SPOT_BASE="https://data-api.binance.vision"
TELEGRAM_LIMIT=4096

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def fmtp(x):
    if x is None: return "-"
    x=float(x)
    if abs(x)>=1000: return f"{x:,.2f}"
    if abs(x)>=1: return f"{x:.4f}"
    return f"{x:.8f}".rstrip("0")

def get(path,params=None):
    r=requests.get(SPOT_BASE+path,params=params or {},timeout=10,
                   headers={"User-Agent":"long-short-reversal/1.0"})
    r.raise_for_status()
    return r.json()

def init_db():
    with sqlite3.connect(DB) as con:
        con.execute("""CREATE TABLE IF NOT EXISTS watch_state(
            symbol TEXT PRIMARY KEY,
            direction TEXT NOT NULL,
            score INTEGER,
            data_mode TEXT,
            sweep_level REAL NOT NULL,
            micro_break_level REAL NOT NULL,
            retest_low REAL NOT NULL,
            retest_high REAL NOT NULL,
            invalidation REAL NOT NULL,
            target1 REAL,
            target2 REAL,
            stage TEXT NOT NULL DEFAULT 'WATCH',
            analyst_scan_time TEXT,
            last_price REAL,
            last_closed_5m REAL,
            last_closed_1m REAL,
            last_update_utc TEXT NOT NULL
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS events(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_time_utc TEXT NOT NULL,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            stage_from TEXT,
            stage_to TEXT NOT NULL,
            price REAL,
            closed_5m REAL,
            closed_1m REAL,
            payload_json TEXT
        )""")

def load_plans():
    if not os.path.exists(ANALYST_DB):
        return []
    with sqlite3.connect(ANALYST_DB) as con:
        con.row_factory=sqlite3.Row
        row=con.execute("SELECT MAX(scan_time_utc) ts FROM analyses").fetchone()
        if not row or not row["ts"]: return []
        rows=con.execute("""SELECT symbol,confidence,payload_json
                           FROM analyses WHERE scan_time_utc=?
                           ORDER BY confidence DESC""",(row["ts"],)).fetchall()
        out=[]
        for r in rows:
            try:
                p=json.loads(r["payload_json"] or "{}")
                q=p.get("reversal_plan")
                if not q: continue
                out.append({
                    "symbol":r["symbol"],"direction":q["direction"],
                    "score":int(q.get("score") or 0),
                    "data_mode":str(p.get("data_mode") or "UNKNOWN"),
                    "sweep_level":float(q["sweep_level"]),
                    "micro_break_level":float(q["micro_break_level"]),
                    "retest_low":float(q["retest_low"]),
                    "retest_high":float(q["retest_high"]),
                    "invalidation":float(q["invalidation"]),
                    "target1":float(q.get("target1") or 0),
                    "target2":float(q.get("target2") or 0),
                    "scan_time":row["ts"],
                })
            except Exception:
                continue
        out.sort(key=lambda x:x["score"],reverse=True)
        return out[:MAX_WATCH]

def sync(items):
    with sqlite3.connect(DB) as con:
        keep={x["symbol"] for x in items}
        for x in items:
            old=con.execute("""SELECT direction,sweep_level,micro_break_level,stage
                               FROM watch_state WHERE symbol=?""",(x["symbol"],)).fetchone()
            reset=(not old or old[0]!=x["direction"]
                   or abs(float(old[1])-x["sweep_level"])>max(1e-12,x["sweep_level"]*0.002)
                   or abs(float(old[2])-x["micro_break_level"])>max(1e-12,x["micro_break_level"]*0.002))
            if reset:
                con.execute("""INSERT OR REPLACE INTO watch_state(
                    symbol,direction,score,data_mode,sweep_level,micro_break_level,
                    retest_low,retest_high,invalidation,target1,target2,stage,
                    analyst_scan_time,last_price,last_closed_5m,last_closed_1m,last_update_utc
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,'WATCH',?,NULL,NULL,NULL,?)""",
                (x["symbol"],x["direction"],x["score"],x["data_mode"],x["sweep_level"],
                 x["micro_break_level"],x["retest_low"],x["retest_high"],x["invalidation"],
                 x["target1"],x["target2"],x["scan_time"],now_iso()))
            else:
                con.execute("""UPDATE watch_state SET score=?,data_mode=?,retest_low=?,retest_high=?,
                    invalidation=?,target1=?,target2=?,analyst_scan_time=?,last_update_utc=?
                    WHERE symbol=?""",
                    (x["score"],x["data_mode"],x["retest_low"],x["retest_high"],
                     x["invalidation"],x["target1"],x["target2"],x["scan_time"],now_iso(),x["symbol"]))
        if keep:
            q=",".join("?" for _ in keep)
            con.execute(f"DELETE FROM watch_state WHERE symbol NOT IN ({q})",tuple(keep))
        else:
            con.execute("DELETE FROM watch_state")

def snapshot(symbol):
    k5=get("/api/v3/klines",{"symbol":symbol,"interval":"5m","limit":4})
    k1=get("/api/v3/klines",{"symbol":symbol,"interval":"1m","limit":4})
    px=float(get("/api/v3/ticker/price",{"symbol":symbol})["price"])
    c5=k5[-2] if len(k5)>=2 else k5[-1]
    f5=k5[-1]
    c1=k1[-2] if len(k1)>=2 else k1[-1]
    return {
        "price":px,
        "closed5_open":float(c5[1]),"closed5_high":float(c5[2]),"closed5_low":float(c5[3]),
        "closed5_close":float(c5[4]),
        "forming5_high":float(f5[2]),"forming5_low":float(f5[3]),
        "closed1_open":float(c1[1]),"closed1_high":float(c1[2]),"closed1_low":float(c1[3]),
        "closed1_close":float(c1[4]),
        "closed5_time":datetime.fromtimestamp(int(c5[6])/1000,tz=timezone.utc).isoformat(),
        "closed1_time":datetime.fromtimestamp(int(c1[6])/1000,tz=timezone.utc).isoformat(),
    }

def next_stage(r,s):
    d=r["direction"]; stage=r["stage"]
    sweep=float(r["sweep_level"]); micro=float(r["micro_break_level"])
    rl=float(r["retest_low"]); rh=float(r["retest_high"]); inv=float(r["invalidation"])
    price=s["price"]; c5=s["closed5_close"]; c1=s["closed1_close"]; o1=s["closed1_open"]
    dist=abs(price/sweep-1.0)*100 if sweep else 999

    if d=="SHORT":
        swept=(s["forming5_high"]>=sweep or s["closed5_high"]>=sweep)
        failed=(s["closed5_high"]>=sweep and c5<sweep)
        structure_break=(c5<micro)
        in_retest=(rl<=price<=rh)
        one_min_confirm=(c1<o1 and c1<rl)
        if stage in ("FAILED_BREAK_CONFIRMED","STRUCTURE_BREAK","RETESTING") and c5>inv:
            return "INVALIDATED"
    else:
        swept=(s["forming5_low"]<=sweep or s["closed5_low"]<=sweep)
        failed=(s["closed5_low"]<=sweep and c5>sweep)
        structure_break=(c5>micro)
        in_retest=(rl<=price<=rh)
        one_min_confirm=(c1>o1 and c1>rh)
        if stage in ("FAILED_BREAK_CONFIRMED","STRUCTURE_BREAK","RETESTING") and c5<inv:
            return "INVALIDATED"

    if stage in ("WATCH","APPROACHING"):
        if swept: return "SWEEP_SEEN"
        if dist<=APPROACH_PCT: return "APPROACHING"
        return "WATCH"
    if stage=="SWEEP_SEEN":
        if failed: return "FAILED_BREAK_CONFIRMED"
        return "SWEEP_SEEN"
    if stage=="FAILED_BREAK_CONFIRMED":
        if structure_break: return "STRUCTURE_BREAK"
        return "FAILED_BREAK_CONFIRMED"
    if stage=="STRUCTURE_BREAK":
        if in_retest: return "RETESTING"
        return "STRUCTURE_BREAK"
    if stage=="RETESTING":
        if one_min_confirm: return "TRIGGERED"
        return "RETESTING"
    return stage

def msg(r,stage,s):
    sym=r["symbol"]; d=r["direction"]; sweep=float(r["sweep_level"])
    micro=float(r["micro_break_level"]); rl=float(r["retest_low"]); rh=float(r["retest_high"])
    inv=float(r["invalidation"]); t1=float(r["target1"] or 0); t2=float(r["target2"] or 0)
    head=f"🔁 DÖNÜŞ MOTORU | {sym} | {d}"
    if stage=="APPROACHING":
        return (f"{head}\n🟡 Dönüş bölgesine yaklaşıyor. Henüz giriş YOK.\n"
                f"İzlenen {'tepe' if d=='SHORT' else 'dip'}: {fmtp(sweep)}\n"
                f"Önce bu seviyenin süpürülmesini, sonra geri alınmasını bekliyoruz.")
    if stage=="SWEEP_SEEN":
        return (f"{head}\n🟠 LİKİDİTE SÜPÜRMESİ GÖRÜLDÜ\n"
                f"{'Eski tepenin üstü görüldü.' if d=='SHORT' else 'Eski dibin altı görüldü.'}\n"
                f"Henüz giriş YOK. 5dk mumun tekrar {'tepenin altına' if d=='SHORT' else 'dibin üstüne'} kapanması gerekiyor.")
    if stage=="FAILED_BREAK_CONFIRMED":
        return (f"{head}\n🟠 SAHTE KIRILIM TEYİDİ\n"
                f"5dk mum {fmtp(sweep)} seviyesini süpürüp geri kapandı.\n"
                f"Şimdi mikro yapı seviyesi {fmtp(micro)} {'aşağı' if d=='SHORT' else 'yukarı'} kırılmadan giriş YOK.")
    if stage=="STRUCTURE_BREAK":
        return (f"{head}\n🟡 MİKRO YAPI KIRILDI\n"
                f"İkinci şart tamam. Şimdi kırılan bölgeye retest bekleniyor: {fmtp(rl)}–{fmtp(rh)}\n"
                f"Henüz giriş YOK.")
    if stage=="RETESTING":
        return (f"{head}\n🟠 RETEST YAPILIYOR\n"
                f"Fiyat {fmtp(rl)}–{fmtp(rh)} bölgesini test ediyor.\n"
                f"Son teyit: 1dk mumun yeniden {'aşağı' if d=='SHORT' else 'yukarı'} kapanması.")
    if stage=="TRIGGERED":
        warn="" if r["data_mode"]=="BINANCE_FUTURES" else "\n⚠️ Grafik Spot verisine dayanıyor; Binance Futures akış teyidi yok."
        return (f"{head}\n{'🔴' if d=='SHORT' else '🟢'} DÖNÜŞ ŞARTLARI TAMAMLANDI{warn}\n"
                f"Fiyat: {fmtp(s['price'])}\n"
                f"✓ Likidite süpürmesi\n✓ 5dk sahte kırılım kapanışı\n✓ Mikro yapı kırılımı\n"
                f"✓ Retest\n✓ 1dk yön teyidi\n"
                f"❌ Fikir bozulur: {fmtp(inv)}\n🎯 Hedefler: {fmtp(t1)} | {fmtp(t2)}")
    if stage=="INVALIDATED":
        return (f"{head}\n⚪ DÖNÜŞ FİKRİ İPTAL\n"
                f"Fiyat {fmtp(inv)} yanlışlanma seviyesinin karşı tarafında 5dk kapandı.")
    return None

def send(text):
    token=(os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    configured=(os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    if not token:
        print(text); return
    chat=resolve_chat_id(token,configured,"binance_avci2.db","Long/Short Reversal")
    r=requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id":chat,"text":text[:TELEGRAM_LIMIT],"disable_web_page_preview":True},
                    timeout=10)
    r.raise_for_status()

def loop_once():
    with sqlite3.connect(DB) as con:
        con.row_factory=sqlite3.Row
        rows=con.execute("SELECT * FROM watch_state ORDER BY score DESC").fetchall()
        cache={}
        for r in rows:
            try:
                if r["symbol"] not in cache:
                    cache[r["symbol"]]=snapshot(r["symbol"])
                s=cache[r["symbol"]]
                old=r["stage"]; new=next_stage(r,s); ts=now_iso()
                if new!=old:
                    con.execute("""UPDATE watch_state SET stage=?,last_price=?,last_closed_5m=?,
                        last_closed_1m=?,last_update_utc=? WHERE symbol=?""",
                        (new,s["price"],s["closed5_close"],s["closed1_close"],ts,r["symbol"]))
                    con.execute("""INSERT INTO events(event_time_utc,symbol,direction,stage_from,stage_to,
                        price,closed_5m,closed_1m,payload_json) VALUES(?,?,?,?,?,?,?,?,?)""",
                        (ts,r["symbol"],r["direction"],old,new,s["price"],s["closed5_close"],
                         s["closed1_close"],json.dumps(dict(r),ensure_ascii=False)))
                    con.commit()
                    m=msg(r,new,s)
                    if m:
                        print(m); send(m)
                else:
                    con.execute("""UPDATE watch_state SET last_price=?,last_closed_5m=?,
                        last_closed_1m=?,last_update_utc=? WHERE symbol=?""",
                        (s["price"],s["closed5_close"],s["closed1_close"],ts,r["symbol"]))
                    con.commit()
            except Exception as exc:
                print("reversal live error",r["symbol"],type(exc).__name__,str(exc)[:160])

def main():
    init_db()
    items=load_plans()
    sync(items)
    print(f"Reversal live started: {len(items)} symbols, poll={POLL_SECONDS}s, run={RUN_SECONDS}s")
    end=time.time()+RUN_SECONDS
    while time.time()<end:
        loop_once()
        time.sleep(POLL_SECONDS)

if __name__=="__main__":
    main()
