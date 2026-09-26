#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Seed Gate historical-pair registry from Gate's official delisting archive.

This supplements current-API and observed-history survivorship recovery with
official delisting announcements. It only expands the research universe; it
never changes frozen signal thresholds.
"""
from __future__ import annotations
import html, json, os, re, sqlite3, sys
from datetime import datetime, timezone
from urllib.parse import urljoin
import requests

DB=os.getenv("HISTORY_DB","history_miner.db")
PAGES=int(os.getenv("GATE_DELIST_PAGES","25"))
BASE="https://www.gate.com"
LIST="https://www.gate.com/announcements/delisted"
UA={"User-Agent":"avci-history-research/1.0"}

def now():return datetime.now(timezone.utc).isoformat()
def fetch(url):
    r=requests.get(url,headers=UA,timeout=30)
    r.raise_for_status();return r.text

def init(c):
    c.execute("""CREATE TABLE IF NOT EXISTS official_delist_registry(
      pair TEXT PRIMARY KEY,symbol TEXT NOT NULL,announcement_url TEXT,
      first_seen_utc TEXT NOT NULL,last_seen_utc TEXT NOT NULL,
      source TEXT NOT NULL DEFAULT 'gate_official_delisting_archive')""")
    c.execute("""CREATE TABLE IF NOT EXISTS historical_pair_registry(
      pair TEXT PRIMARY KEY,symbol TEXT NOT NULL,quote TEXT NOT NULL,
      first_source TEXT NOT NULL,current_trade_status TEXT,currency_delisted INTEGER,
      trade_disabled INTEGER,first_seen_utc TEXT NOT NULL,last_seen_utc TEXT NOT NULL,
      coverage_status TEXT NOT NULL DEFAULT 'QUEUED',attempts INTEGER NOT NULL DEFAULT 0,
      recovered_bars INTEGER NOT NULL DEFAULT 0,first_bar_ts INTEGER,last_bar_ts INTEGER,
      last_error TEXT,version TEXT NOT NULL)""")
    c.commit()

def article_links(text):
    links=set()
    for m in re.finditer(r'href=["\']([^"\']*/announcements/article/[^"\']+)["\']',text,re.I):
        links.add(urljoin(BASE,html.unescape(m.group(1))))
    return links

def pairs_from_article(text):
    # Gate delisting announcements consistently expose spot pairs as SYMBOL_USDT.
    pairs=set(re.findall(r'\b([A-Z0-9][A-Z0-9._-]{0,40})_USDT\b',text.upper()))
    return {(f"{s}_USDT",s) for s in pairs if s not in {"USDT","USD"}}

def main():
    os.makedirs(os.path.dirname(DB) or ".",exist_ok=True)
    links=set();errors=[]
    for p in range(1,PAGES+1):
        url=LIST if p==1 else f"{LIST}?page={p}"
        try: links.update(article_links(fetch(url)))
        except Exception as e: errors.append(f"list:{p}:{type(e).__name__}:{str(e)[:100]}")
    found={}
    for url in sorted(links):
        try:
            text=fetch(url)
            for pair,sym in pairs_from_article(text):
                found[pair]=(sym,url)
        except Exception as e:errors.append(f"article:{url}:{type(e).__name__}:{str(e)[:100]}")
    stamp=now()
    with sqlite3.connect(DB,timeout=60) as c:
        init(c)
        for pair,(sym,url) in found.items():
            c.execute("""INSERT INTO official_delist_registry(pair,symbol,announcement_url,first_seen_utc,last_seen_utc)
              VALUES(?,?,?,?,?) ON CONFLICT(pair) DO UPDATE SET
              announcement_url=excluded.announcement_url,last_seen_utc=excluded.last_seen_utc""",
              (pair,sym,url,stamp,stamp))
            c.execute("""INSERT INTO historical_pair_registry(
              pair,symbol,quote,first_source,current_trade_status,currency_delisted,trade_disabled,
              first_seen_utc,last_seen_utc,coverage_status,version)
              VALUES(?,?,?,'gate_official_delisting_archive','DELISTED_OFFICIAL',1,1,?,?,'QUEUED','official-delist-v1-20260926')
              ON CONFLICT(pair) DO UPDATE SET
                current_trade_status='DELISTED_OFFICIAL',currency_delisted=1,trade_disabled=1,
                last_seen_utc=excluded.last_seen_utc""",(pair,sym,"USDT",stamp,stamp))
        c.commit()
    report={"generated_at_utc":stamp,"list_pages":PAGES,"article_links":len(links),
            "official_usdt_pairs":len(found),"errors":errors[:50]}
    open("gate_delist_import_report.json","w",encoding="utf-8").write(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=="__main__":main()
