"""Readable Telegram enrichment for Avci engines.
Observational only: fetches current prices/logos and records notification comparisons.
"""
import sqlite3, time
import requests

def fmt_price(v):
    if v is None: return "?"
    return f"{float(v):.10f}".rstrip("0").rstrip(".")

def pct(now, base):
    try:
        return 100.0*(float(now)/float(base)-1.0) if float(base)>0 else None
    except (TypeError,ValueError,ZeroDivisionError):
        return None

def binance_price(symbol, session=requests):
    try:
        r=session.get("https://api.binance.com/api/v3/ticker/price",params={"symbol":symbol},timeout=10)
        r.raise_for_status(); return float(r.json()["price"])
    except Exception:
        return None

def coingecko_logo(symbol, verified_name=None, session=requests):
    """Only use a logo when identity is reasonably constrained."""
    try:
        q=verified_name or symbol
        r=session.get("https://api.coingecko.com/api/v3/search",params={"query":q},timeout=10)
        r.raise_for_status()
        coins=r.json().get("coins") or []
        exact=[c for c in coins if str(c.get("symbol","")).upper()==symbol.upper()]
        if verified_name:
            named=[c for c in exact if str(c.get("name","")).lower()==verified_name.lower()]
            exact=named or exact
        if len(exact)!=1:
            return None
        return exact[0].get("large") or exact[0].get("thumb")
    except Exception:
        return None

def gecko_token(network, contract, session=requests):
    """Contract-address lookup for Gate/on-chain candidates."""
    try:
        url=f"https://api.geckoterminal.com/api/v2/networks/{network}/tokens/{contract}"
        r=session.get(url,headers={"accept":"application/json"},timeout=12)
        r.raise_for_status(); a=((r.json().get("data") or {}).get("attributes") or {})
        price=a.get("price_usd")
        return {
            "price": float(price) if price not in (None,"") else None,
            "logo": a.get("image_url"),
            "name": a.get("name"),
            "symbol": a.get("symbol"),
        }
    except Exception:
        return {"price":None,"logo":None,"name":None,"symbol":None}

def send_photo_or_text(token, chat_id, text, logo=None, session=requests):
    if logo:
        try:
            r=session.post(f"https://api.telegram.org/bot{token}/sendPhoto",
                json={"chat_id":chat_id,"photo":logo,"caption":text[:1024]},timeout=20)
            r.raise_for_status()
            if r.json().get("ok"): return True
        except Exception:
            pass
    r=session.post(f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id":chat_id,"text":text[:4096],"disable_web_page_preview":True},timeout=20)
    r.raise_for_status()
    return bool(r.json().get("ok"))

def ensure_history(con, table):
    con.execute(f"""CREATE TABLE IF NOT EXISTS {table}(
        key TEXT PRIMARY KEY, symbol TEXT, signal_price REAL, first_sent_price REAL,
        last_price REAL, last_sent_ts INTEGER, last_change_pct REAL, logo_url TEXT)""")

def record_initial(con, table, key, symbol, signal_price, current_price, logo):
    ensure_history(con,table)
    change=pct(current_price,signal_price)
    con.execute(f"""INSERT OR REPLACE INTO {table}
        (key,symbol,signal_price,first_sent_price,last_price,last_sent_ts,last_change_pct,logo_url)
        VALUES(?,?,?,?,?,?,?,?)""",
        (str(key),symbol,signal_price,current_price,current_price,int(time.time()),change,logo))

def due_followups(con, table, current_getter, min_pp=3.0, max_age=72*3600):
    ensure_history(con,table)
    now=int(time.time()); out=[]
    for row in con.execute(f"""SELECT key,symbol,signal_price,first_sent_price,last_price,
        last_sent_ts,last_change_pct,logo_url FROM {table}
        WHERE last_sent_ts>=?""",(now-max_age,)):
        key,symbol,signal_price,first_sent,last_price,last_ts,last_change,logo=row
        cur=current_getter(key,symbol)
        if cur is None: continue
        change=pct(cur,signal_price)
        if change is None: continue
        if last_change is None or abs(change-float(last_change))>=min_pp:
            out.append({"key":key,"symbol":symbol,"signal_price":signal_price,
                        "first_sent":first_sent,"last_price":last_price,"current":cur,
                        "change":change,"since_last":pct(cur,last_price),"logo":logo})
    return out

def mark_followup(con, table, key, current, change):
    con.execute(f"UPDATE {table} SET last_price=?,last_sent_ts=?,last_change_pct=? WHERE key=?",
                (current,int(time.time()),change,str(key)))
