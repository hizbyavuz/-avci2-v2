"""Plain Turkish display helpers. No scoring, thresholds or trade decisions."""
import json
import math
import os
import sqlite3
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def number(value, digits=2, signed=False):
    try:
        if isinstance(value,bool):return 'yok'
        n=float(value)
        if not math.isfinite(n):return 'yok'
        return format(n,('+' if signed else '')+'.'+str(digits)+'f')
    except (TypeError,ValueError,OverflowError):return 'yok'


def local_time(value):
    try:
        dt=datetime.fromisoformat(value.replace('Z','+00:00'))
        if dt.tzinfo is None:dt=dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(ZoneInfo('Europe/Istanbul')).strftime('%H:%M')
    except (ValueError,AttributeError,TypeError):return 'yok'


def analysis_fields(symbol,db,now=None):
    now=now or datetime.now(timezone.utc)
    try:
        with sqlite3.connect('file:'+os.path.abspath(db)+'?mode=ro',uri=True,timeout=2) as c:
            row=c.execute('SELECT scan_time_utc,payload_json FROM analyses WHERE symbol=? ORDER BY scan_time_utc DESC LIMIT 1',(symbol,)).fetchone()
        if not row:return {'status':'bekleniyor'}
        dt=datetime.fromisoformat(row[0].replace('Z','+00:00'))
        if dt.tzinfo is None:dt=dt.replace(tzinfo=timezone.utc)
        if not -2<=(now-dt).total_seconds()<=600:return {'status':'eski kayıt','time':local_time(row[0])}
        p=json.loads(row[1] or '{}');t=p.get('t15') or {};oi=p.get('oi') or {}
        trend='belirsiz'
        try:
            if float(t['price'])>float(t['ema20'])>float(t['ema50']):trend='yukarı'
            elif float(t['price'])<float(t['ema20'])<float(t['ema50']):trend='aşağı'
            else:trend='karışık'
        except (KeyError,TypeError,ValueError):pass
        evidence=p.get('native_derivative_evidence') or {}
        return {'status':'kayıtlı','time':local_time(row[0]),'trend':trend,
            'strength':t.get('rsi'),'oi_change':oi.get('oi_change_1h'),
            'funding':p.get('funding_pct'),'taker_ratio':p.get('taker_ratio'),
            'account_ratio':p.get('long_short_ratio'),
            'btc':{'UP':'yukarı','DOWN':'aşağı','RANGE':'yatay'}.get(p.get('market_regime'),'belirsiz'),
            'coverage':'4/4 kayıtlı' if evidence.get('ready') else 'eksik/belirsiz'}
    except (sqlite3.Error,OSError,ValueError,TypeError,AttributeError):return {'status':'okunamadı'}


def analysis_line(fields):
    if fields.get('status')!='kayıtlı':
        return 'Ayrıntılı veri: '+fields.get('status','bekleniyor')
    def measure(key,suffix,digits=2,signed=False):
        value=number(fields.get(key),digits,signed)
        return value+suffix if value!='yok' else value
    return ('Yön 15 dk: '+fields['trend']+
        ' | Hareket gücü: '+measure('strength','/100',0)+
        ' | Açık işlemler 1 sa: '+measure('oi_change','%',2,True)+
        ' | Taşıma oranı: '+measure('funding','%',4,True)+
        ' | Alış/satış: '+measure('taker_ratio','×')+
        ' | Long/short hesap: '+measure('account_ratio','×')+
        ' | BTC: '+fields['btc']+' | Vadeli veri: '+fields['coverage']+
        ' | Analiz: '+fields['time'])


def signed_return_pct(direction,entry,price):
    if direction not in ('LONG','SHORT') or not all(math.isfinite(float(x)) and float(x)>0 for x in (entry,price)):
        raise ValueError('invalid direction or price')
    return (float(price)/float(entry)-1)*100*(1 if direction=='LONG' else -1)
