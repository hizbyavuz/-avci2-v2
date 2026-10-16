"""Descriptive, signal-time feature comparisons; no promotion or rule changes."""
import json
import statistics
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent


def stats(rows):
    vals = [r['outcome']['model_net_return_pct'] for r in rows]
    directional = [r['outcome']['directional_return_pct'] for r in rows]
    risk = [r['outcome']['model_net_return_pct'] / r['signal']['geometry']['loss_with_cost_pct'] for r in rows]
    return dict(n=len(rows), positive=sum(x > 0 for x in vals),
        mean_net_pct=statistics.mean(vals), median_net_pct=statistics.median(vals),
        mean_net_r=statistics.mean(risk),
        endpoint_right=sum(x > 0 for x in directional),
        endpoint_wrong=sum(x < 0 for x in directional),
        mean_directional_endpoint_pct=statistics.mean(directional))


def run():
    raw = json.loads((HERE / 'matched_ready_180m.json').read_text())
    keys = [(r['assessment_id'], r['horizon']) for r in raw]
    assert len(keys) == len(set(keys)), 'Replay duplicate'
    assert all(r['horizon'] == 180 and r['signal']['stage'] == 'DELIVERED_READY' for r in raw)
    assert len({r['signal']['strategy_id'] for r in raw}) == 1
    rows = [r for r in raw if r['state'] == 'RESOLVED' and
            isinstance(r['outcome'].get('model_net_return_pct'), (float, int))]
    groups = defaultdict(list)
    for r in rows:
        p = r['signal']
        sign = 1 if p['direction'] == 'LONG' else -1
        expected = 'UP' if sign == 1 else 'DOWN'
        daily = p['context_directions'].get('1d')
        daily_label = 'same' if daily == expected else 'range' if daily == 'RANGE' else 'opposing' if daily in ('UP','DOWN') else 'missing'
        flow = p.get('hourly_flow', {})
        flow_label = 'missing' if flow.get('status') != 'OK' else 'aligned' if (
            sign * (flow['buy_share'] - .5) > 0 and sign * flow['price_return_pct'] > 0) else 'not_aligned'
        features = dict(model=p['model'], scenario=p['scenario'], daily=daily_label,
            flow=flow_label, reward_risk='at_least_one' if p['geometry']['net_reward_risk'] >= 1 else 'below_one')
        for key, value in features.items():
            groups[key + ':' + value].append(r)
    result = dict(scope='EXPLORATORY_POST_HOC_NO_HELD_OUT_VALIDATION',
        primary_horizon_minutes=180, total=stats(rows),
        excluded_exported_boundary=len(raw)-len(rows),
        data_unresolved_not_in_startup_export=2,
        groups={key:stats(value) for key, value in sorted(groups.items())},
        promotion_decision='NONE',
        notes=['Features are from the saved Telegram delivery, never a later chart.',
               'Raw direction and stop/target reference P&L are separate outcomes.',
               'R divides reference return by the saved cost-adjusted stop loss; it is not an executable portfolio.',
               'Short, correlated sample; overlapping feature groups are not independent experiments.',
               'High reward/risk subgroup has only 8 resolved records; no reliability claim.',
               'Current data has already been examined; a future rule needs a new untouched evaluation period.'])
    (HERE / 'matched_features.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    lines = ['# Canlı güncelleme doğrulaması ve ilk eşleştirme', '',
        '16 Ekim 2026, yaklaşık 17:53 Türkiye saati.', '',
        '**Ölçüm güncellemesi canlıya geçti. Dağıtım SUCCESS, yeni ölçüm çalışanı aktif. Süresi dolmuş ama sırada bekleyen ölçüm sayısı bütün sürelerde sıfıra indi. Veri eksikleri ve aynı dakika belirsizlikleri ayrı tutuluyor.**', '',
        'Güncel fiyat taraması yeniden başladı: 153 sembol değerlendirildi, 10 izleme adayı bulundu. Sinyal ve ayar kimlikleri değişmedi. Bu, sinyal performansının arttığı anlamına gelmez.', '',
        '## İlk mesaj bazında karşılaştırma', '',
        'Aynı sürümün üç saatlik sonucu çözümlenmiş 115 giriş mesajı kullanıldı. Başarı sütunu, hedef/stop veya süre sonunda varsayılan maliyet sonrası artıda kalmayı gösterir; yalnızca yönün doğru çıkması değildir.', '',
        '| Mesaj anındaki koşul | Ölçülen | Artıda sonuç | Ortalama net referans getiri |',
        '|---|---:|---:|---:|',
    ]
    labels = {'daily:same':'Günlük yönle aynı', 'daily:opposing':'Günlük yöne ters',
              'daily:range':'Günlük yön belirsiz', 'flow:aligned':'Saatlik akış ve fiyat destekliyor',
              'flow:not_aligned':'Saatlik akış ve fiyat birlikte desteklemiyor',
              'reward_risk:at_least_one':'Net hedef kazancı stop kaybından az değil',
              'reward_risk:below_one':'Net hedef kazancı stop kaybından küçük'}
    for key, label in labels.items():
        g = result['groups'][key]
        lines.append(f"| {label} | {g['n']} | {g['positive']} | %{g['mean_net_pct']:+.3f} |")
    lines += ['', 'Bu gruplar birbirinin üzerine biner; satırlar toplanmaz. Sadece günlük yönü veya akış desteğini zorunlu tutmanın sorunu çözdüğüne dair bu örneklemde kanıt yok. Kazanç/kayıp oranı >=1 grubu da yalnızca 8 örnek içeriyor ve sonucu pozitif değil. Hiçbirini yeni bir kural olarak canlıya almadık.', '',
        '## Kullanıcı için anlamı', '',
        'Mevcut motorun kararları henüz kazandıran bir yöntemi göstermiyor. Birkaç büyük kaybın küçük kazançları silebildiğini artık tek tek mesajlarla inceleyebiliyoruz. İyileştirme için yalnızca daha fazla gösterge eklemek yeterli görünmüyor. Girişin zamanlaması, hedef/kayıp mesafesi ve süre birlikte değerlendirilmek zorunda.', '',
        '## Kapsam', '',
        'Gerçek emir veya hesap getirisi ölçülmedi; maliyet varsayımı %0,30. İlk dakikadaki olay sırası belirsiz olan 2 kayıt ve ilk açılış dışa aktarımına girmeyen 2 veri eksikliği bu ana karşılaştırmaya dahil değil. Bekleyen süreler başarısız sayılmadı. Aynı coin ve zaman çevresindeki kayıtlar bağımsız örnekler kabul edilmemeli.', '',
        'Tekrarlama: `python audits/delivered_setup_review_20261016/review_features.py`. Canlı kanıtlar `live_verification.json`, mesaj kayıtları `matched_ready_180m.json`, karşılaştırma `matched_features.json` içinde.', '']
    (HERE / 'CANLI_SONUC.md').write_text('\n'.join(lines))
    print(json.dumps(dict(measured=len(rows), groups=len(groups), promotion='NONE')))


if __name__ == '__main__':
    run()
