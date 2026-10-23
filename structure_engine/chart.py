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
                context=assessment['context'], first_obstacle=plan['first_obstacle'])


def render(assessment, frames, plan, path):
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
    fig, axes = plt.subplots(3, 1, figsize=(11, 11), gridspec_kw={'height_ratios': [1, 1, 1.7]})
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
    ax = axes[-1]
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
    ax.legend(loc='upper center', bbox_to_anchor=(.5, -.15), ncol=2, fontsize=9, frameon=False)
    ax.set_xlabel('Tarih / saat · Türkiye')
    stamp = date(assessment['asof_ms']).astimezone(tz).strftime('%d.%m.%Y %H:%M')
    title = f"{assessment['symbol']} · {plan['direction']} · {stages.get(plan['stage'], plan['stage'])} · {stamp}"
    fig.suptitle(title+'\nKarar anı görünümü · sonrası gizli · araştırma, emir değil', fontsize=12)
    fig.tight_layout(rect=(0, .035, 1, .95))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    metadata = {'Description': json.dumps(spec, ensure_ascii=False)}
    fig.savefig(path, dpi=135, metadata=metadata, bbox_inches='tight', pad_inches=.2)
    plt.close(fig)
    return spec
