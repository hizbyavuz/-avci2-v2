#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Time-bounded, single-provider historical 1m candles for V3.1 outcomes.

No live scoring/entry code imports this module. If Binance Spot blocks with
HTTP 451, independently try Gate USDT perpetual and then Bybit linear. Never
splice candles across venues or mislabel fallback as venue-matched execution.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from long_short_data_router import _gate, _bybit

MINUTE_MS=60_000


class PriceSeries(list):
    def __init__(self, rows, source, requested_source, *, source_inferred=False):
        super().__init__(rows)
        self.source=source
        self.requested_source=requested_source
        self.source_inferred=bool(source_inferred)
        self.venue_matched=source==requested_source and not source_inferred


def _ms(value):
    if isinstance(value,datetime):
        if value.tzinfo is None:
            raise ValueError("naive outcome timestamp")
        return int(value.timestamp()*1000)
    raise TypeError("start/end must be timezone-aware datetimes")


def _normalize(rows, begin_ms, end_ms):
    """Returns sorted, unique, strictly bounded 1m candles; no interpolation."""
    out={}
    for r in rows:
        try:
            t=int(r[0])
            op,hi,lo,cl=[float(r[i]) for i in range(1,5)]
            if begin_ms<=t<end_ms and all(math.isfinite(v) and v>0 for v in (op,hi,lo,cl)):
                if lo<=min(op,cl)<=max(op,cl)<=hi:
                    out[t]=[t,str(op),str(hi),str(lo),str(cl),"0",t+59999]
        except (TypeError,ValueError,IndexError,OverflowError):
            continue
    return [out[k] for k in sorted(out)]


def _chunks(begin_ms,end_ms,max_minutes=900):
    left=(begin_ms//MINUTE_MS)*MINUTE_MS
    while left<end_ms:
        right=min(left+max_minutes*MINUTE_MS,end_ms)
        yield left,right
        left=right


def _gate_rows(symbol,begin_ms,end_ms):
    base=symbol[:-4] if symbol.endswith("USDT") else symbol
    result=[]
    for a,b in _chunks(begin_ms,end_ms,900):
        data=_gate("/futures/usdt/candlesticks",{
            "contract":f"{base}_USDT",
            "from":a//1000,
            "to":(b-1)//1000,
            "interval":"1m",
        })
        if not isinstance(data,list):
            raise RuntimeError("Gate futures candle data not list")
        for x in data:
            if not isinstance(x,dict):
                continue
            t=int(float(x.get("t") or 0))*1000
            result.append([t,x.get("o"),x.get("h"),x.get("l"),x.get("c")])
    return _normalize(result,begin_ms,end_ms)


def _bybit_rows(symbol,begin_ms,end_ms):
    result=[]
    for a,b in _chunks(begin_ms,end_ms,900):
        x=_bybit("/v5/market/kline",{
            "category":"linear","symbol":symbol,"interval":"1",
            "start":a,"end":b-1,"limit":1000,
        })
        raw=x.get("result",{}).get("list") or []
        for row in raw:
            if len(row)>=5:
                result.append([int(row[0]),row[1],row[2],row[3],row[4]])
    return _normalize(result,begin_ms,end_ms)


def _binance_rows(symbol,begin_ms,end_ms,fetch_binance):
    result=[]
    for a,b in _chunks(begin_ms,end_ms,900):
        cursor=a
        while cursor<b:
            x=fetch_binance("/api/v3/klines",{
                "symbol":symbol,"interval":"1m",
                "startTime":cursor,"endTime":b-1,"limit":1000,
            })
            if not isinstance(x,list):
                raise RuntimeError("Binance spot candle response not list")
            if not x:
                break
            result.extend(x)
            nxt=int(x[-1][6])+1
            if nxt<=cursor:
                raise RuntimeError("Binance non-advancing cursor")
            cursor=nxt
            if len(x)<1000:
                break
    return _normalize(result,begin_ms,end_ms)


def _usable(rows,begin_ms,end_ms):
    """Minimum first-finished horizon for a candidate; no silent time gaps.

    The caller may request 180m while only 15m has elapsed; requiring
    all 180m at once would incorrectly prevent the already-matured 15m label.
    """
    if not rows:
        return False
    first=(begin_ms//MINUTE_MS)*MINUTE_MS
    if rows[0][0]>first+MINUTE_MS:
        return False
    last=None
    for r in rows:
        if last is not None and r[0]-last>MINUTE_MS:
            return False
        last=r[0]
    return True


def has_full_horizon(rows,start,end):
    """Require 1m bars covering the complete matured horizon without gaps."""
    a=_ms(start); b=_ms(end)
    selected=[r for r in rows if a-MINUTE_MS<int(r[0])<b]
    if not selected:
        return False
    # The first candle must contain the entry time or be the next full minute.
    if int(selected[0][0])>a+MINUTE_MS:
        return False
    # A full bar must be available within one minute of the target endpoint.
    if int(selected[-1][0])+MINUTE_MS<b:
        return False
    return all(int(y[0])-int(x[0])==MINUTE_MS for x,y in zip(selected,selected[1:]))


def historical_1m(symbol,start,end,*,requested_source="BINANCE_SPOT",
                  allow_fallback=True,fetch_binance=None,source_inferred=False):
    a=_ms(start); b=_ms(end)
    if a>=b:
        raise ValueError("invalid outcome range")
    if not symbol.endswith("USDT"):
        raise ValueError("outcome symbol must be USDT")
    if requested_source not in ("BINANCE_SPOT","GATE_FUTURES","BYBIT_LINEAR"):
        raise ValueError("unrecognized requested source")
    order=[requested_source]
    if allow_fallback:
        order += [x for x in ("GATE_FUTURES","BYBIT_LINEAR","BINANCE_SPOT") if x not in order]
    errors=[]
    for src in order:
        try:
            if src=="BINANCE_SPOT":
                if fetch_binance is None:
                    raise RuntimeError("Binance fetch callback unavailable")
                rows=_binance_rows(symbol,a,b,fetch_binance)
            elif src=="GATE_FUTURES":
                rows=_gate_rows(symbol,a,b)
            else:
                rows=_bybit_rows(symbol,a,b)
            if _usable(rows,a,b):
                return PriceSeries(rows,src,requested_source,source_inferred=source_inferred)
            raise RuntimeError("missing or discontinuous historical candles")
        except Exception as exc:
            errors.append(f"{src}:{type(exc).__name__}:{str(exc)[:80]}")
    raise RuntimeError("no complete outcome price source: "+" | ".join(errors))
