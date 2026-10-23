"""Shared human-facing labels for the exact saved plan; no new trading logic."""

def card(p):
    f=lambda v: f'{v:.8g}' if v is not None else 'Bilinmiyor'
    g=p['payoff']
    reasons={'LOW_NET_REWARD_RISK':'Hedef alanı, stop riskine göre yetersiz.',
             'COST_EXCEEDS_ROOM':'Hedef alanı varsayılan masrafı karşılamıyor.',
             'FIRST_OBSTACLE_UNKNOWN':'İlk hedef belirlenemedi.',
             'TARGET_WRONG_SIDE':'Hedef giriş referansının gerisinde.',
             'STOP_WRONG_SIDE':'Stop giriş referansına göre geçersiz.'}
    eligible=p['plan_eligible']
    status=('KOŞULLU PLAN' if eligible else 'GEÇ KALINDI' if p['stage']=='LATE' else
            'İŞLEM UYGUN DEĞİL' if not g['valid'] else 'BEKLE · GİRİŞ TEYİDİ YOK')
    relation='üstünde' if p['direction']=='LONG' else 'altında'
    condition=(f"{p['interval']} kapanışı {f(p['trigger'])} {relation} bekleniyor." if p['stage']=='WATCH' else
               'Geri dönüş ve yönünde kapanış görüldü.' if p['condition']=='RETEST_HELD' else
               'Kırılım kapanışı görüldü; geri dönüş teyidi yok.')
    reason=('Motorun risk/mesafe koşulları sağlandı; kazanç garantisi değil.' if eligible else
            'Fiyat seviyeden uzaklaştı.' if p['stage']=='LATE' else
            reasons.get(g['reason'],'Giriş koşulu henüz tamamlanmadı.'))
    return dict(status=status,reason=reason,condition=condition,eligible=eligible,
                entry=p['entry_reference'] if eligible else None,
                stop=p['stop'],target=p['target'],trigger=p['trigger'],last=p['reference_price'],
                entry_label='GİRİŞ REFERANSI' if eligible else 'GİRİŞ YOK',
                stop_label='SL · STOP' if eligible else 'GEÇERSİZLİK REFERANSI',
                target_label='TP · İLK HEDEF' if eligible else 'İLK ENGEL · TP ONAYLI DEĞİL')
