"""Small PNG cards drawn from saved closed candles; no network or forecasts."""
from datetime import datetime, timezone
from io import BytesIO
import math
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

from .core import CFG, MINUTE, finite

BG = '#0b1120'
PANEL = '#151e30'
TEXT = '#edf3ff'
MUTED = '#9cabc3'
GREEN = '#3dd9a3'
RED = '#ff6b80'
BLUE = '#65baff'
AMBER = '#ffce6b'


def font(size, bold=False):
    name = 'DejaVuSans-Bold.ttf' if bold else 'DejaVuSans.ttf'
    path = Path('/usr/share/fonts/truetype/dejavu')/name
    return ImageFont.truetype(str(path) if path.exists() else name, size)


def price(value):
    value = finite(value, True)
    return f'{value:.10g}' if value < .000001 else f'{value:,.8f}'.rstrip('0').rstrip('.')


def local_time(ms):
    return datetime.fromtimestamp(ms/1000, timezone.utc).astimezone(ZoneInfo('Europe/Istanbul')).strftime('%d.%m %H:%M')


def render_plan(p, rows, n=0, demo=False):
    watch=p.get('status')=='WATCH'
    rows = [r for r in rows if int(r[0]) <= p['candle_ms']][-60:]
    if not rows:
        raise ValueError('CHART_HAS_NO_SAVED_CANDLES')
    for r in rows:
        for value in r[1:5]:
            finite(value, True)
        if not r[3] <= min(r[1],r[4]) <= max(r[1],r[4]) <= r[2]:
            raise ValueError('CHART_INVALID_CANDLE')
        if int(r[6])+CFG.publication_delay_ms >= p['observed_ms']:
            raise ValueError('CHART_OPEN_CANDLE_REFUSED')
    entry, sl, tp = [finite(p[k], True) for k in ('entry','stop','target')]
    direction = p['direction']
    if not (sl < entry < tp if direction == 'LONG' else tp < entry < sl):
        raise ValueError('CHART_INVALID_LEVELS')
    image = Image.new('RGB', (1440,1660), BG)
    draw = ImageDraw.Draw(image)

    def text(x,y,value,size=28,color=TEXT,bold=False,width=None):
        face = font(size,bold)
        while width and draw.textlength(str(value),font=face)>width and size>16:
            size-=1
            face=font(size,bold)
        draw.text((x,y),str(value),font=face,fill=color)

    sign_color = GREEN if direction=='LONG' else RED
    draw.rounded_rectangle((56,52,64,147), radius=4, fill=sign_color)
    text(86,48,p['symbol'],52,bold=True,width=700)
    text(88,115,'30 dk mum  /  4 saat yön filtresi',27,MUTED)
    draw.rounded_rectangle((1130,56,1380,126),radius=18,fill=sign_color)
    text(1172,68,direction,38,BG,True)
    status = {'WATCH':'TEKNİK ADAY / İZLE','PENDING':'TEST PLANI','OPEN':'SANAL GİRİŞ','CLOSED':'KAPANAN TEST','CANCELLED':'İPTAL EDİLDİ'}.get(p['status'],'TEST')
    text(60,187,status,32,AMBER,True)
    text(1365-draw.textlength(local_time(p['observed_ms'])+'  TSİ',font=font(26)),193,
         local_time(p['observed_ms'])+'  TSİ',26,MUTED)
    if demo:
        draw.rounded_rectangle((56,243,1384,301),radius=12,fill='#40341e')
        text(81,253,'ÖRNEK GÖRSEL • YAPAY VERİ • GERÇEK SİNYAL DEĞİL',27,AMBER,True)
    else:
        text(60,253,'Sinyal anında kaydedilen kapanmış mumlar • Canlı fiyat değildir',25,MUTED)

    left, right, top, bottom = 82, 1060, 347, 885
    all_values = [sl,tp,entry]+[float(r[i]) for r in rows for i in (2,3)]
    lo,hi=min(all_values),max(all_values)
    pad=max((hi-lo)*.12,entry*.0002)
    lo,hi=lo-pad,hi+pad
    y=lambda value: bottom-(value-lo)/(hi-lo)*(bottom-top)
    draw.rounded_rectangle((56,325,1384,956),radius=18,fill=PANEL)
    for i in range(6):
        yy=top+(bottom-top)*i/5
        draw.line((left,yy,right,yy),fill='#293348',width=1)
        text(left+4,yy-23,f'{hi-(hi-lo)*i/5:.5g}',18,MUTED)
    # Colored level zones are static plan levels, not a projected price path.
    zone_left=right-190
    draw.rectangle((zone_left,min(y(entry),y(sl)),right,max(y(entry),y(sl))),fill='#362334')
    draw.rectangle((zone_left,min(y(entry),y(tp)),right,max(y(entry),y(tp))),fill='#173638')
    step=(right-left-45)/max(1,len(rows))
    for i,r in enumerate(rows):
        x=left+step*(i+.5)
        o,h,l,c=map(float,r[1:5])
        color=GREEN if c>=o else RED
        draw.line((x,y(h),x,y(l)),fill=color,width=2)
        half=max(2,min(7,step*.3))
        draw.rectangle((x-half,min(y(o),y(c)),x+half,max(y(o),y(c))+1),fill=color)
    levels=[('Teknik hedef' if watch else 'TP / Take-profit',tp,GREEN),
            ('Sinyal kapanışı' if watch else 'Giriş / Entry',entry,BLUE),('SL / Stop-loss',sl,RED)]
    positions=[]
    for label,value,color in sorted(levels,key=lambda item:y(item[1])):
        yy=max(top+35,min(bottom-35,y(value)))
        if positions:
            yy=max(yy,positions[-1][0]+86)
        positions.append((yy,label,value,color))
    overflow=max(0,positions[-1][0]-(bottom-35))
    positions=[(yy-overflow,label,value,color) for yy,label,value,color in positions]
    for yy,label,value,color in positions:
        actual=y(value)
        for x in range(left,right,18):
            draw.line((x,actual,min(x+10,right),actual),fill=color,width=2)
        draw.line((right,actual,1080,yy),fill=color,width=2)
        draw.rounded_rectangle((1080,yy-34,1365,yy+37),radius=9,fill=BG,outline=color,width=2)
        text(1096,yy-27,label,19,color,True)
        text(1096,yy-3,price(value),28,color,True,width=254)
    for index in (0,len(rows)//2,len(rows)-1):
        x=left+step*(index+.5)
        text(min(right-140,max(left,x-70)),912,local_time(rows[index][0]),20,MUTED)

    def card(x,y0,label,value,color=TEXT):
        draw.rounded_rectangle((x,y0,x+426,y0+111),radius=14,fill=PANEL)
        text(x+20,y0+14,label,23,MUTED,width=385)
        text(x+20,y0+48,value,34,color,True,width=385)

    for x,label,value,color in [(56,'SİNYAL KAPANIŞI' if watch else 'GİRİŞ / ENTRY',price(entry),BLUE),
                              (506,'TEKNİK STOP' if watch else 'STOP-LOSS / SL',price(sl),RED),
                              (956,'TEKNİK HEDEF' if watch else 'TAKE-PROFIT / TP',price(tp),GREEN)]:
        card(x,980,label,value,color)
    if watch:
        for x,label,value in [(56,'STOPA UZAKLIK',f'%{100*abs(entry-sl)/entry:.2f}'),
                              (506,'HEDEFE UZAKLIK',f'%{100*abs(tp-entry)/entry:.2f}'),
                              (956,'BRÜT ÖDÜL / RİSK',f'{abs(tp-entry)/abs(entry-sl):.2f} R')]:
            card(x,1108,label,value)
        draw.rounded_rectangle((56,1236,1384,1510),radius=14,fill=PANEL)
        text(76,1254,'SANAL HESAP UYARISI',26,AMBER,True)
        words=(p['risk_note']+' İşlem büyüklüğü ve giriş kararı sana ait.').split()
        lines=[];line=''
        for word in words:
            trial=(line+' '+word).strip()
            if draw.textlength(trial,font=font(25))>1255:
                lines.append(line);line=word
            else:line=trial
        if line:lines.append(line)
        for i,line in enumerate(lines[:5]):text(76,1302+i*34,line,25)
        text(60,1543,'İZLEME ADAYI • İŞLEM ONAYI DEĞİL • GERÇEK EMİR YOK',26,AMBER,True,width=1320)
        text(60,1588,'Brüt seviyeler: masraflar hariç. Güncel fiyat/likidite ayrıca kontrol edilmeli.',22,MUTED,width=1320)
        out=BytesIO();image.save(out,format='PNG',optimize=False)
        return out.getvalue()
    for x,label,value in [(56,'POZİSYON TUTARI',f"{p['notional']:.2f} USDT"),(506,f"ÖRNEK {CFG.indicative_leverage}x TEMİNAT",f"{p['indicative_margin']:.2f} USDT"),(956,'NET ÖDÜL / RİSK',f"{p['net_rr']:.2f} R")]:
        card(x,1108,label,value)
    for x,label,value,color in [(56,'PLANLANAN SL KAYBI',f"{p['planned_loss']:.2f} USDT",RED),(506,'KÖTÜ KAYMA KAYBI',f"{p['stress_loss']:.2f} USDT",AMBER),(956,'TP NET KAZANÇ*',f"{p['target_net']:.2f} USDT",GREEN)]:
        card(x,1236,label,value,color)
    detail = f"Miktar: {p['quantity']:.8g}  •  Sanal giriş: {local_time(p['entry_ms'])} TSİ"
    if p['status']=='CLOSED':
        detail=f"{p['exit_reason']}  •  Sonuç: {p['net_pnl']:+.2f} USDT / {p['net_r']:+.2f} R"
    elif p['status']=='CANCELLED':
        detail='İptal: '+p.get('cancel_reason','Koşul bozuldu')
    text(60,1375,detail,26,width=1310)
    text(60,1420,f"Plan son geçerlilik: {local_time(p['expires_ms'])} TSİ • Fiyat kaçarsa giriş iptal",23,MUTED,width=1310)
    text(60,1470,'* Ücret ve kayma varsayımları dahil; hedef hesabında funding hariç.',23,MUTED)
    draw.line((56,1522,1384,1522),fill='#354158',width=2)
    text(60,1543,f"GERÇEK EMİR YOK  •  {CFG.paper_initial_equity:,.0f} USDT ÖRNEK HESAP",27,AMBER,True)
    text(60,1588,f"Başarı: DOĞRULANMADI (N={n})  •  Kaldıraç ve likidasyon uygunluğu ölçülmedi",23,MUTED,width=1320)
    out=BytesIO()
    image.save(out,format='PNG',optimize=False)
    return out.getvalue()


def preview(now, n=0):
    """A conspicuously synthetic delivery check, never a real-coin signal."""
    end=(now-CFG.publication_delay_ms)//CFG.entry_ms*CFG.entry_ms
    rows=[]
    for i in range(60):
        c=96+i*.06+math.sin(i*.42)*.65
        o=c-math.sin(i*.75)*.35
        start=end-(60-i)*CFG.entry_ms
        rows.append([start,o,max(o,c)+.35,min(o,c)-.35,c,100,start+CFG.entry_ms-1])
    from .core import sizing
    market={'filters':[{'filterType':'PRICE_FILTER','tickSize':'0.01'},
        {'filterType':'MARKET_LOT_SIZE','stepSize':'0.001','minQty':'0.001','maxQty':'10000'},
        {'filterType':'MIN_NOTIONAL','notional':'5'}]}
    p=dict(symbol='ÖRNEK / YAPAY VERİ',direction='LONG',status='PENDING',observed_ms=now,
           candle_ms=rows[-1][0],reference_entry=100,stop=98,target=104,entry_limit=100.4,
           entry_ms=(now//MINUTE+1)*MINUTE,expires_ms=now+10*MINUTE)
    p.update(sizing(p,market,1000,5))
    return render_plan(p,rows,n=n,demo=True)
