"""Closed native rolling-hour trade pressure and contemporaneous price response."""
import math
import sqlite3

SOURCES = {'BINANCE_UM_FUTURES_WEBSOCKET', 'BINANCE_UM_FUTURES_ARCHIVE', 'BINANCE_UM_FUTURES_REST'}


def hourly_flow(con, symbol, now_ms):
    # The boundary deliberately lags by 250ms, matching closed-chart validation.
    end = (now_ms-250)//300000*300000
    start = end-3600000
    try:
        rows = con.execute("""SELECT open_ms,close_ms,open,close,quote_volume,taker_buy_quote,source
            FROM futures_closed_klines WHERE symbol=? AND interval='5m'
            AND open_ms>=? AND open_ms<? ORDER BY open_ms""", (symbol,start,end)).fetchall()
    except sqlite3.Error:
        return dict(status='UNAVAILABLE')
    if [int(r[0]) for r in rows] != list(range(start,end,300000)):
        return dict(status='INCOMPLETE')
    for t,close_ms,op,cl,q,b,source in rows:
        if (source not in SOURCES or close_ms != t+299999 or
                any(not isinstance(v,(float,int)) or not math.isfinite(v) for v in (op,cl,q,b)) or
                min(op,cl)<=0 or q<0 or not 0<=b<=q):
            return dict(status='INVALID')
    total=sum(r[4] for r in rows);buy=sum(r[5] for r in rows)
    if total<=0:return dict(status='NO_VOLUME')
    def share(part):
        q=sum(r[4] for r in part)
        return sum(r[5] for r in part)/q if q>0 else None
    previous,recent=share(rows[:6]),share(rows[6:])
    return dict(status='OK',start_ms=start,end_ms=end,window_minutes=60,
                buy_share=buy/total,quote_volume=total,
                price_return_pct=(rows[-1][3]/rows[0][2]-1)*100,
                previous_30m_buy_share=previous,recent_30m_buy_share=recent)


def reversal_reason(flow, direction):
    if not flow or flow.get('status')!='OK':return 'REVERSAL_HOURLY_FLOW_UNAVAILABLE'
    sign=1 if direction=='LONG' else -1
    if sign*(flow['buy_share']-.5)<=0:return 'REVERSAL_FLOW_NOT_ALIGNED'
    if sign*flow['price_return_pct']<=0:return 'REVERSAL_PRICE_NOT_RESPONDING'
    return None


def flow_note(flow, direction):
    if not flow or flow.get('status')!='OK':
        return 'Son 60 dk alış/satış verisi eksik; akış desteği doğrulanamadı.'
    sign=1 if direction=='LONG' else -1
    pressure=sign*(flow['buy_share']-.5)>0
    response=sign*flow['price_return_pct']>0
    if pressure and response:note='Son 60 dk işlem baskısı ve fiyat hareketi bu yönü destekliyor.'
    elif pressure:note='İşlem baskısı bu yönde, fakat fiyat karşılık vermiyor; tek başına teyit değil.'
    elif response:note='Fiyat bu yönde ilerliyor, fakat son 60 dk işlem baskısı desteklemiyor.'
    else:note='Son 60 dk işlem baskısı ve fiyat hareketi bu yönü desteklemiyor.'
    old,new=flow.get('previous_30m_buy_share'),flow.get('recent_30m_buy_share')
    if old is not None and new is not None and sign*(new-old)<0:
        note+=' Son yarım saatte bu yöndeki işlem payı zayıfladı.'
    return note
