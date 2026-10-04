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
from long_short_simple_notify import can_send, mark_sent, classify_move, format_alert

ANALYST_DB=os.getenv("LS_DB","long_short_analyst.db")
LIVE_DB=os.getenv("LS_LIVE_DB","long_short_live_pool.db")
POLL_SECONDS=float(os.getenv("LS_LIVE_POLL_SECONDS","30"))
RUN_SECONDS=int(os.getenv("LS_LIVE_RUN_SECONDS","3600"))
MAX_WATCH=int(os.getenv("LS_LIVE_MAX_WATCH","12"))
APPROACH_PCT=float(os.getenv("LS_LIVE_APPROACH_PCT","0.25"))
# Observational early-entry layer. It never changes the frozen continuation rules.
EARLY_APPROACH_PCT=float(os.getenv("LS_EARLY_APPROACH_PCT","0.18"))
EARLY_MAX_EXTENSION_PCT=float(os.getenv("LS_EARLY_MAX_EXTENSION_PCT","0.22"))
EARLY_MIN_VOLUME_MULT=float(os.getenv("LS_EARLY_MIN_VOLUME_MULT","1.20"))
EARLY_MIN_TAKER_SHARE=float(os.getenv("LS_EARLY_MIN_TAKER_SHARE","0.54"))
EARLY_MAX_COMPRESSION_PCT=float(os.getenv("LS_EARLY_MAX_COMPRESSION_PCT","0.90"))
EARLY_MIN_ROOM_PCT=float(os.getenv("LS_EARLY_MIN_ROOM_PCT","0.30"))
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
            htf_direction TEXT,
            htf_score INTEGER,
            htf_reasons_json TEXT,
            stage TEXT NOT NULL DEFAULT 'WATCH',
            close_confirmed_time TEXT,
            retest_seen INTEGER NOT NULL DEFAULT 0,
            last_price REAL,
            last_closed_5m REAL,
            early_state TEXT NOT NULL DEFAULT 'NONE',
            early_signal_price REAL,
            early_signal_time TEXT,
            confirmed_signal_price REAL,
            confirmed_signal_time TEXT,
            gain_before_confirmation REAL,
            time_early_to_confirmed_seconds REAL,
            early_short_signal_price REAL,
            confirmed_short_signal_price REAL,
            gain_before_short_confirmation REAL,
            time_early_short_to_confirmed_seconds REAL,
            last_update_utc TEXT NOT NULL
        )""")
        cols={r[1] for r in con.execute("PRAGMA table_info(watch_state)")}
        if "trigger_level" not in cols and "entry_level" in cols:
            con.execute("ALTER TABLE watch_state RENAME COLUMN entry_level TO trigger_level")
        if "data_mode" not in cols:
            con.execute("ALTER TABLE watch_state ADD COLUMN data_mode TEXT")
        for name,typ in [("htf_direction","TEXT"),("htf_score","INTEGER"),("htf_reasons_json","TEXT")]:
            if name not in cols:
                con.execute(f"ALTER TABLE watch_state ADD COLUMN {name} {typ}")
        for name,typ,default in [
            ("early_state","TEXT","'NONE'"),
            ("early_signal_price","REAL",None),("early_signal_time","TEXT",None),
            ("confirmed_signal_price","REAL",None),("confirmed_signal_time","TEXT",None),
            ("gain_before_confirmation","REAL",None),("time_early_to_confirmed_seconds","REAL",None),
            ("early_short_signal_price","REAL",None),("confirmed_short_signal_price","REAL",None),
            ("gain_before_short_confirmation","REAL",None),("time_early_short_to_confirmed_seconds","REAL",None),
        ]:
            if name not in cols:
                clause=f" DEFAULT {default}" if default is not None else ""
                con.execute(f"ALTER TABLE watch_state ADD COLUMN {name} {typ}{clause}")
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
                gate=p.get("htf_gate") or {}
                if not plan.get("direction") or plan.get("trigger_level") is None:
                    continue
                # Early alerts are now allowed only when 1D/4H context agrees.
                if not gate.get("qualified") or gate.get("direction")!=plan.get("direction"):
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
                    "htf_direction":str(gate.get("direction") or "NONE"),
                    "htf_score":int(gate.get("score") or 0),
                    "htf_reasons":list(gate.get("reasons") or []),
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
                    analyst_scan_time,analyst_confidence,data_mode,htf_direction,htf_score,htf_reasons_json,
                    stage,close_confirmed_time,retest_seen,last_price,last_closed_5m,last_update_utc
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,'WATCH',NULL,0,NULL,NULL,?)""",
                (x["symbol"],x["direction"],x["trigger_level"],x["retest_low"],x["retest_high"],
                 x["invalidation"],x["target1"],x["target2"],x["scan_time"],x["confidence"],x["data_mode"],
                 x["htf_direction"],x["htf_score"],json.dumps(x["htf_reasons"],ensure_ascii=False),now_iso()))
                con.execute("""UPDATE watch_state SET early_state='NONE',early_signal_price=NULL,early_signal_time=NULL,
                    confirmed_signal_price=NULL,confirmed_signal_time=NULL,gain_before_confirmation=NULL,
                    time_early_to_confirmed_seconds=NULL,early_short_signal_price=NULL,
                    confirmed_short_signal_price=NULL,gain_before_short_confirmation=NULL,
                    time_early_short_to_confirmed_seconds=NULL WHERE symbol=?""",(x["symbol"],))
            else:
                if active_stage in ("WATCH","APPROACHING"):
                    con.execute("""UPDATE watch_state SET retest_low=?,retest_high=?,invalidation=?,
                        target1=?,target2=?,analyst_scan_time=?,analyst_confidence=?,data_mode=?,
                        htf_direction=?,htf_score=?,htf_reasons_json=?,last_update_utc=? WHERE symbol=?""",
                    (x["retest_low"],x["retest_high"],x["invalidation"],x["target1"],x["target2"],
                     x["scan_time"],x["confidence"],x["data_mode"],x["htf_direction"],x["htf_score"],
                     json.dumps(x["htf_reasons"],ensure_ascii=False),now_iso(),x["symbol"]))
                else:
                    con.execute("""UPDATE watch_state SET analyst_scan_time=?,analyst_confidence=?,data_mode=?,
                        htf_direction=?,htf_score=?,htf_reasons_json=?,last_update_utc=? WHERE symbol=?""",
                    (x["scan_time"],x["confidence"],x["data_mode"],x["htf_direction"],x["htf_score"],
                     json.dumps(x["htf_reasons"],ensure_ascii=False),now_iso(),x["symbol"]))
        if keep:
            q=",".join("?" for _ in keep)
            con.execute(f"DELETE FROM watch_state WHERE symbol NOT IN ({q})",tuple(keep))
        else:
            con.execute("DELETE FROM watch_state")

def _ema(values,period=7):
    if not values:
        return 0.0
    alpha=2.0/(period+1.0)
    out=float(values[0])
    for v in values[1:]:
        out=alpha*float(v)+(1.0-alpha)*out
    return out

def _true_range(rows):
    if len(rows)<2:
        return 0.0
    vals=[]
    for prev,cur in zip(rows[:-1],rows[1:]):
        ph=float(cur[2]); pl=float(cur[3]); pc=float(prev[4])
        vals.append(max(ph-pl,abs(ph-pc),abs(pl-pc)))
    return sum(vals[-14:])/max(1,len(vals[-14:]))

def market_snapshot(symbol):
    # 1m data lets the observational layer see a breakout while it is forming.
    # The frozen continuation engine still uses the last completed 5m close below.
    kl5=spot_get("/api/v3/klines",{"symbol":symbol,"interval":"5m","limit":30})
    kl1=spot_get("/api/v3/klines",{"symbol":symbol,"interval":"1m","limit":30})
    ticker=spot_get("/api/v3/ticker/price",{"symbol":symbol})
    stats=spot_get("/api/v3/ticker/24hr",{"symbol":symbol})
    price=float(ticker["price"])
    row=kl5[-2] if len(kl5)>=2 else kl5[-1]
    closed=float(row[4])
    close_ms=int(row[6])
    close_time=datetime.fromtimestamp(close_ms/1000.0,tz=timezone.utc).isoformat()

    prev5=kl5[-7:-1] if len(kl5)>=7 else kl5[:-1]
    highs=[float(x[2]) for x in prev5]
    lows=[float(x[3]) for x in prev5]
    local_high=max(highs) if highs else price
    local_low=min(lows) if lows else price
    compression_pct=((max(highs)-min(lows))/price*100.0) if highs and lows and price else 999.0

    c1=[float(x[4]) for x in kl1]
    ema7_now=_ema(c1[-12:],7)
    ema7_prev=_ema(c1[-13:-1],7) if len(c1)>=13 else ema7_now
    ema7_slope=(ema7_now-ema7_prev)/price*100.0 if price else 0.0

    forming=kl1[-1]
    qvol=float(forming[7] or 0)
    base=[float(x[7] or 0) for x in kl1[-12:-2]]
    vol_base=(sum(base)/len(base)) if base else 0.0
    vol_mult=qvol/vol_base if vol_base>0 else 1.0
    taker_buy=float(forming[10] or 0)
    taker_share=taker_buy/qvol if qvol>0 else 0.5
    atr1=_true_range(kl1[-16:])

    short_range_pct=((max(highs)-min(lows))/price*100.0) if highs and lows and price else 0.0
    early={
        "local_high":local_high,"local_low":local_low,
        "compression_pct":compression_pct,"ema7_slope_pct":ema7_slope,
        "vol_mult":vol_mult,"taker_buy_share":taker_share,"atr1":atr1,
        "quote_volume_24h":float(stats.get("quoteVolume") or 0.0),
        "short_range_pct":short_range_pct,
    }
    return price,closed,close_time,early

def early_observation(row,price,early):
    """Observational only: detects a breakout/breakdown beginning before 5m confirmation."""
    d=row["direction"]
    trig=float(row["trigger_level"])
    t1=float(row["target1"] or 0)
    if not trig or not price:
        return "NONE",{}
    htf_direction=(row["htf_direction"] or "NONE") if "htf_direction" in row.keys() else "NONE"
    if htf_direction!=d:
        return "NONE",{"htf_gate_ok":False,"htf_direction":htf_direction}

    dist_pct=(price/trig-1.0)*100.0
    compression_ok=float(early["compression_pct"])<=EARLY_MAX_COMPRESSION_PCT
    vol_ok=float(early["vol_mult"])>=EARLY_MIN_VOLUME_MULT
    ema_slope=float(early["ema7_slope_pct"])
    taker=float(early["taker_buy_share"])
    atr1=float(early["atr1"] or 0.0)

    if d=="LONG":
        approach=(-EARLY_APPROACH_PCT)<=dist_pct
        directional=(ema_slope>0 and taker>=EARLY_MIN_TAKER_SHARE)
        started=price>=trig*(1.0-0.0005)
        extension=max(0.0,dist_pct)
        room=((t1/price-1.0)*100.0) if t1>price else 0.0
    else:
        approach=dist_pct<=EARLY_APPROACH_PCT
        directional=(ema_slope<0 and taker<=(1.0-EARLY_MIN_TAKER_SHARE))
        started=price<=trig*(1.0+0.0005)
        extension=max(0.0,-dist_pct)
        room=((price/t1-1.0)*100.0) if 0<t1<price else 0.0

    # Anti-chase: do not call a move "early" after it has already stretched or
    # when the first structural target is too close.
    atr_extension=(extension/100.0*price/atr1) if atr1>0 else 0.0
    chase=(extension>EARLY_MAX_EXTENSION_PCT or atr_extension>1.10 or room<EARLY_MIN_ROOM_PCT)
    metrics={
        "dist_trigger_pct":dist_pct,"compression_ok":compression_ok,
        "volume_ok":vol_ok,"directional_flow_ok":directional,
        "started":started,"extension_pct":extension,"room_pct":room,
        "htf_gate_ok":True,"htf_direction":htf_direction,
        "htf_score":int(row["htf_score"] or 0) if "htf_score" in row.keys() else 0,
        **early,
    }
    if chase and approach and started:
        return "CHASE",metrics
    if compression_ok and approach and started and directional and vol_ok:
        return ("EARLY_LONG" if d=="LONG" else "EARLY_SHORT"),metrics
    if approach and (directional or vol_ok):
        return "PENDING",metrics
    return "NONE",metrics

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

def early_message(row,estate,price,metrics):
    sym=row["symbol"]; d=row["direction"]
    if estate=="EARLY_LONG":
        return (f"🟢 ERKEN LONG | {sym}\n"
                f"1D/4H zemin LONG ({int(row['htf_score'] or 0)}/11).\n"
                f"Kırılım yeni başlıyor. Fiyat: {fmtp(price)}\n"
                f"Direnç: {fmtp(row['trigger_level'])} | Hacim: {metrics.get('vol_mult',1):.2f}x\n"
                f"⚠️ Gözlemsel sinyal; frozen ana giriş kuralını değiştirmez.")
    if estate=="EARLY_SHORT":
        return (f"🔻 ERKEN SHORT | {sym}\n"
                f"1D/4H zemin SHORT ({int(row['htf_score'] or 0)}/11).\n"
                f"Aşağı kırılım yeni başlıyor. Fiyat: {fmtp(price)}\n"
                f"Destek: {fmtp(row['trigger_level'])} | Hacim: {metrics.get('vol_mult',1):.2f}x\n"
                f"⚠️ Gözlemsel sinyal; frozen ana giriş kuralını değiştirmez.")
    if estate=="PENDING":
        return (f"🟡 TEYİT BEKLİYOR | {sym}\n"
                f"Erken {d} görüldü; ana 5dk teyidi henüz tamamlanmadı. Fiyat: {fmtp(price)}")
    if estate=="CHASE":
        return (f"⚪ GEÇ/KOVALAMA | {sym}\n"
                f"Hareket başladı ama erken giriş avantajı azaldı. Fiyat: {fmtp(price)}")
    if estate=="BROKEN":
        return (f"🔴 BOZULDU | {sym}\nErken {d} gözlemi geçersizleşti.")
    return None

def message_for(row,stage,price,closed):
    sym=row["symbol"]; d=row["direction"]
    trig=float(row["trigger_level"]); rl=float(row["retest_low"]); rh=float(row["retest_high"])
    inv=float(row["invalidation"] or 0); t1=float(row["target1"] or 0); t2=float(row["target2"] or 0)
    side_ball="🟢" if d=="LONG" else "🔴"
    side_word=f"{side_ball} {d}"
    coin=f"{side_ball} {sym}"

    if stage in ("APPROACHING","CLOSE_CONFIRMED","RETESTING"):
        # Passive states stay in SQLite; Telegram is reserved for actionable state changes.
        return None

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
                price,closed,closed_candle_time,early=market_snapshot(row["symbol"])
                old=row["stage"]
                new=next_stage(row,price,closed)
                observed_time=now_iso()

                # Separate observational early layer: never mutates frozen continuation stage.
                old_early=(row["early_state"] or "NONE") if "early_state" in row.keys() else "NONE"
                estate,emetrics=early_observation(row,price,early)
                inv=float(row["invalidation"] or 0)
                if old_early in ("EARLY_LONG","EARLY_SHORT","PENDING","CHASE"):
                    broken=(row["direction"]=="LONG" and inv and price<inv) or (row["direction"]=="SHORT" and inv and price>inv)
                    if broken:
                        estate="BROKEN"
                    elif old_early in ("EARLY_LONG","EARLY_SHORT") and estate=="NONE":
                        estate="PENDING"
                    elif old_early=="PENDING" and estate=="NONE":
                        estate="PENDING"
                    elif old_early=="CHASE" and estate in ("NONE","PENDING"):
                        estate="CHASE"
                if estate!=old_early:
                    emsg=None
                    if estate in ("PENDING","EARLY_LONG","EARLY_SHORT","CHASE"):
                        level=float(row["trigger_level"])
                        if can_send(row["symbol"],row["direction"],level):
                            mclass=classify_move(emetrics.get("quote_volume_24h"),emetrics.get("short_range_pct"))
                            emsg=format_alert(row["symbol"],row["direction"],level,mclass)
                            mark_sent(row["symbol"],row["direction"],level)
                    first_signal=estate in ("EARLY_LONG","EARLY_SHORT") and old_early not in ("EARLY_LONG","EARLY_SHORT")
                    con.execute("""UPDATE watch_state SET early_state=?,
                        early_signal_price=CASE WHEN ? THEN ? ELSE early_signal_price END,
                        early_signal_time=CASE WHEN ? THEN ? ELSE early_signal_time END,
                        early_short_signal_price=CASE WHEN ? AND direction='SHORT' THEN ? ELSE early_short_signal_price END,
                        last_price=?,last_closed_5m=?,last_update_utc=? WHERE symbol=?""",
                        (estate,1 if first_signal else 0,price,1 if first_signal else 0,observed_time,
                         1 if first_signal else 0,price,price,closed,observed_time,row["symbol"]))
                    con.commit()
                    if emsg:
                        print(emsg); send_telegram(emsg)
                    con.execute("""INSERT INTO events(event_time_utc,symbol,direction,stage_from,stage_to,
                        price,closed_5m,condition_time_utc,payload_json) VALUES(?,?,?,?,?,?,?,?,?)""",
                        (observed_time,row["symbol"],row["direction"],"EARLY:"+old_early,"EARLY:"+estate,
                         price,closed,observed_time,json.dumps(emetrics,ensure_ascii=False)))
                    con.commit()

                if new!=old:
                    # For a 5m close confirmation, the market condition time is the
                    # completed candle close. For intrabar states, first observation
                    # is the most honest timestamp available without websocket trades.
                    condition_time = closed_candle_time if new=="CLOSE_CONFIRMED" else observed_time
                    msg=None

                    con.execute("""UPDATE watch_state SET stage=?,last_price=?,last_closed_5m=?,last_update_utc=?,
                                   close_confirmed_time=CASE WHEN ?='CLOSE_CONFIRMED' THEN ? ELSE close_confirmed_time END,
                                   retest_seen=CASE WHEN ?='RETESTING' THEN 1 ELSE retest_seen END
                                   WHERE symbol=?""",
                        (new,price,closed,observed_time,new,condition_time,new,row["symbol"]))
                    if new=="TRIGGERED":
                        fresh=con.execute("SELECT early_signal_price,early_signal_time,direction FROM watch_state WHERE symbol=?",(row["symbol"],)).fetchone()
                        if fresh and fresh[0] is not None:
                            ep=float(fresh[0]); et=fresh[1]
                            gain=((price/ep-1.0)*100.0) if fresh[2]=="LONG" else ((ep/price-1.0)*100.0)
                            try:
                                dt=(datetime.fromisoformat(observed_time)-datetime.fromisoformat(et)).total_seconds() if et else None
                            except Exception:
                                dt=None
                            con.execute("""UPDATE watch_state SET confirmed_signal_price=?,confirmed_signal_time=?,
                                gain_before_confirmation=?,time_early_to_confirmed_seconds=?,
                                confirmed_short_signal_price=CASE WHEN direction='SHORT' THEN ? ELSE confirmed_short_signal_price END,
                                gain_before_short_confirmation=CASE WHEN direction='SHORT' THEN ? ELSE gain_before_short_confirmation END,
                                time_early_short_to_confirmed_seconds=CASE WHEN direction='SHORT' THEN ? ELSE time_early_short_to_confirmed_seconds END
                                WHERE symbol=?""",(price,observed_time,gain,dt,price,gain,dt,row["symbol"]))
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
