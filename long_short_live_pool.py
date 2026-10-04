#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Long/Short Live Pool

Separate from Avci/Gate/Long-Short analyst scoring.
- Reads latest Long/Short analyst candidates from long_short_analyst.db.
- Keeps its own state in long_short_live_pool.db.
- Polls watched symbols every ~15 seconds during a short-lived worker window.
- Sends Telegram only on meaningful stage transitions.
- Never places orders.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import datetime, timezone

import requests
from binance_notify import resolve_chat_id

ANALYST_DB=os.getenv("LS_DB","long_short_analyst.db")
LIVE_DB=os.getenv("LS_LIVE_DB","long_short_live_pool.db")
POLL_SECONDS=float(os.getenv("LS_LIVE_POLL_SECONDS","15"))
RUN_SECONDS=int(os.getenv("LS_LIVE_RUN_SECONDS","250"))
MAX_WATCH=int(os.getenv("LS_LIVE_MAX_WATCH","12"))
APPROACH_PCT=float(os.getenv("LS_LIVE_APPROACH_PCT","0.25"))
TELEGRAM_LIMIT=4096

SPOT_BASES=("https://data-api.binance.vision","https://api.binance.com")

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def spot_get(path,params=None):
    last=None
    for base in SPOT_BASES:
        try:
            r=requests.get(base+path,params=params or {},timeout=10,
                           headers={"User-Agent":"long-short-live-pool/1.0"})
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            last=exc
    raise last or RuntimeError("Binance Spot unavailable")

def fmtp(x):
    if x is None: return "-"
    x=float(x)
    if abs(x)>=1000: return f"{x:,.2f}"
    if abs(x)>=1: return f"{x:.4f}"
    return f"{x:.8f}".rstrip("0")

def init_db():
    with sqlite3.connect(LIVE_DB) as con:
        con.execute("""CREATE TABLE IF NOT EXISTS watch_state(
            symbol TEXT PRIMARY KEY,
            direction TEXT NOT NULL,
            trigger_level REAL NOT NULL,
            retest_low REAL,
            retest_high REAL,
            invalidation REAL,
            target1 REAL,
            target2 REAL,
            analyst_scan_time TEXT,
            analyst_confidence INTEGER,
            data_mode TEXT,
            stage TEXT NOT NULL DEFAULT 'WATCH',
            close_confirmed_time TEXT,
            retest_seen INTEGER NOT NULL DEFAULT 0,
            last_price REAL,
            last_closed_5m REAL,
            last_update_utc TEXT NOT NULL
        )""")
        cols={r[1] for r in con.execute("PRAGMA table_info(watch_state)")}
        if "trigger_level" not in cols and "entry_level" in cols:
            con.execute("ALTER TABLE watch_state RENAME COLUMN entry_level TO trigger_level")
        if "data_mode" not in cols:
            con.execute("ALTER TABLE watch_state ADD COLUMN data_mode TEXT")
        con.execute("""CREATE TABLE IF NOT EXISTS events(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_time_utc TEXT NOT NULL,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            stage_from TEXT,
            stage_to TEXT NOT NULL,
            price REAL,
            closed_5m REAL,
            condition_time_utc TEXT,
            telegram_sent_time_utc TEXT,
            delay_seconds REAL,
            payload_json TEXT
        )""")
        event_cols={r[1] for r in con.execute("PRAGMA table_info(events)")}
        for name,typ in {
            "condition_time_utc":"TEXT",
            "telegram_sent_time_utc":"TEXT",
            "delay_seconds":"REAL",
        }.items():
            if name not in event_cols:
                con.execute(f"ALTER TABLE events ADD COLUMN {name} {typ}")

def load_watchlist():
    if not os.path.exists(ANALYST_DB):
        return []
    with sqlite3.connect(ANALYST_DB) as con:
        con.row_factory=sqlite3.Row
        scan=con.execute("SELECT MAX(scan_time_utc) AS ts FROM analyses").fetchone()
        if not scan or not scan["ts"]:
            return []
        rows=con.execute("""SELECT symbol,status,long_score,short_score,confidence,payload_json
                           FROM analyses WHERE scan_time_utc=?
                           ORDER BY confidence DESC LIMIT ?""",(scan["ts"],MAX_WATCH*3)).fetchall()
        out=[]
        for r in rows:
            try:
                p=json.loads(r["payload_json"] or "{}")
                plan=p.get("setup_plan") or {}
                if not plan.get("direction") or plan.get("trigger_level") is None:
                    continue
                # Watch only meaningful WAIT/LONG/SHORT candidates.
                if r["status"] not in ("WAIT","LONG","SHORT"):
                    continue
                out.append({
                    "symbol":r["symbol"],"direction":plan["direction"],
                    "trigger_level":float(plan["trigger_level"]),
                    "retest_low":float(plan.get("retest_low") or plan["trigger_level"]),
                    "retest_high":float(plan.get("retest_high") or plan["trigger_level"]),
                    "invalidation":float(plan.get("invalidation") or 0),
                    "target1":float(plan.get("target1") or 0),
                    "target2":float(plan.get("target2") or 0),
                    "confidence":int(r["confidence"] or 0),
                    "data_mode":str(p.get("data_mode") or "UNKNOWN"),
                    "scan_time":scan["ts"],
                })
                if len(out)>=MAX_WATCH:
                    break
            except Exception:
                continue
        return out

def sync_watchlist(items):
    with sqlite3.connect(LIVE_DB) as con:
        keep={x["symbol"] for x in items}
        for x in items:
            old=con.execute("SELECT direction,trigger_level,stage FROM watch_state WHERE symbol=?",(x["symbol"],)).fetchone()
            active_stage=old[2] if old else None
            level_changed=(old and abs(float(old[1])-x["trigger_level"])>max(1e-12,x["trigger_level"]*0.001))
            reset = not old or old[0]!=x["direction"] or (active_stage in ("WATCH","APPROACHING") and level_changed)
            if reset:
                con.execute("""INSERT OR REPLACE INTO watch_state(
                    symbol,direction,trigger_level,retest_low,retest_high,invalidation,target1,target2,
                    analyst_scan_time,analyst_confidence,data_mode,stage,close_confirmed_time,retest_seen,
                    last_price,last_closed_5m,last_update_utc
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,'WATCH',NULL,0,NULL,NULL,?)""",
                (x["symbol"],x["direction"],x["trigger_level"],x["retest_low"],x["retest_high"],
                 x["invalidation"],x["target1"],x["target2"],x["scan_time"],x["confidence"],x["data_mode"],now_iso()))
            else:
                if active_stage in ("WATCH","APPROACHING"):
                    con.execute("""UPDATE watch_state SET retest_low=?,retest_high=?,invalidation=?,
                        target1=?,target2=?,analyst_scan_time=?,analyst_confidence=?,data_mode=?,last_update_utc=?
                        WHERE symbol=?""",
                    (x["retest_low"],x["retest_high"],x["invalidation"],x["target1"],x["target2"],
                     x["scan_time"],x["confidence"],x["data_mode"],now_iso(),x["symbol"]))
                else:
                    con.execute("""UPDATE watch_state SET analyst_scan_time=?,analyst_confidence=?,
                        data_mode=?,last_update_utc=? WHERE symbol=?""",
                    (x["scan_time"],x["confidence"],x["data_mode"],now_iso(),x["symbol"]))
        if keep:
            q=",".join("?" for _ in keep)
            con.execute(f"DELETE FROM watch_state WHERE symbol NOT IN ({q})",tuple(keep))
        else:
            con.execute("DELETE FROM watch_state")

def market_snapshot(symbol):
    kl=spot_get("/api/v3/klines",{"symbol":symbol,"interval":"5m","limit":3})
    ticker=spot_get("/api/v3/ticker/price",{"symbol":symbol})
    price=float(ticker["price"])
    # Binance last row is normally forming; previous row is the last completed 5m candle.
    row=kl[-2] if len(kl)>=2 else kl[-1]
    closed=float(row[4])
    close_ms=int(row[6])
    close_time=datetime.fromtimestamp(close_ms/1000.0,tz=timezone.utc).isoformat()
    return price,closed,close_time

def next_stage(row,price,closed):
    direction=row["direction"]
    trig=float(row["trigger_level"])
    rl=float(row["retest_low"])
    rh=float(row["retest_high"])
    inv=float(row["invalidation"] or 0)
    stage=row["stage"]
    dist=abs(price/trig-1.0)*100.0 if trig else 999

    if direction=="LONG":
        if inv and closed < inv:
            return "INVALIDATED"
        close_ok=closed>trig
        in_retest=(rl<=price<=rh)
        moving_away=price>rh
    else:
        if inv and closed > inv:
            return "INVALIDATED"
        close_ok=closed<trig
        in_retest=(rl<=price<=rh)
        moving_away=price<rl

    if stage in ("WATCH","APPROACHING"):
        if close_ok:
            return "CLOSE_CONFIRMED"
        if dist<=APPROACH_PCT:
            return "APPROACHING"
        return "WATCH"
    if stage=="CLOSE_CONFIRMED":
        if in_retest:
            return "RETESTING"
        return "CLOSE_CONFIRMED"
    if stage=="RETESTING":
        if moving_away:
            return "TRIGGERED"
        return "RETESTING"
    return stage

def message_for(row,stage,price,closed):
    sym=row["symbol"]; d=row["direction"]
    trig=float(row["trigger_level"]); rl=float(row["retest_low"]); rh=float(row["retest_high"])
    inv=float(row["invalidation"] or 0); t1=float(row["target1"] or 0); t2=float(row["target2"] or 0)
    side_ball="🟢" if d=="LONG" else "🔴"
    side_word=f"{side_ball} {d}"
    coin=f"{side_ball} {sym}"

    if stage=="APPROACHING":
        return (f"➡️ DEVAM MOTORU\n"
                f"{coin} — {side_word} İZLENİYOR\n"
                f"🟡 ŞİMDİ GİRME\n"
                f"Kırılmasını beklediğimiz {'direnç' if d=='LONG' else 'destek'}: {fmtp(trig)}\n"
                f"Bu doğrudan giriş fiyatı değildir.\n"
                f"5dk mumun {'üstünde' if d=='LONG' else 'altında'} kapanmasını bekliyoruz.")

    if stage=="CLOSE_CONFIRMED":
        return (f"➡️ DEVAM MOTORU\n"
                f"{coin} — {side_word} İÇİN İLK ŞART GELDİ\n"
                f"🟠 HAZIRLAN, AMA HENÜZ GİRME\n"
                f"5dk kapanış şartı tamamlandı.\n"
                f"Şimdi fiyatın {fmtp(rl)}–{fmtp(rh)} bölgesine geri dönmesini bekliyoruz.")

    if stage=="RETESTING":
        return (f"➡️ DEVAM MOTORU\n"
                f"{coin} — {side_word} İÇİN SON KONTROL\n"
                f"🟠 HAZIRLAN\n"
                f"Fiyat kontrol bölgesinde: {fmtp(rl)}–{fmtp(rh)}\n"
                f"Henüz giriş yok. Son teyit bekleniyor.")

    if stage=="TRIGGERED":
        data_mode=(row["data_mode"] or "UNKNOWN") if "data_mode" in row.keys() else "UNKNOWN"
        warn="" if data_mode=="BINANCE_FUTURES" else "\n⚠️ Binance Futures akış teyidi yok; grafik şartına dayanıyor."
        return (f"➡️ DEVAM MOTORU\n"
                f"{coin} — {side_word} ŞARTLARI TAMAM\n"
                f"{side_ball} {d} DEĞERLENDİRİLEBİLİR{warn}\n"
                f"Fiyat: {fmtp(price)}\n"
                f"❌ Fikir bozulur: {fmtp(inv)}\n"
                f"🎯 Hedef 1: {fmtp(t1)}\n"
                f"🎯 Hedef 2: {fmtp(t2)}")

    if stage=="INVALIDATED":
        return (f"➡️ DEVAM MOTORU\n"
                f"⚪ {sym} — ESKİ {side_word} FİKRİ İPTAL\n"
                f"Bu setup artık kullanılmamalı.")

    return None

def send_telegram(msg):
    token=(os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    configured=(os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    if not token:
        print(msg)
        return now_iso()
    chat=resolve_chat_id(token,configured,"binance_avci2.db","Long/Short Live Pool")
    r=requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id":chat,"text":msg[:TELEGRAM_LIMIT],"disable_web_page_preview":True},
                    timeout=10)
    r.raise_for_status()
    return now_iso()

def loop_once():
    with sqlite3.connect(LIVE_DB) as con:
        con.row_factory=sqlite3.Row
        rows=con.execute("SELECT * FROM watch_state ORDER BY analyst_confidence DESC").fetchall()
        for row in rows:
            try:
                price,closed,closed_candle_time=market_snapshot(row["symbol"])
                old=row["stage"]
                new=next_stage(row,price,closed)
                observed_time=now_iso()
                if new!=old:
                    # For a 5m close confirmation, the market condition time is the
                    # completed candle close. For intrabar states, first observation
                    # is the most honest timestamp available without websocket trades.
                    condition_time = closed_candle_time if new=="CLOSE_CONFIRMED" else observed_time
                    msg=message_for(row,new,price,closed)

                    con.execute("""UPDATE watch_state SET stage=?,last_price=?,last_closed_5m=?,last_update_utc=?,
                                   close_confirmed_time=CASE WHEN ?='CLOSE_CONFIRMED' THEN ? ELSE close_confirmed_time END,
                                   retest_seen=CASE WHEN ?='RETESTING' THEN 1 ELSE retest_seen END
                                   WHERE symbol=?""",
                        (new,price,closed,observed_time,new,condition_time,new,row["symbol"]))
                    con.commit()

                    sent_time=None
                    delay=None
                    if msg:
                        print(msg)
                        sent_time=send_telegram(msg)
                        try:
                            delay=(datetime.fromisoformat(sent_time)-datetime.fromisoformat(condition_time)).total_seconds()
                        except Exception:
                            delay=None
                        if delay is not None:
                            print(f"ALERT_DELAY {row['symbol']} {new}: {delay:.1f}s")

                    con.execute("""INSERT INTO events(
                        event_time_utc,symbol,direction,stage_from,stage_to,price,closed_5m,
                        condition_time_utc,telegram_sent_time_utc,delay_seconds,payload_json
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                        (observed_time,row["symbol"],row["direction"],old,new,price,closed,
                         condition_time,sent_time,delay,json.dumps(dict(row),ensure_ascii=False)))
                    con.commit()
                else:
                    con.execute("UPDATE watch_state SET last_price=?,last_closed_5m=?,last_update_utc=? WHERE symbol=?",
                                (price,closed,observed_time,row["symbol"]))
                    con.commit()
            except Exception as exc:
                print("live error",row["symbol"],type(exc).__name__,str(exc)[:120])


def main():
    init_db()
    items=load_watchlist()
    sync_watchlist(items)
    print(f"Live pool started: {len(items)} symbols, poll={POLL_SECONDS}s, run={RUN_SECONDS}s")
    end=time.time()+RUN_SECONDS
    while time.time()<end:
        loop_once()
        time.sleep(POLL_SECONDS)

if __name__=="__main__":
    main()
