"""Draw the exact immutable decision snapshot; no second analysis in the chart."""
import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import CONFIG_HASH, VERSION


def drawing_spec(assessment, plan):
    if assessment['config_hash'] != CONFIG_HASH or assessment['version'] != VERSION:
        raise ValueError('CHART_VERSION_MISMATCH')
    if plan not in assessment['decisions']:
        raise ValueError('PLAN_NOT_IN_SNAPSHOT')
    return dict(symbol=assessment['symbol'], asof_ms=assessment['asof_ms'],
                interval=assessment['interval'], config_hash=CONFIG_HASH, event_id=plan['event_id'],
                zone=plan['decision_zone'], trigger=plan['trigger'], stop=plan['stop'],
                target=plan['target'], invalidation_anchor=plan['invalidation_anchor'],
                swings=plan['evidence_swings'], fibonacci=plan['fibonacci'],
                context=assessment['context'], first_obstacle=plan['first_obstacle'],
                analysis=assessment.get('chart_analysis'), model=plan['model'], plan_eligible=plan['plan_eligible'])


def render_detail(assessment, frames, plan, path):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.dates as dates
    from matplotlib.patches import Rectangle
    from matplotlib.ticker import MaxNLocator

    spec = drawing_spec(assessment, plan)
    for tf in (assessment['interval'], '1h', '4h'):
        if any(b['close_ms'] >= assessment['asof_ms'] for b in frames[tf]):
            raise ValueError('FUTURE_CANDLE_IN_CHART')
    tz = ZoneInfo('Europe/Istanbul')
    tf_labels = {'4h': '4 saat', '1h': '1 saat', '15m': '15 dakika', '30m': '30 dakika'}
    directions = {'UP': 'yükseliş', 'DOWN': 'düşüş', 'RANGE': 'yatay', 'UNKNOWN': 'yapı net değil'}
    stages = {'WATCH': 'İZLE', 'CONDITION_MET': 'KOŞUL OLUŞTU', 'LATE': 'GEÇ / KOVALAMA'}
    date = lambda ms: datetime.fromtimestamp(ms/1000, tz=timezone.utc)
    fig, axes = plt.subplots(6, 1, figsize=(12, 18), gridspec_kw={'height_ratios': [1, 1, 2.6, .5, .6, .6]})
    for ax, tf in zip(axes, ('4h', '1h', assessment['interval'])):
        rows = frames[tf][-80:]
        if tf == assessment['interval']:
            anchors = [spec['zone']['anchor']['open_ms'], spec['invalidation_anchor']['open_ms']]
            if spec['fibonacci']:
                anchors.append(spec['fibonacci']['start']['open_ms'])
            first_needed = min(anchors+[rows[0]['open_ms']])
            rows = [b for b in frames[tf] if b['open_ms'] >= first_needed]
        x = [dates.date2num(date(b['open_ms'])) for b in rows]
        width = (x[1]-x[0])*.65
        for t, b in zip(x, rows):
            up = b['close'] >= b['open']
            color = '#14776b' if up else '#b73542'
            ax.vlines(t, b['low'], b['high'], color=color, linewidth=.8)
            ax.add_patch(Rectangle((t-width/2, min(b['open'], b['close'])), width,
                                  max(abs(b['close']-b['open']), b['close']*.00001),
                                  facecolor='white' if up else color, edgecolor=color, linewidth=.8))
        ps = assessment['context'][tf]['swings']
        visible = [p for p in ps if p['open_ms'] >= rows[0]['open_ms']]
        ax.plot([date(p['open_ms']) for p in visible], [p['price'] for p in visible],
                color='#555555', linewidth=.8, marker='o', markersize=3, label='Teyitli anlamlı dip–tepe')
        ax.set_title(tf_labels[tf]+' · '+directions[assessment['context'][tf]['direction']], loc='left', fontsize=11)
        ax.set_ylabel('Fiyat · USDT')
        ax.xaxis.set_major_locator(MaxNLocator(4))
        ax.xaxis.set_major_formatter(dates.DateFormatter('%d/%m %H:%M', tz=tz))
        ax.tick_params(labelsize=9)
        ax.grid(alpha=.15)
        ax.set_xlim(x[0]-width, x[-1]+3*width)
        if tf != assessment['interval']:
            for z in assessment['levels']:
                if z['timeframe'] == tf and rows[-1]['close']*.8 < z['center'] < rows[-1]['close']*1.2:
                    ax.axhspan(z['low'], z['high'], color='#888888', alpha=.12)
    ax = axes[2]
    setup_rows = rows
    z = spec['zone']
    ax.axhspan(z['low'], z['high'], color='#3075ba', alpha=.15, label='Karar bölgesi')
    for label, value, color in [('Kırılım', spec['trigger'], '#3075ba'), ('Stop', spec['stop'], '#b73542'),
                                ('İlk engel', spec['target'], '#14776b')]:
        if value is not None:
            ax.axhline(value, color=color, linestyle='--', linewidth=1, label=f'{label}: {value:.8g}')
    fib = spec['fibonacci']
    if fib:
        a, b = fib['start'], fib['end']
        ax.plot([date(a['open_ms']), date(b['open_ms'])], [a['price'], b['price']],
                color='#8055a6', linewidth=2, label='Fibonacci dayanakları A–B')
        for label, p, offset in [('A', a, -15), ('B', b, 12)]:
            ax.annotate(label, (date(p['open_ms']), p['price']), xytext=(0, offset),
                        textcoords='offset points', fontsize=10)
        for ratio, value in fib['retracements'].items():
            ax.plot([date(b['open_ms']), date(frames[assessment['interval']][-1]['open_ms'])],
                    [value, value], ':', color='#8055a6', alpha=.65,
                    label=f'Fib %{float(ratio)*100:g}: {value:.8g}')
    analysis = spec['analysis']
    if analysis:
        ind = analysis['indicators']
        start = next(i for i,t in enumerate(ind['open_ms']) if t >= setup_rows[0]['open_ms'])
        times = [date(t) for t in ind['open_ms'][start:]]
        ax.plot(times, ind['ema20'][start:], color='#d39000', lw=1, label='EMA20')
        ax.plot(times, ind['ema50'][start:], color='#7758a5', lw=1, label='EMA50')
        visible_events=list({(e['kind'],e['side'],e['anchor_ms']):e for e in analysis['events']}.values())[-10:]
        for e in visible_events:
            if e['at_ms'] < setup_rows[0]['open_ms']: continue
            color = '#14776b' if e['side']=='LONG' else '#b73542'
            if e['kind']=='SWEEP':
                ax.annotate('Süpürme*', (date(e['at_ms']),e['level']), xytext=(-14,24 if e['side']=='LONG' else -28),
                            textcoords='offset points', fontsize=7, color=color, ha='center', arrowprops=dict(arrowstyle='->',color=color,lw=.6))
            else:
                ax.plot([date(max(e['anchor_ms'],setup_rows[0]['open_ms'])),date(e['at_ms'])], [e['level']]*2, color=color,lw=1.1)
                ax.annotate(e['kind'], (date(e['at_ms']),e['level']), xytext=(0,5), textcoords='offset points', fontsize=8,color=color,ha='right')
        zones = sorted(analysis['zones'], key=lambda z:abs((z['low']+z['high'])/2-setup_rows[-1]['close']))
        shown=zones[:4]
        for j,zann in enumerate(shown):
            left=date(max(zann['known_ms']+1,setup_rows[0]['open_ms']))
            right=date(setup_rows[-1]['close_ms'])
            if left>=right:continue
            color='#e7b94c' if zann['kind']=='OB_ADAY' else '#33a6b8'
            ax.add_patch(Rectangle((dates.date2num(left),zann['low']), dates.date2num(right)-dates.date2num(left),zann['high']-zann['low'],facecolor=color,edgecolor=color,alpha=.16))
            ax.annotate('OB adayı' if zann['kind']=='OB_ADAY' else 'FVG', (left,zann['high']),fontsize=8,color='#73561c' if zann['kind']=='OB_ADAY' else '#176d78')
        last_by_kind={}
        for swing in assessment['context'][assessment['interval']]['swings']:
            prior=last_by_kind.get(swing['kind']); last_by_kind[swing['kind']]=swing
            fib_times={spec['fibonacci'][k]['open_ms'] for k in ('start','end')} if spec['fibonacci'] else set()
            if prior and swing['open_ms']>=setup_rows[0]['open_ms'] and swing['open_ms'] not in fib_times:
                label=('HH' if swing['price']>prior['price'] else 'LH') if swing['kind']=='H' else ('HL' if swing['price']>prior['price'] else 'LL')
                ax.annotate(label,(date(swing['open_ms']),swing['price']),xytext=(0,10 if swing['kind']=='H' else -13),textcoords='offset points',fontsize=7,color='#555555')
        vol,rs,mc=axes[3:]
        vol.bar(times,ind['quote_volume'][start:],width=width,color=['#14776b' if b['close']>=b['open'] else '#b73542' for b in setup_rows])
        vol.set_ylabel('Hacim\nUSDT',fontsize=9)
        rs.plot(times,ind['rsi14'][start:],color='#7758a5',lw=1)
        rs.axhspan(30,70,color='#7758a5',alpha=.06)
        for value in (30,50,70):rs.axhline(value,color='#888888',lw=.5,ls=':')
        rs.set_ylim(0,100);rs.set_ylabel('RSI14',fontsize=9)
        mc.plot(times,ind['macd'][start:],color='#3075ba',lw=1,label='MACD12/26')
        mc.plot(times,ind['signal'][start:],color='#d39000',lw=1,label='Sinyal9')
        mc.bar(times,ind['histogram'][start:],width=width,color=['#14776b' if v>=0 else '#b73542' for v in ind['histogram'][start:]],alpha=.5)
        mc.set_ylabel('MACD',fontsize=9);mc.legend(loc='upper left',fontsize=7,ncol=2)
        for panel in (vol,rs,mc):
            panel.set_xlim(ax.get_xlim());panel.xaxis.set_major_locator(MaxNLocator(4))
            panel.xaxis.set_major_formatter(dates.DateFormatter('%d/%m %H:%M',tz=tz));panel.tick_params(labelsize=8);panel.grid(alpha=.12)
        counts=analysis['counts']
        footer=f"Son 60 mum: {counts['structure']} yapı kırılımı · {counts['sweeps']} süpürme* · {counts['active_fvg']} dokunulmamış FVG · {counts['ob_candidates']} OB adayı\nGrafikte en yakın {len(shown)} FVG/OB ve son 10 ayrı yapı işareti gösterilir; tamamı kayıtta saklanır."
        fig.text(.08,.035,footer,fontsize=9)
    if plan['plan_eligible']:
        entry=plan['entry_reference'];left=x[-1]+width;boxwidth=width*8
        for boundary,color in [(spec['stop'],'#b73542'),(spec['target'],'#14776b')]:
            ax.add_patch(Rectangle((left,min(entry,boundary)),boxwidth,abs(boundary-entry),facecolor=color,alpha=.18,edgecolor=color))
        ax.annotate(f'Giriş referansı {entry:.7g}',(left,entry),fontsize=8)
        ax.set_xlim(x[0]-width,x[-1]+width*12)
    model={'LEVEL_BREAK':'Seviye kırılımı / yaklaşım (şekil formasyonu değil)','DOUBLE_TOP':'Benzer iki tepe · yerel geometri adayı','DOUBLE_BOTTOM':'Benzer iki dip · yerel geometri adayı'}[plan['model']]
    if plan['model'] in ('DOUBLE_TOP','DOUBLE_BOTTOM'):
        kind='H' if plan['model']=='DOUBLE_TOP' else 'L'
        anchors=[p for p in spec['swings'] if p['kind']==kind][-2:]
        for n,pivot in enumerate(anchors,1):
            ax.scatter([date(pivot['open_ms'])],[pivot['price']],s=65,facecolors='none',edgecolors='#d39000',zorder=6)
            ax.annotate(('Tepe ' if kind=='H' else 'Dip ')+str(n),(date(pivot['open_ms']),pivot['price']),xytext=(12,20 if kind=='H' else -24),textcoords='offset points',fontsize=8,color='#815600')
    ax.set_title(tf_labels[assessment['interval']]+' · '+model,loc='left',fontsize=11,fontweight='bold')
    ax.legend(loc='upper left',fontsize=7,ncol=3,framealpha=.9)
    fig.text(.08,.012,'*Süpürme: fitil geçişi + geri kapanış; emir defteri kanıtı değil. OB: kurala dayalı aday. Göstergeler giriş onayı değildir.',fontsize=8)

    ax.set_xlabel('Tarih / saat · Türkiye')
    stamp = date(assessment['asof_ms']).astimezone(tz).strftime('%d.%m.%Y %H:%M')
    title = f"{assessment['symbol']} · {plan['direction']} · {stages.get(plan['stage'], plan['stage'])} · {stamp}"
    fig.suptitle(title+'\n'+('KOŞULLU PLAN · giriş, risk ve hedef sağda' if plan['plan_eligible'] else 'İŞLEM PLANI YOK · mavi bant izleme bölgesi, giriş bölgesi değil'), fontsize=12)
    axes[2].margins(y=.10)
    fig.tight_layout(rect=(0, .065, 1, .97))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    metadata = {'Description': json.dumps(spec, ensure_ascii=False)}
    fig.savefig(path, dpi=135, metadata=metadata, bbox_inches='tight', pad_inches=.2)
    plt.close(fig)
    return spec


def render(assessment, frames, plan, path):
    """Mobile-first single price chart. Full annotation chart is a separate file."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    from .presentation import card
    spec=drawing_spec(assessment,plan)
    for tf in (assessment['interval'],'1h','4h'):
        if any(b['close_ms']>=assessment['asof_ms'] for b in frames[tf]):
            raise ValueError('FUTURE_CANDLE_IN_CHART')
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    c=card(plan);spec['card']=c
    rows=frames[assessment['interval']][-48:]
    fig=plt.figure(figsize=(9,9),facecolor='#101923')
    ax=fig.add_axes([.09,.30,.68,.43],facecolor='#101923')
    text='#e7edf5';muted='#afbdcc';red='#ff7284';green='#50d0aa';blue='#65b8ff'
    title_color=green if c['eligible'] else '#f6c46b'
    fig.text(.07,.95,c['status'],color=title_color,fontsize=19,weight='bold')
    stamp=datetime.fromtimestamp(assessment['asof_ms']/1000,ZoneInfo('Europe/Istanbul')).strftime('%d/%m %H:%M')
    fig.text(.07,.91,f"{plan['symbol']} · {plan['direction']} senaryosu · {plan['interval']} · {stamp}",color=text,fontsize=13)
    fig.text(.07,.855,c['reason'],color=text,fontsize=12,wrap=True)
    fig.text(.07,.815,c['condition'],color=muted,fontsize=11,wrap=True)
    for i,b in enumerate(rows):
        color=green if b['close']>=b['open'] else red
        ax.vlines(i,b['low'],b['high'],color=color,lw=1)
        ax.add_patch(Rectangle((i-.3,min(b['open'],b['close'])),.6,max(abs(b['close']-b['open']),b['close']*.00001),facecolor=color,edgecolor=color))
    z=spec['zone'];ax.axhspan(z['low'],z['high'],color=blue,alpha=.12)
    ax.annotate('İzlenen seviye',(1,z['high']),xytext=(0,5),textcoords='offset points',color=blue,fontsize=9)
    n=len(rows);ax.set_xlim(-1,n+8)
    levels=[c['last'],c['stop'],c['target'],c['entry'] or c['trigger']]
    lo=min([b['low'] for b in rows]+[v for v in levels if v is not None]);hi=max([b['high'] for b in rows]+[v for v in levels if v is not None])
    pad=max((hi-lo)*.12,hi*.001);ax.set_ylim(lo-pad,hi+pad)
    label_levels=[('SL' if c['eligible'] else 'Geçersizlik',c['stop'],red),('TP' if c['eligible'] else 'İlk engel',c['target'],green),('Giriş' if c['eligible'] else 'Kırılım',c['entry'] or c['trigger'],blue)]
    # Avoid right-side text collisions while retaining leader lines to exact prices.
    positions=[]
    for label,value,color in sorted([v for v in label_levels if v[1] is not None],key=lambda v:v[1]):
        y=value
        if positions:y=max(y,positions[-1]+(hi-lo+2*pad)*.12)
        positions.append(y)
        ax.axhline(value,color=color,lw=1.2,ls='--')
        ax.annotate(f'{label}\n{value:.7g}',xy=(n+6,value),xytext=(1.02,(y-(lo-pad))/(hi-lo+2*pad)),textcoords='axes fraction',color=color,fontsize=11,weight='bold',va='center',annotation_clip=False,arrowprops=dict(arrowstyle='-',color=color,lw=.8))
    ax.scatter([n-1],[c['last']],color='white',s=23,zorder=6)
    if c['eligible']:
        for boundary,color in [(c['stop'],red),(c['target'],green)]:
            ax.add_patch(Rectangle((n+.3,min(c['entry'],boundary)),6,abs(boundary-c['entry']),facecolor=color,alpha=.24,edgecolor=color))
    ticks=list(range(0,n,max(1,n//4)))
    ax.set_xticks(ticks,[datetime.fromtimestamp(rows[i]['open_ms']/1000,ZoneInfo('Europe/Istanbul')).strftime('%d/%m\n%H:%M') for i in ticks])
    ax.tick_params(colors=muted,labelsize=9);ax.grid(alpha=.12,color='white')
    for spine in ax.spines.values():spine.set_color('#334453')
    ax.set_ylabel('Fiyat · USDT',color=muted,fontsize=10)
    f=lambda v:f'{v:.8g}' if v is not None else 'Bilinmiyor'
    fig.text(.07,.22,f"GİRİŞ REFERANSI: {f(c['entry'])}" if c['eligible'] else 'GİRİŞ: YOK · işlem planı doğrulanmadı',color=blue if c['eligible'] else title_color,fontsize=14,weight='bold')
    fig.text(.07,.175,f"{c['stop_label']}: {f(c['stop'])}",color=red,fontsize=12,weight='bold')
    fig.text(.07,.135,f"{c['target_label']}: {f(c['target'])}",color=green,fontsize=12,weight='bold')
    g=plan['payoff']
    if g.get('net_r') is not None:
        fig.text(.07,.09,f"Hedef %{g['room_pct']:.2f} · Risk %{g['risk_pct']:.2f} · Masraf sonrası {g['net_r']:.2f} R",color=text,fontsize=11)
    fig.text(.07,.035,'Karar anı referansları · emir değil · %0,30 masraf varsayımı, funding hariç.\nFiyat değişirse uygunluk değişebilir. Geçmiş test kârlılığı doğrulamadı.',color=muted,fontsize=9)
    fig.savefig(path,dpi=140,facecolor=fig.get_facecolor(),metadata={'Description':json.dumps(spec,ensure_ascii=False)})
    plt.close(fig)
    render_detail(assessment,frames,plan,path.with_name(path.stem+'-detail.png'))
    return spec
