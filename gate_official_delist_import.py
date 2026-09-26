#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Seed Gate historical-pair registry from Gate's official announcement API.

Uses Gate's public POST /ann/list_article endpoint (no API key required) and
miniapp article pages as an optional content fallback. This only expands the
research universe; it never changes frozen signal thresholds.
"""
from __future__ import annotations
import html, json, os, re, sqlite3
from datetime import datetime, timezone
import requests

DB=os.getenv("HISTORY_DB","history_miner.db")
PAGES=int(os.getenv("GATE_DELIST_PAGES","25"))
ANN_API="https://api.gateio.ws/api/v4/ann/list_article"
MINIAPP="https://miniapp.gate.com/announcements/article/"
UA={"User-Agent":"avci-history-research/1.0"}

def now(): return datetime.now(timezone.utc).isoformat()

def fetch(url):
    r=requests.get(url,headers=UA,timeout=10)
    r.raise_for_status()
    return r.text

def ann_page(page):
    payload={
      "page":str(page),"size":"100","title_query":"Delist","lang":"en",
      "sub_website_id":"0","filter_empty_content":0
    }
    r=requests.post(
      ANN_API,json=payload,
      headers={**UA,"Accept":"application/json","Content-Type":"application/json"},
      timeout=30
    )
    r.raise_for_status()
    data=r.json()
    block=(data.get("data") or {}) if isinstance(data,dict) else {}
    return block.get("list") or [], int(block.get("total") or 0)

def init(c):
    c.execute("""CREATE TABLE IF NOT EXISTS official_delist_registry(
      pair TEXT PRIMARY KEY,symbol TEXT NOT NULL,announcement_url TEXT,
      first_seen_utc TEXT NOT NULL,last_seen_utc TEXT NOT NULL,
      source TEXT NOT NULL DEFAULT 'gate_official_delisting_api')""")
    c.execute("""CREATE TABLE IF NOT EXISTS historical_pair_registry(
      pair TEXT PRIMARY KEY,symbol TEXT NOT NULL,quote TEXT NOT NULL,
      first_source TEXT NOT NULL,current_trade_status TEXT,currency_delisted INTEGER,
      trade_disabled INTEGER,first_seen_utc TEXT NOT NULL,last_seen_utc TEXT NOT NULL,
      coverage_status TEXT NOT NULL DEFAULT 'QUEUED',attempts INTEGER NOT NULL DEFAULT 0,
      recovered_bars INTEGER NOT NULL DEFAULT 0,first_bar_ts INTEGER,last_bar_ts INTEGER,
      last_error TEXT,version TEXT NOT NULL)""")
    c.commit()

def pairs_from_text(text):
    text=html.unescape(str(text or "")).upper()
    syms=set(re.findall(r"\b([A-Z0-9][A-Z0-9._-]{0,40})_USDT\b",text))
    return {(f"{s}_USDT",s) for s in syms if s not in {"USDT","USD"}}

def main():
    os.makedirs(os.path.dirname(DB) or ".",exist_ok=True)
    errors=[]; articles=[]; total=0
    for page in range(1,PAGES+1):
        try:
            items,total=ann_page(page)
            if not items: break
            articles.extend(items)
            if total and len(articles)>=total: break
        except Exception as e:
            errors.append(f"api:{page}:{type(e).__name__}:{str(e)[:180]}")
            break

    found={}
    def parse_article(article):
        aid=str(article.get("id") or "")
        title=str(article.get("title") or "")
        brief=str(article.get("brief") or "")
        tags=str(article.get("tags") or "")
        cate=str(article.get("cate") or "")
        # Precision-first: only use fields returned directly by Gate's official
        # announcement API. Do not scrape miniapp HTML because page chrome can
        # contain unrelated market symbols and contaminate survivorship labels.
        blob="\n".join([title,brief,tags,cate])
        pairs=pairs_from_text(blob)
        url=f"{MINIAPP}{aid}" if aid else ""
        return aid,title,url,pairs,None

    for article in articles:
        try:
            aid,title,url,pairs,err=parse_article(article)
            for pair,sym in pairs:
                found[pair]=(sym,url,title)
        except Exception as e:
            errors.append(f"parse:{type(e).__name__}:{str(e)[:120]}")

    stamp=now()
    with sqlite3.connect(DB,timeout=60) as c:
        init(c)
        for pair,(sym,url,title) in found.items():
            c.execute("""INSERT INTO official_delist_registry(
              pair,symbol,announcement_url,first_seen_utc,last_seen_utc
            ) VALUES(?,?,?,?,?)
            ON CONFLICT(pair) DO UPDATE SET
              announcement_url=excluded.announcement_url,last_seen_utc=excluded.last_seen_utc""",
              (pair,sym,url,stamp,stamp))
            c.execute("""INSERT INTO historical_pair_registry(
              pair,symbol,quote,first_source,current_trade_status,currency_delisted,trade_disabled,
              first_seen_utc,last_seen_utc,coverage_status,version
            ) VALUES(?,?,?,'gate_official_delisting_api','DELISTED_OFFICIAL',1,1,?,?,'QUEUED',
                     'official-delist-api-v2-20260926')
            ON CONFLICT(pair) DO UPDATE SET
              current_trade_status='DELISTED_OFFICIAL',currency_delisted=1,trade_disabled=1,
              last_seen_utc=excluded.last_seen_utc""",
              (pair,sym,"USDT",stamp,stamp))
        c.commit()

    report={
      "generated_at_utc":stamp,"api_articles":len(articles),"api_total":total,
      "official_usdt_pairs":len(found),"source":"Gate API POST /ann/list_article (API fields only)",
      "errors":errors[:100]
    }
    with open("gate_delist_import_report.json","w",encoding="utf-8") as out:
        json.dump(report,out,ensure_ascii=False,indent=2)
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
