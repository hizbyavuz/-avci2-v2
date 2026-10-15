"""Presentation only: deliberately excluded from decision identity."""
from long_short_chart_setups import geometry

def message(p, stage=None):
    from long_short_chart_first import CONFIG
    from datetime import datetime, timezone
    from long_short_display import local_time
    stage=stage or p['stage'];side=p['direction'];up=side=='LONG'
    marker='🟢' if up else '🔴'
    move='YÜKSELİŞ' if up else 'DÜŞÜŞ'
    relation='üstünde' if up else 'altında'
    stamp='🕒 '+local_time(datetime.fromtimestamp(p['observed_ms']/1000,timezone.utc).isoformat())+' (Türkiye)'
    if stage in ('BROKEN','EXPIRED','OBSTACLE_REACHED'):
        why={'BROKEN':'🛑 Fikrin bozulacağı seviye aşıldı.',
             'EXPIRED':('⌛ Aradaki fiyat hareketi kesin doğrulanamadı; bu takip kapatıldı.' if p.get('path_guard',{}).get('status')=='AMBIGUOUS' else '⌛ Sistem kuralı güncellendi; eski takip kapatıldı.' if p.get('expiry_reason')=='STRATEGY_CHANGED' else '⌛ Eski giriş koşulu artık geçerli değil; yeni fırsat bekleniyor.'),
             'OBSTACLE_REACHED':'🎯 Fiyatın zorlanabileceği ilk seviyeye ulaşıldı; bu takip bitti.'}[stage]
        return '\n'.join([f'{marker} {p["symbol"]} · {side} TAKİBİ BİTTİ',why,
                          f'💰 Güncel fiyat: {p["price"]:.10g}',stamp])
    title='GİRİŞ KOŞULU OLUŞTU' if stage=='READY' else 'İÇİN İZLE'
    rows=[f'{marker} {p["symbol"]} · {move} {title} ({side})',
          '✅ Giriş koşulu oluştu; fiyat aralığını kontrol et.' if stage=='READY' else '⏳ Henüz giriş koşulu oluşmadı.']
    if p.get('scenario')=='REVERSAL_ATTEMPT':
        rows.append('⚠️ ANA YÖNE TERS: Büyük yön ters; bu kısa vadeli bir dönüş denemesi.')
    rows.extend(['',f'💰 Şu an fiyat: {p["price"]:.10g}'])
    if stage=='READY':
        lo,hi=sorted((p['trigger'],p['entry_limit']))
        rows.extend([f'🔔 5 dk mum {p["trigger"]:.10g} {relation} kapandı; fiyat seviyeyi koruyor.',
                     f'📍 Giriş için değerlendirilen aralık: {lo:.10g}–{hi:.10g}'])
    else:
        rows.append(f'🔔 Beklenen: 5 dk mum {p["trigger"]:.10g} {relation} kapansın, fiyat bu seviyeyi korusun.')
    rows.extend(['',f'🎯 {"Yükselişin" if up else "Düşüşün"} zorlanabileceği ilk yer: {p["obstacle"]:.10g}',
                 f'🛑 {p["stop"]:.10g} {"altına düşerse" if up else "üstüne çıkarsa"} fikir bozulur.'])
    if p.get('intermediate_level') is not None and (1 if up else -1)*(p['intermediate_level']-p['price'])>0:
        rows.append(f'⚠️ Arada {p["intermediate_level"]:.10g} seviyesi var; fiyat burada takılabilir.')
    entry=p['price'] if stage=='READY' else p['trigger']
    g=geometry(side,entry,p['stop'],p.get('obstacle'),CONFIG['round_trip_cost_pct'])
    if g.get('valid'):
        anchor='Şu anki fiyattan' if stage=='READY' else 'Beklenen girişten'
        rows.append(f'📏 {anchor}: {"yukarı" if up else "aşağı"} alan %{g["gross_room_pct"]:.2f} · Stopa uzaklık %{g["invalidation_pct"]:.2f} (masraf hariç)')
    ctx=p['context'];labels={'UP':'yükseliş ↑','DOWN':'düşüş ↓','RANGE':'belirsiz ↔'}
    rows.extend(['',f'🧭 1 saat: {labels[ctx["1h"]["direction"]]} · 4 saat: {labels[ctx["4h"]["direction"]]}'])
    vol=p.get('volume_15m_multiple')
    if vol is not None:rows.append(f'📊 Son 15 dk işlem yoğunluğu: normalin {vol:.2f} katı')
    flow=p.get('hourly_flow') or {}
    if flow.get('status')=='OK':
        rows.append(f'🛒 Son 1 saat işlem payı: Alış %{flow["buy_share"]*100:.0f} · Satış %{(1-flow["buy_share"])*100:.0f} · Fiyat %{flow["price_return_pct"]:+.2f}')
        sign=1 if up else -1
        if sign*(flow['buy_share']-.5)<=0 or sign*flow['price_return_pct']<=0:
            rows.append('⚠️ Son saatte alış/satış baskısı ile fiyat bu yönü birlikte desteklemiyor.')
        elif (flow.get('previous_30m_buy_share') is not None and flow.get('recent_30m_buy_share') is not None
              and sign*(flow['recent_30m_buy_share']-flow['previous_30m_buy_share'])<0):
            rows.append('⚠️ Son yarım saatte bu yöndeki işlem payı zayıfladı.')
    else:rows.append('🛒 Son 1 saat alış/satış bilgisi: veri eksik')
    opposite='DOWN' if up else 'UP'
    if ctx['1d']['direction']==opposite:
        rows.append('⚠️ Günlük yön hâlâ '+('aşağı.' if up else 'yukarı.'))
    if ctx['15m']['direction']==opposite:
        rows.append('⚠️ Son 15 dakikalık yön de bu fikre ters.')
    rows.append(stamp)
    return '\n'.join(rows)
