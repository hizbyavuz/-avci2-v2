"""Reproduce a bounded review of saved production logs; never change signals."""
import json
import statistics
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAMES = {
    'COMPRESSION_BREAKOUT': 'Sıkışmadan kırılım',
    'CONFIRMED_LEVEL_WATCH': 'Seviye geçişi',
    'PULLBACK_CONTINUATION': 'Geri çekilme sonrası devam',
    'SWING_BREAKOUT': 'Tepe/dip kırılımı',
}


def aggregate(groups):
    """Weight subgroup means by sample size, never by number of subgroups."""
    totals = {}
    for row in groups:
        if row['stage'] != 'DELIVERED_READY':
            continue
        key = (row['model'], row['horizon'])
        out = totals.setdefault(key, dict(model=key[0], horizon=key[1],
            measured=0, positive=0, unresolved=0, net_sum_pct=0.0))
        n = row['model_trades']
        assert row['count'] == n + row['unresolved'], row
        assert 0 <= row['positive'] <= n
        assert (row['mean_net_pct'] is not None) == (n > 0)
        out['measured'] += n
        out['positive'] += row['positive']
        out['unresolved'] += row['unresolved']
        out['net_sum_pct'] += n * (row['mean_net_pct'] or 0)
    for row in totals.values():
        n = row['measured']
        row['mean_net_pct'] = row['net_sum_pct'] / n if n else None
    return sorted(totals.values(), key=lambda r: (r['horizon'], r['model']))


def review(data):
    snapshot = data['outcome_snapshot']
    sid = snapshot['strategy_id']
    deliveries = {}
    for p in data['deliveries']:
        assert p['strategy_id'] == sid and p['stage'] == 'DELIVERED_READY'
        key = (sid, p['episode_id'], p['stage'])
        if key in deliveries:
            assert p == deliveries[key], 'Conflicting duplicate delivery'
        deliveries[key] = p
    rows = list(deliveries.values())
    valid = [r for r in rows if r['geometry'].get('valid')]
    invalid = [r for r in rows if not r['geometry'].get('valid')]
    for c in data['coverage_snapshot']:
        assert c['strategy_id'] == sid
        assert c['records'] == sum(c[k] for k in ('resolved','unresolved','pending','missing'))
    quality = dict(delivered=len(rows), valid_geometry=len(valid),
        invalid_geometry=len(invalid),
        net_reward_below_loss=sum(r['geometry']['net_reward_risk'] < 1 for r in valid),
        binary_break_even_above_75=sum(r['geometry']['break_even_win_rate_pct'] > 75 for r in valid),
        binary_break_even_above_90=sum(r['geometry']['break_even_win_rate_pct'] > 90 for r in valid),
        median_net_reward_risk=statistics.median(r['geometry']['net_reward_risk'] for r in valid),
        median_binary_break_even_pct=statistics.median(r['geometry']['break_even_win_rate_pct'] for r in valid),
        invalid_examples=invalid,
        scope='SIGNAL_TIME_PAYOFF_GEOMETRY_NOT_REALIZED_RETURNS; BINARY_TARGET_STOP_IGNORES_TIMEOUT')
    return dict(strategy_id=sid, outcome_timestamp=snapshot['timestamp'],
        groups=aggregate(snapshot['groups']), payoff_quality=quality,
        coverage=data['coverage_snapshot'], promoted_models=[], edge_proven=False,
        status='MEASUREMENT_RELEASE_BLOCKED; DESCRIPTIVE_EVIDENCE_ONLY',
        limitations=[
            'Aggregate results are not linked to individual delivery rows; no feature/outcome causal attribution.',
            'Returns use recorded levels and assumed 0.30% round-trip cost, not actual fills, spread or funding.',
            'Equal-notional average signal returns are not portfolio returns or equal-risk position sizing results.',
            'Horizons have different mature samples; their averages are not a paired holding-time experiment.',
            'Same-symbol and concurrent signals are correlated; this is a short two-UTC-day sample.',
            'Missing, pending and ambiguous records are retained and are not counted as ordinary wins/losses.',
            'Model group reports use a rolling 24h window; coverage is a separate 30-day delivery inventory.',
            'No thresholds were optimized; no model passed a held-out validation or was promoted.'
        ])


def render(data, result):
    q = result['payoff_quality']
    stamp = datetime.fromisoformat(result['outcome_timestamp'][:26] + '+00:00').astimezone(timezone(timedelta(hours=3)))
    lines = [
        '# Gönderilen önerilerin ilk karşılaştırması', '',
        f'Ölçüm raporu: {stamp:%d.%m.%Y %H:%M:%S} Türkiye saati.', '',
        '**Sonuç: İncelenen dört öneri türünün hiçbirinin üç saatlik ortalama referans getirisi pozitif değil. Henüz bir türü başarılı ilan edip Telegram önerisi olarak öne çıkarmıyoruz.**', '',
        '## Üç saatlik ana değerlendirme', '',
        'Hedef veya stop daha önce görülürse o seviye, aksi halde süre sonu fiyatı kullanılıyor. Hesapta %0,30 gidiş-dönüş maliyet varsayımı var. Yüzdeler kaldıraçsız işlem büyüklüğüne göre; gerçek hesap kazancı değil.', '',
        '| Öneri türü | Sonucu ölçülen | Artıda sonuç | Ortalama net referans getiri | Belirsiz/eksik sonuç kaydı |',
        '|---|---:|---:|---:|---:|',
    ]
    for r in result['groups']:
        if r['horizon'] == 180:
            lines.append(f"| {NAMES[r['model']]} | {r['measured']} | {r['positive']} | %{r['mean_net_pct']:+.3f} | {r['unresolved']} |")
    lines += ['', '## Mesaj anında görülen somut sorun', '',
        f"135 giriş bildirimi incelendi. {q['valid_geometry']} tanesinde hesap geometrisi geçerli; {q['invalid_geometry']} tanesinde bildirim onayı anındaki fiyatla hedef mesafesi varsayılan maliyeti karşılamıyor.", '',
        f"Geçerli {q['valid_geometry']} planın {q['net_reward_below_loss']} tanesinde net hedef kazancı, stopta hesaplanan kayıptan küçük. {q['binary_break_even_above_75']} tanesinde yalnızca hedef/stop ile kapanıldığı varsayılırsa başa baş için %75'in üzerinde hedefe ulaşma oranı gerekiyor; {q['binary_break_even_above_90']} tanesinde bu oran %90'ın üzerinde.", '',
        'Bu oranlar gerçekleşmiş başarı oranı değil, hedef/stop mesafelerinin matematiğidir. Süre sonunda kapanışlar ve değişken işlem büyüklüğü farklı sonuç verir.', '',
        'Örnek: MAGICUSDT SHORT, 15 Ekim 23:00 Türkiye. Kaydedilen net hedef alanı yaklaşık %0,113; masraf dahil stop kaybı %1,740. Sadece bu iki çıkış kullanılsa bir stop yaklaşık 15,4 hedef kazancını siler. Bu, bu işlemin gerçekte stop olduğu iddiası değildir.', '',
        "Sıkışma sonrası devam LONG alt grubunda üç saatlik 13 sonucun 11'i artıda olmasına rağmen ortalama net sonuç yaklaşık -%0,308. Bu küçük alt grup, yüksek artıda kapanış sayısının tek başına olumlu beklenti sağlamadığını gösteriyor; bir strateji seçme gerekçesi değil.", '',
        '## Ölçümde kalan boşluk', '',
        '| Mesaj türü | Süre | Kayıt | Ölçülmüş | Belirsiz/veri sorunu | Süresi dolmamış | Süresi dolmuş, sonuç yok |',
        '|---|---:|---:|---:|---:|---:|---:|',
    ]
    for c in sorted(result['coverage'], key=lambda c:(c['stage'],c['horizon'])):
        lines.append(f"| {'Giriş oluştu' if c['stage']=='DELIVERED_READY' else 'İzle'} | {c['horizon']} dk | {c['records']} | {c['resolved']} | {c['unresolved']} | {c['pending']} | {c['missing']} |")
    lines += ['', 'Bu envanter 16 Ekim 17:43 Türkiye saati civarındaki ayrı kayıt kontrolüdür. Üstteki model tablosuyla farklı rapor anına aittir. İzle mesajlarına varsayımsal işlem açılmış gibi kazanç atanmıyor.', '',
        '## Uygulanan ve engellenen işlem', '',
        'Ölçüm kuyruğu düzeltmesi GitHub üzerinde hazır. Railway dağıtım aracı, kullanıcının yazılı onayına rağmen iki girişimde de `Cancelled — the user did not approve this action. No changes were made.` döndürdü. Yeni sürüm canlıya alınmadı. Bu engeli aşmak için farklı bir dağıtım yolu denenmedi.', '',
        f"Canlı commit: `{data['source']['live_commit']}`.",
        f"Hazır ölçüm commit'i: `{data['source']['prepared_measurement_commit']}`.", '',
        'Sinyal kuralları, hedef/stop seviyeleri, maliyet varsayımı veya Telegram mesaj politikası bu incelemeyle değiştirilmedi.', '',
        '## Sonraki kararın koşulları', '',
        '1. Hazır ölçüm sürümü dağıtılınca önce SUCCESS ve sonuç kuyruğunun gerçekten ilerlediği doğrulanacak.',
        '2. Tek tek mesaj sonuçları geldiğinde aynı sürüm, model ve sürede; günlük/4 saatlik yön, akış desteği ve mevcut kazanç/kayıp oranı ile sonuçlar eşleştirilecek. Eksik veriden ortak özellik uydurulmayacak.',
        '3. Eski eşik olan net kazanç/kayıp >=1 ayrı gözlemsel karşılaştırma olarak incelenebilir. Bu eşik tek başına kârlılık garantisi değildir; hedef küçültülmeyecek veya stop sonradan değiştirilmeyecek.',
        '4. Yeni bir kural adayı açıkça sabitlendikten sonra, henüz görülmemiş sonraki dönemde mevcut kuralla aynı fırsatlarda karşılaştırılacak. Daha önce incelenmiş mesajlar doğrulama dönemi sayılmayacak.',
        '5. Ortalama getiri yanında eşit riskle sonuç, kayıp büyüklüğü, veri kapsamı ve aynı coin/zaman bağımlılığı incelenecek. Mevcut toplu rapordan bunların tümü hesaplanamıyor.', '',
        '## Tekrarlanabilirlik', '',
        '`python audits/delivered_setup_review_20261016/analyze.py` komutu kaydedilmiş girdiyle aynı raporu üretir. Kaynak, Railway loglarının `CHART_FIRST_OUTCOMES`, `CHART_RELIABILITY_COHORT` ve `CHART_FIRST_DELIVERY` kayıtlarıdır. `input.json` veri anını ve ham kayıtları; `report.json` hesaplanan karşılaştırmayı içerir.', '',
    ]
    return '\n'.join(lines)


if __name__ == '__main__':
    data = json.loads((HERE / 'input.json').read_text())
    result = review(data)
    (HERE / 'report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    (HERE / 'SONUC.md').write_text(render(data, result))
    print(json.dumps(dict(groups=len(result['groups']), deliveries=result['payoff_quality']['delivered'],
        promoted_models=result['promoted_models'], status=result['status']), ensure_ascii=False))
