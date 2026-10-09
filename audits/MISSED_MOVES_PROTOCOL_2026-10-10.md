# Bağımsız Büyük Hareket / Kaçırma Denetimi (üretim dışı)

**Durum:** Veri erişimi bekleniyor; test sonucu henüz yok. **Bu dal production değildir.** Kod/eşik/Telegram/volume değişikliği yapılmaz.

## Önceden sabitlenmiş protokol (2026-10-10)

1. Evren: Olay tarihinde işlem gören bütün hedef Binance USDT perpetual sembolleri; analistin 85/24 coinlik listesi olay kaynağı olarak kullanılmaz. Delist ve yeni listeler tarihsel evrene göre dahil edilir.
2. Kaynak: 1 dakikalık kapanmış, UTC damgalı mumlar. Kesinti/eksik mumlar **DATA_FAILURE**; sıfır hareket olarak sayılmaz. Tarihsel sembol/kontrat eşlemesi ayrıca doğrulanır.
3. Olay: 60 dakikalık pencerenin başlangıç kapanışından sonraki 60 dakika içinde en az +%8 veya -%8 ilk erişim. **t0**, bu pencerenin başlangıç kapanış zamanıdır; t0 seçimi gelecekteki fiyat bilgisi kullanılarak sinyal üretiminde kullanılmaz, yalnız sonuç etiketidir. Aynı sembol+yön için örtüşen 60 dakikalık pencereler tek olaya birleştirilir; en erken t0 korunur. Karşıt yön ayrı olaydır.
4. Anlık durum: t0, t0+5, t0+15, t0+30 için olay-zamanında bilinen bilgi ile: UNIVERSE_ABSENT, NOT_SHORTLISTED, SHORTLIST_NO_TRADE (somut hard blocker), WATCH_EARLY_NOT_TRIGGERED, TRIGGERED_NOT_SENT, SENT. Tarama gerçekleşmemişse NOT_SCANNED; kayıt yoksa UNKNOWN_LOG_MISSING. Bu iki durum yanlışlıkla NOT_SHORTLISTED sayılmaz.
5. SENT için Telegram'ın kabul ettiği zaman/fiyat ve olayın o ana kadar gerçekleşen yüzdesi; fiyat bazlı ölçüm, gönderim gecikmesi ve geleceğe bakma ayrı gösterilir. Sinyal yönü ile olay yönü karşılaştırılır.
6. Kontrol: Aynı tarih/saat, benzer likidite ve coin yaşı tabakasından, sonraki 60 dakikada ±%8 yapmayan coinler; eşit sayıda rastgele/near-miss örnek, sabit seed=20261010. Kontrollerde aynı aşama kovaları ölçülür.
7. Offline shortlist replay: yalnız t0 itibarıyla kapanmış 5m/15m mumları, o andaki 24s hacim, Binance Spot üyeliği ve external-only doğrulama durumu kullanılır. Mevcut kodun **önce actionable/research ayrımı, sonra rank, sonra max24** kuralı bire bir uygulanır. Tarihsel meta yoksa REPLAY_UNAVAILABLE, güncel metadata geriye taşınmaz.
8. Rapor: Olay ve kontrol sayısı, 7 gün/72 saat kapsamı, eksik veri, aşama geçiş tablosu, kaçırma sebepleri, t0 sonrası gecikme, sinyal öncesi hareket, sonuç ve belirsizlik. 72 saat betimleyicidir; kanıt iddiası yok.

## Veri güvenliği / çalışma yöntemi

- Canlı Railway volume'undan ajan tarafından okuma/yazma yok. Kullanıcı veya yetkili işlemci **ayrı bir salt-okunur SQLite snapshot** hazırlar; WAL ile tutarlı yedek ve gizli anahtar/Telegram chat bilgileri çıkarılmış halde paylaşılır.
- Snapshot olmadan olay-bazlı üretim geçmişi **ölçülemedi** olarak raporlanır. Mevcut OPPORTUNITY_FUNNEL yalnız preselected sembolleri kapsar; UNIVERSE_ABSENT ve ön-elemede hata yaşayan coinleri tek başına göstermez.
- Bağımsız tam perp 1m mum geçmişi de gereklidir. Bu iki veri kaynağı olmadan gerçek recall/kaçırma oranı üretilmez.
- Üretim kuralları V3.1 dondurulmuş kalır; bu dosya yalnız ölçüm sözleşmesidir. Sinyal kalitesi kanıtlanmadan otomatik emir açılmaz.

## Bilinen mevcut gözlem

Son denetlenen canlı taramalarda 30 preselected, 24 deep; OPPORTUNITY_FUNNEL 23 NO_TRADE / 1 WAIT / 6 NOT_SELECTED veya 24 NO_TRADE / 6 NOT_SELECTED. CONFIRMED_TRADE_SUMMARY completed=0. Bunlar kaçırma oranı değildir.
