#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared anti-spam + compact Telegram formatting for Long/Short observers."""
from __future__ import annotations
import json, os, sqlite3, time

DB=os.getenv("LS_SIMPLE_NOTIFY_DB","long_short_simple_notify.db")
COOLDOWN_SECONDS=int(os.getenv("LS_SIMPLE_NOTIFY_COOLDOWN","3600"))
GLOBAL_GAP_SECONDS=int(os.getenv("LS_SIMPLE_NOTIFY_GLOBAL_GAP","1200"))
CORE_STABLE_BASES={x.strip().upper() for x in os.getenv("LS_CORE_STABLE_BASES","BTC,ETH,BNB,SOL,XRP,LINK,ADA,AVAX,LTC,BCH").split(",") if x.strip()}
FAST_FRESH_MIN_PCT=float(os.getenv("LS_FAST_FRESH_MIN_PCT","5.0"))
FAST_EXTENDED_MIN_PCT=float(os.getenv("LS_FAST_EXTENDED_MIN_PCT","10.0"))

def init_db():
    with sqlite3.connect(DB) as con:
        con.execute("""CREATE TABLE IF NOT EXISTS sent_alerts(
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            level REAL NOT NULL,
            fingerprint TEXT NOT NULL,
            sent_at_epoch REAL NOT NULL,
            PRIMARY KEY(symbol,fingerprint)
        )""")
        con.execute("CREATE INDEX IF NOT EXISTS ix_simple_alert_symbol_time ON sent_alerts(symbol,sent_at_epoch)")
        sent_cols={r[1] for r in con.execute("PRAGMA table_info(sent_alerts)")}
        if "payload_json" not in sent_cols:
            con.execute("ALTER TABLE sent_alerts ADD COLUMN payload_json TEXT")
        con.execute("""CREATE TABLE IF NOT EXISTS pending_alerts(
            symbol TEXT PRIMARY KEY,
            direction TEXT NOT NULL,
            level REAL NOT NULL,
            fingerprint TEXT NOT NULL,
            message TEXT NOT NULL,
            priority INTEGER NOT NULL DEFAULT 0,
            queued_at_epoch REAL NOT NULL,
            updated_at_epoch REAL NOT NULL
        )""")
        pending_cols={r[1] for r in con.execute("PRAGMA table_info(pending_alerts)")}
        if "payload_json" not in pending_cols:
            con.execute("ALTER TABLE pending_alerts ADD COLUMN payload_json TEXT")
        con.execute("""CREATE TABLE IF NOT EXISTS scheduler_state(
            key TEXT PRIMARY KEY,
            value REAL NOT NULL
        )""")

def fingerprint(symbol,direction,level):
    # 4 significant decimals is enough to prevent repeat spam while allowing a genuinely new setup later.
    return f"{symbol}|{direction}|{float(level):.8g}"

def can_send(symbol,direction,level):
    init_db()
    fp=fingerprint(symbol,direction,level)
    now=time.time()
    with sqlite3.connect(DB) as con:
        if con.execute("SELECT 1 FROM sent_alerts WHERE symbol=? AND fingerprint=?",(symbol,fp)).fetchone():
            return False
        row=con.execute("SELECT MAX(sent_at_epoch) FROM sent_alerts WHERE symbol=?",(symbol,)).fetchone()
        if row and row[0] is not None and now-float(row[0])<COOLDOWN_SECONDS:
            return False
    return True

def mark_sent(symbol,direction,level,payload=None):
    init_db()
    fp=fingerprint(symbol,direction,level)
    payload_json=json.dumps(payload,ensure_ascii=False,separators=(",",":")) if payload is not None else None
    with sqlite3.connect(DB) as con:
        con.execute("""INSERT OR REPLACE INTO sent_alerts(
            symbol,direction,level,fingerprint,sent_at_epoch,payload_json
        ) VALUES(?,?,?,?,?,?)""",
                    (symbol,direction,float(level),fp,time.time(),payload_json))

def queue_alert(symbol,direction,level,message,priority=0,payload=None):
    """Queue at most one current alert per coin; shared across observers."""
    init_db()
    if not can_send(symbol,direction,level):
        return False
    fp=fingerprint(symbol,direction,level)
    now=time.time()
    payload_json=json.dumps(payload,ensure_ascii=False,separators=(",",":")) if payload is not None else None
    with sqlite3.connect(DB) as con:
        con.execute("""INSERT INTO pending_alerts(
            symbol,direction,level,fingerprint,message,priority,queued_at_epoch,updated_at_epoch,payload_json
        ) VALUES(?,?,?,?,?,?,?,?,?)
        ON CONFLICT(symbol) DO UPDATE SET
            direction=excluded.direction,level=excluded.level,fingerprint=excluded.fingerprint,
            message=excluded.message,priority=MAX(pending_alerts.priority,excluded.priority),
            updated_at_epoch=excluded.updated_at_epoch,payload_json=excluded.payload_json""",
            (symbol,direction,float(level),fp,message,int(priority),now,now,payload_json))
    return True

def claim_ready_alert():
    """Atomically claim at most one Telegram message every GLOBAL_GAP_SECONDS.

    The alert is NOT marked sent here. Call ack_claimed_alert() only after
    Telegram accepts the message. If delivery fails, call retry_claimed_alert().
    """
    init_db()
    now=time.time()
    con=sqlite3.connect(DB)
    try:
        con.execute("BEGIN IMMEDIATE")
        row=con.execute("SELECT value FROM scheduler_state WHERE key='next_allowed_epoch'").fetchone()
        if row and now<float(row[0]):
            con.commit()
            return None
        item=con.execute("""SELECT symbol,direction,level,fingerprint,message,priority,payload_json
                            FROM pending_alerts
                            ORDER BY priority DESC,queued_at_epoch ASC LIMIT 1""").fetchone()
        if not item:
            con.commit()
            return None
        symbol,direction,level,fp,message,priority,payload_json=item
        # Reserve the global slot and remove this item from the queue so the two
        # parallel watchers cannot claim the same alert.
        con.execute("""INSERT INTO scheduler_state(key,value) VALUES('next_allowed_epoch',?)
                       ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                    (now+GLOBAL_GAP_SECONDS,))
        con.execute("DELETE FROM pending_alerts WHERE symbol=?",(symbol,))
        con.commit()
        return {
            "symbol":symbol,"direction":direction,"level":float(level),
            "fingerprint":fp,"message":message,"priority":int(priority),
            "payload_json":payload_json,
            "payload":json.loads(payload_json) if payload_json else None,
        }
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def ack_claimed_alert(item):
    """Mark a claimed alert sent only after Telegram delivery succeeds."""
    mark_sent(item["symbol"],item["direction"],item["level"],item.get("payload"))


def retry_claimed_alert(item,retry_after_seconds=60):
    """Put a failed Telegram delivery back into the queue for a near-term retry."""
    init_db()
    now=time.time()
    with sqlite3.connect(DB) as con:
        con.execute("""INSERT INTO pending_alerts(
            symbol,direction,level,fingerprint,message,priority,queued_at_epoch,updated_at_epoch,payload_json
        ) VALUES(?,?,?,?,?,?,?,?,?)
        ON CONFLICT(symbol) DO UPDATE SET
            direction=excluded.direction,level=excluded.level,fingerprint=excluded.fingerprint,
            message=excluded.message,priority=MAX(pending_alerts.priority,excluded.priority),
            updated_at_epoch=excluded.updated_at_epoch,payload_json=excluded.payload_json""",
            (item["symbol"],item["direction"],float(item["level"]),item["fingerprint"],
             item["message"],int(item.get("priority",0)),now,now,item.get("payload_json")))
        con.execute("""INSERT INTO scheduler_state(key,value) VALUES('next_allowed_epoch',?)
                       ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                    (now+max(15,int(retry_after_seconds)),))

def classify_move(symbol, day_change_pct):
    """Fixed core/stable bucket + daily-move buckets for faster coins."""
    base=(symbol[:-4] if str(symbol).upper().endswith("USDT") else str(symbol)).upper()
    if base in CORE_STABLE_BASES:
        return "STABLE"
    move=abs(float(day_change_pct or 0.0))
    if move>=FAST_EXTENDED_MIN_PCT:
        return "FAST_EXTENDED"
    if move>=FAST_FRESH_MIN_PCT:
        return "FAST_FRESH"
    return "FAST_QUIET"

def format_alert(symbol,direction,level,move_class,day_change_pct=0.0,current_price=None,
                 stop_level=None,target1=None,target2=None):
    """Compact Telegram watch alert with trigger, invalidation and targets."""
    level_text=f"{float(level):,.8g}"
    price_text="-" if current_price is None else f"{float(current_price):,.8g}"
    stop_text=None if stop_level in (None,0,"") else f"{float(stop_level):,.8g}"
    t1_text=None if target1 in (None,0,"") else f"{float(target1):,.8g}"
    t2_text=None if target2 in (None,0,"") else f"{float(target2):,.8g}"
    if move_class=="STABLE":
        tip="🟦 Güçlü/Stabil"
    elif move_class=="FAST_FRESH":
        tip="⚡ Hızlı/Taze"
    elif move_class=="FAST_EXTENDED":
        tip="🔥 Hızlı/Uzamış"
    else:
        tip="⚪ Hızlı/Sakin"

    risk_lines=""
    if stop_text:
        risk_lines += f"❌ Fikir bozulur / Stop: {stop_text}\n"
    if t1_text:
        risk_lines += f"🎯 Hedef 1: {t1_text}\n"
    if t2_text:
        risk_lines += f"🎯 Hedef 2: {t2_text}\n"

    if direction=="LONG":
        return (f"🟢 LONG İÇİN İZLE | {symbol}\n"
                f"5 dk mum {level_text} üstünde kapanırsa LONG güçlenir.\n"
                f"Şu an fiyat: {price_text}\n"
                f"Beklenen: {level_text} üstü kapanış → ardından seviyeyi koruması.\n"
                f"{risk_lines}"
                f"Tip: {tip}")

    return (f"🔴 SHORT İÇİN İZLE | {symbol}\n"
            f"5 dk mum {level_text} altında kapanırsa SHORT güçlenir.\n"
            f"Şu an fiyat: {price_text}\n"
            f"Beklenen: {level_text} altı kapanış → ardından seviyenin altında kalması.\n"
            f"{risk_lines}"
            f"Tip: {tip}")
