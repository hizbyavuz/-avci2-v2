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


def trade_plan_message(row, price, execution, data_brief, format_price):
    """Explain the existing locked plan; never create targets or change gates."""
    direction = str(row['direction'])
    stop, target = float(row['invalidation']), float(row['target1'])
    second = float(row['target2'] or 0)
    long = direction == 'LONG'
    if not (stop < price < target if long else target < price < stop):
        raise ValueError('invalid trade plan geometry')

    def level(value, meaning):
        change = signed_return_pct(direction, price, value)
        return f'{format_price(value)} — {meaning} ({change:+.2f}%)'

    second_valid = math.isfinite(second) and (second > target if long else 0 < second < target)
    lines = [
        f"{'🟢' if long else '🔴'} {direction} SİNYALİ | {row['symbol']}",
        f"Yön: {'yükseliş' if long else 'düşüş'}",
        f'📍 Giriş referansı: {format_price(price)} (sinyal anındaki fiyat)',
        '🛡️ SL: ' + level(stop, 'zarar kes'),
        '🎯 TP1: ' + level(target, 'ilk kâr hedefi'),
        '🎯 TP2: ' + (level(second, 'ikinci hedef; izleme seviyesi') if second_valid else 'belirlenmedi'),
        '',
        '📌 Neden: 5 dk kapanış, devam ve son giriş kontrolleri tamamlandı.',
    ]
    if execution.get('qualified'):
        ratio = number(execution.get('net_t1_r_cost_inclusive_observation', execution.get('net_t1_r')))
        if ratio != 'yok':
            lines.append(f'📐 TP1 hesabı: Tahmini masraflarla 1 birim riske karşı {ratio} birim hedef kazanç.')
        cost = number(execution.get('round_trip_cost_pct'))
        if cost != 'yok':
            lines.append(f'Masraf varsayımı: %{cost} (komisyon + fiyat kayması; taşıma ödemesi hariç).')
    lines += [
        '⏱ Sanal takip: SL veya TP1’de kapanış; ikisi de gelmezse 3 saat sonunda kapanış.',
        'Bu süre mevcut test kuralıdır; en uygun süre olduğu henüz doğrulanmadı.',
        'Yüzdeler kaldıraç hariçtir. Giriş fiyatı değişirse kazanç/risk hesabı da değişir.',
        '🧪 Kârlılık henüz doğrulanmadı; otomatik emir açılmadı.',
        '',
        '📊 Kısa veri özeti',
    ]
    # Keep the existing recorded evidence underneath the actionable levels.
    for line in data_brief.splitlines():
        clean = line.removeprefix('📊').removeprefix('•').strip()
        lines.extend('• ' + field.strip() for field in clean.split(' | ') if field.strip())
    return '\n'.join(lines)
