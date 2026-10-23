"""Readable Turkish text and chart from the very same saved display object."""
from datetime import datetime
from pathlib import Path
from textwrap import wrap
from zoneinfo import ZoneInfo

from .records import decimal, validate_record


def timestamp(record):
    return datetime.fromtimestamp(record['asof_ms']/1000, ZoneInfo('Europe/Istanbul')).strftime('%d.%m.%Y %H:%M')


def message(record):
    validate_record(record)
    d, p = record['display'], record['display']['price_labels']
    r = d['net_r']
    lines = [f"{record['symbol']} | {record['direction']} | 15m", d['state'],
             f"Karar zamanı: {timestamp(record)} Türkiye", 'Yapı: bölge kenarı kırılımı; isimli formasyon yok.',
             f"GİRİŞ REFERANSI: {p['entry']} (bölge kenarı; otomatik giriş değil)",
             f"STOP (SL) REFERANSI: {p['stop']}", f"HEDEF (TP) REFERANSI: {p['target']}",
             f"Kırılan bölge ({d['broken_region']['timeframe']}): {p['band_low']} – {p['band_high']}",
             f"Son kapanış: {p['last']}"]
    if r['value'] is not None:
        lines.append(f"Hedef %{decimal(r['reward_pct']):.2f} | Risk %{decimal(r['risk_pct']):.2f} | "
                     f"Masraf sonrası {decimal(r['value']):.2f} R")
    lines.append(' · '.join(f"{tf}: {label}" for tf, label in d['trend_labels'].items()))
    anchor = record['evidence']['setup']['stop'].get('anchor')
    if anchor:
        lines.append('SL dayanağı: kırılımdan önce onaylanan en yakın 15m '+
                     ('dip' if record['direction'] == 'LONG' else 'tepe')+' ve 0,3 ATR tampon.')
    if d['target_region']:
        lines.append('TP dayanağı: kırılan bandın tamamen ötesindeki ilk uygun karşı bölgenin yakın kenarı.')
    lines.extend(d['reasons'])
    lines += ['Gözlem kaydı; gerçek emir/giriş yok. Canlı işlem uygunluğu doğrulanmadı.',
              '%0,30 toplam komisyon/kayma varsayımı; funding hariç.',
              'Kayıt: '+record['record_hash'][:12]]
    return '\n'.join(lines)


def render(record, path):
    """No swing/region/price/R recomputation. Exact record prices drive artists."""
    validate_record(record)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    import matplotlib.ticker as mticker

    d, p, v = record['display'], record['display']['price_labels'], record['display']['levels']
    bg, fg, muted = '#111723', '#f3f5fa', '#a5b1c4'
    green, red, blue, yellow = '#63d6ad', '#ff7b92', '#72b9ff', '#f2c56e'
    fig = plt.figure(figsize=(10, 12), facecolor=bg)
    fig.text(.06, .957, f"{record['symbol']} · {record['direction']} · 15m", color=fg, fontsize=23, weight='bold')
    fig.text(.06, .925, d['state'], color=yellow, fontsize=15, weight='bold')
    source = record['evidence']['source_verification']
    archive = isinstance(source, dict) and source.get('kind') == 'ARCHIVE_PREVIEW'
    fig.text(.06, .897, timestamp(record)+' Türkiye · '+('ARŞİV ÖNİZLEMESİ' if archive else 'GÖZLEM / EMİR DEĞİL'), color=muted, fontsize=11)
    fig.text(.06, .870, 'Bölge kenarı kırılımı · İsimli formasyon yok', color=fg, fontsize=12)
    trends = '   |   '.join(f'{tf}: {label.lower()}' for tf, label in d['trend_labels'].items())
    fig.text(.06, .844, trends, color=muted, fontsize=11)
    ax = fig.add_axes([.08, .39, .68, .42], facecolor=bg)
    rows = d['candles']; n = len(rows)
    indices = {b['open_ms']: i for i, b in enumerate(rows)}
    for i, b in enumerate(rows):
        color = green if b['close'] >= b['open'] else red
        ax.vlines(i, b['low'], b['high'], color=color, linewidth=.9)
        # Exact open/close; a doji is a horizontal line, no fabricated body height.
        if b['close'] == b['open']:
            ax.hlines(b['open'], i-.32, i+.32, color=color)
        else:
            ax.add_patch(Rectangle((i-.32, min(b['open'], b['close'])), .64,
                                  abs(b['close']-b['open']), color=color))
    region = d['broken_region']
    if region['low'] is not None and region['high'] is not None:
        # Zone validity starts when its last member swing was confirmed.
        start = max(0., (region['known_ms']-rows[0]['open_ms'])/900000)
        ax.add_patch(Rectangle((start, float(region['low'])), n+6-start,
                              float(region['high'])-float(region['low']), color=blue, alpha=.18))
    target_zone = d['target_region']
    if target_zone:
        start = max(0., (target_zone['known_ms']-rows[0]['open_ms'])/900000)
        ax.add_patch(Rectangle((start, float(target_zone['low'])), n+6-start,
                              float(target_zone['high'])-float(target_zone['low']), color=green, alpha=.08))
    for dot in d['swing_dots']:
        if dot['open_ms'] in indices:
            is_anchor = dot['id'] == d['stop_anchor_id']
            ax.scatter(indices[dot['open_ms']], float(dot['price']), s=45 if is_anchor else 18,
                       color=yellow if is_anchor else muted, zorder=5,
                       marker='v' if dot['kind'] == 'H' else '^')
    if d['level_layout_valid']:
        for name, color in [('stop', red), ('target', green)]:
            a, b = float(v['entry']), float(v[name])
            ax.add_patch(Rectangle((n+.1, min(a, b)), 4.5, abs(a-b),
                                  facecolor=color, alpha=.20, edgecolor=color))
    values = [b[k] for b in rows for k in ('low', 'high')]
    values += [float(x) for x in v.values() if x is not None]
    if target_zone:
        values += [float(target_zone[k]) for k in ('low', 'high')]
    lo, hi = min(values), max(values); pad = max((hi-lo)*.09, hi*.0005)
    ax.set_ylim(lo-pad, hi+pad); ax.set_xlim(-1, n+6)
    levels = [(float(v[k]), k, label, color) for k, label, color in
              [('entry', 'GİRİŞ REF.', blue), ('stop', 'STOP · SL', red), ('target', 'HEDEF · TP', green)]
              if v[k] is not None]
    # Separate adjacent text labels without shifting the actual horizontal line.
    label_positions = []
    separation = (hi-lo+2*pad)*.10
    for value, key, label, color in sorted(levels):
        label_y = max(value, label_positions[-1]+separation) if label_positions else value
        label_positions.append(label_y)
    overflow = max(0, label_positions[-1]-(hi+pad)*1.0) if label_positions else 0
    for (value, key, label, color), label_y in zip(sorted(levels), label_positions):
        ax.axhline(value, color=color, linestyle='--', linewidth=1.1)
        ax.annotate(label+'\n'+p[key], xy=(n+5.7, value), xytext=(n+7.5, label_y-overflow),
                    color=color, fontsize=10, fontweight='bold', va='center', annotation_clip=False,
                    arrowprops=dict(arrowstyle='-', color=color, linewidth=.75))
    ticks = list(range(0, n, max(1, n//4)))
    ax.set_xticks(ticks, [datetime.fromtimestamp(rows[i]['open_ms']/1000, ZoneInfo('Europe/Istanbul')).strftime('%d/%m\n%H:%M') for i in ticks])
    ax.yaxis.set_major_formatter(mticker.ScalarFormatter(useOffset=False))
    ax.tick_params(colors=muted, labelsize=9)
    ax.grid(alpha=.12, color=muted)
    for spine in ax.spines.values():
        spine.set_color('#354253')
    ax.set_ylabel('USDT', color=muted, fontsize=10)
    fig.text(.08, .346, 'Mavi bant: kırılan bölge  ·  Noktalar: onaylı swing  ·  Sarı nokta: SL dayanağı', color=muted, fontsize=9)
    if not d['stop_anchor_in_view'] and d['stop_anchor_id']:
        fig.text(.08, .330, 'SL dayanağı bu 64 mumluk görünümün öncesinde; tam kayıtta mevcut.', color=yellow, fontsize=9)
    y = .315
    for label, key, color in [('GİRİŞ REFERANSI', 'entry', blue), ('STOP (SL) REFERANSI', 'stop', red), ('HEDEF (TP) REFERANSI', 'target', green)]:
        fig.text(.06, y, label+': '+p[key], color=color, fontsize=14, weight='bold'); y -= .032
    risk = d['net_r']
    metric = ('Hedef %'+format(decimal(risk['reward_pct']), '.2f')+'  ·  Risk %'+format(decimal(risk['risk_pct']), '.2f')+
              '  ·  Net '+format(decimal(risk['value']), '.2f')+' R') if risk['value'] is not None else 'R hesabı: doğrulanmadı'
    fig.text(.06, y-.004, metric, color=fg, fontsize=11); y -= .036
    # The first reason is the eligibility explanation; the rest remain in full JSON/text.
    short_reasons = [d['reasons'][0]]
    if record['grid']['status'] != 'SUPPLIED_PRICE_FILTER':
        short_reasons.append('Tick size doğrulanmadı. ≈ değerler yaklaşık ham analiz referansıdır.')
    if d['issues']:
        short_reasons.append('Yuvarlama/geometri sorunu kayıtta var; ilgili seviye gösterilmedi.')
    for reason in short_reasons:
        for line in wrap(reason, 96):
            fig.text(.06, y, line, color=yellow, fontsize=10); y -= .019
    fig.text(.06, .043, 'Referanslar emir/giriş değildir. %0,30 toplam masraf varsayımı; funding hariç.', color=muted, fontsize=9)
    fig.text(.06, .025, 'Kayıt: '+record['record_hash'][:12]+' · Ayrıntılı nedenler aynı JSON ve mesajda.', color=muted, fontsize=9)
    path = Path(path)
    fig.savefig(path, dpi=130, facecolor=bg, metadata={'RecordHash': record['record_hash'], 'Schema': record['schema']})
    plt.close(fig)
    return path
