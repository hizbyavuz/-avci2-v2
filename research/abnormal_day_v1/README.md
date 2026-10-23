# Anormal Gün Devamı

Tamamlanan deney: **BAŞARISIZ**. Dönem 2020-01-01–2026-10-22 UTC.
Limitli 24h: 1.408 ölçülen işlem; ortalama net −%0,27941; kontrol +%0,44852.
Son 12 ay ortalama net −%0,38981. LONG ve SHORT ayrı ayrı da başarısız.
Eksik veri olayları raporda belirtilmiştir; sermaye eğrisi tam doğrulanmadı.
Canlı kapalıdır.

- [Rapor](results/report.md)
- [İşlemler (CSV, gzip)](results/trades.csv.gz)
- [Sermaye eğrisi](results/equity_curve.png)
- [Doğrulama](results/verification.json)

Turtle'dan bağımsız sabit kural deneyi: günlük z skoru, 24 saat elde tutma,
2×ATR14 acil stop. Eşik/parametre denemesi yok. 48 saat ve stopsuz ölçümler
önceden belirtilmiş ek bilgilerdir; ana kural seçmek için kullanılmaz.

```bash
pip install -r requirements.trend-breakout.txt
python -m unittest test_abnormal_day -v
python abnormal_day_data.py --source /optional/existing-daily-cache --data /new/abnormal-data --archive-cache /shared/archive-cache
python abnormal_day_backtest.py --data /new/abnormal-data --archive-cache /shared/archive-cache
```

Var olan önbellek yoksa günlük arşiv kataloğu indirilir. Eski daily/prepared
dosyaları yalnız okunur; yeni deneyin günlük mum tamamlama kayıtları, evreni,
hazırlanmış verisi ve sonuçları ayrı dizindedir. Ham resmi ZIP önbelleği
paylaşılabilir. Resmi arşiv SHA256 doğrulaması aynı indirme sınıfında yapılır.

`abnormal_day_protocol.json` tüm uygulama yorumlarını sonuçtan önce sabitler.
`sealed_run.json` kaynak/protokol/veri hashlerini tutar. Tamamlanan deney
yeniden çalıştırılamaz. Teknik kesintide `--resume` yalnız eşleşen hashlerle
aynı gözlem kayıtlarını kullanarak devam eder; yeni parametre denemesi değildir.

Kontrol havuzu sonuçlar hesaplanmadan dondurulur. Aynı coin ve aynı sinyal
takvim ayındaki |z|<1 günlerinden bir örnek, sabit tohum/hash ile seçilir.
Kontrol karşılaştırması geriye dönük eşleştirmedir; canlı karar fonksiyonuna
gelecek bilgi vermez. Havuz günlerinin her birinde z ve ATR yalnız o gün
kapanmış veriyle hesaplanır. Normal gün tekrar seçilebilir; bu yüzden
karşılaştırmaların birbirinden bağımsız olduğu veya istatistiksel anlamlılık
kanıtlandığı iddia edilmez. Kontrol grubuna ayrı portföy kapasitesi uygulanmaz;
ana hesapta alınan her sinyalin kendi karşılaştırması gösterilir.

Funding gerçek oranlardan, yön işaretiyle sabit giriş nominalinde hesaplanır.
Saat içi stop saniyesi bilinmez; funding sırası belirsizse sonuç doğrulanmadı
olur. Eksik veri uydurulmaz. UTC günlük sınır sermaye eğrisi gün içi maksimum
düşüşü göstermez. Marjin/liquidation modellenmez.

Canlı workflow schedulesiz ve onay kapalıdır. Geçmiş rapor GEÇTİ ve açık
kullanıcı onayı birlikte olmadan çalışmaz. Onay sonrası program 00:07 UTC /
03:07 Türkiye'dir. GitHub zamanlaması kesin saniye garantisi vermez; canlı
paper çıkışı gerçek fiyat/zaman ve gecikmeyle kaydedilir. Aynı saate erken
başlayan iş önceki günün 24 saat vadesi için en fazla bir saat bekleyebilir.
1m verisi canlıda yalnız giriş/çıkışın kısmi saatini ölçmek içindir; sinyal,
ATR, stop ve limit fonksiyonları geçmiş testle aynıdır. Otomatik emir yoktur.

Yeni SQLite `abnormal-day-paper-state` dalındaki ayrı dosyada tutulur.
Telegram outbox önce kalıcı yazılır; yanıtı belirsiz gönderim otomatik
tekrarlanmaz. İlk kurulum çalışma anındaki son kapalı günü işler, sonraki
çalışmalar aradaki kapanışları sırayla işler.

Kaynak çalışma: Caporale ve Plastun (2020),
https://doi.org/10.1007/s11408-020-00357-1. Bu deney çalışmanın birebir
replikasyonu değildir; kullanıcı tarafından sabitlenmiş perpetual kuralıdır.
