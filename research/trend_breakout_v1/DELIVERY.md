# Teslim — Trend Kırılım Motoru v1

**Karar: BAŞARISIZ. Yeni canlı tarama ve Telegram gönderimi açılmadı.**

2023-10-23 00:00 UTC–2026-10-23 00:00 UTC aralığında, ay başlarında geçmiş
30 günlük hacimle seçilen ilk 50 parite incelendi. Aylık listelerde toplam
325 farklı perpetual yer aldı. Kullanılan fiyat verisinin tamamı futures;
spot fallback sayısı 0. Kurallar değiştirilmedi, geliştirme varyantı 0.

Kilitli test başlangıcı 2025-11-28 04:00 UTC, Türkiye saatiyle 07:00.
Sınırların son 168 sinyal saati sonuçtan bağımsız olarak dışlandı.

| Kilitli test | Sınırlı (5 toplam / 3 aynı yön) | Sınırsız |
|---|---:|---:|
| İşlem | 1.703 | 3.561 |
| Ortalama net R | −0,1350 | −0,1406 |
| Toplam net R | −229,8330 | −500,7212 |
| Maksimum kapanmış işlem düşüşü | 249,4279 R | 551,7227 R |
| En uzun kayıp serisi | 16 | 37 |
| Hedefe önce ulaşma | %35,1145 | %34,5128 |
| 24 saat yön isabeti | %50,3817 | %49,0593 |
| %0,60 masrafta ortalama R | −0,3170 | −0,3145 |
| En iyi üç coin hariç ortalama R | −0,1546 | −0,1582 |

İşlem sayısı yeterli; üç pozitif ortalama koşulu da sağlanmadı. Sonuçları
iyileştirmek için hedef, stop veya eşik ayarı yapılmadı. R, her işlem için
başlangıç risk birimidir; tablodaki toplamlar hesap yüzdesi değildir.

## Dosyalar

- `results/report.md`: bütün dönem, yön, masraf, aylık sonuç ve veri sınırları.
- `results/trades.csv`: 17.380 portföy kaydı. Aynı sinyal sınırlı ve sınırsız
  senaryoda ayrı bulunabilir; `portfolio` sütunuyla ayrılır. Benzersiz bağımsız
  işlem sayısı gibi toplanmamalıdır.
- `results/skipped_and_rejected.csv`: işlem planı ve portföy tarafından elenenler.
- `results/summary.json`, `monthly.csv`: sayısal özetler.
- `results/universe.json`: ay ay seçilen 50 parite ve seçimde kullanılan zamanlar.
- `results/locked_test_seal.json`: kurallar, kod ve hazırlanmış veri SHA256'ları.
- `results/downloads.jsonl.gz`: resmi arşiv dosyası/checksum indirme izleri.
- `results/verification.json`: CSV mutabakatı, risk sınırı ve kod hash kontrolleri.
- `example_chart.png`: gerçek geliştirme verisiyle çizim örneği; güncel öneri değil.

CSV dosyalarının `.gz` kopyaları aynı içeriğin sıkıştırılmış sürümüdür.
Ham mum arşivleri eski motor veritabanlarından ayrı tutuldu; repo içine
milyarlarca bayt ham veri eklenmedi. Arşiv kaynakları ve hash'ler kayıtlıdır.

## Kod ve kontroller

Sinyal ve plan: `trend_breakout_engine.py`; arşiv/evren:
`trend_breakout_data.py`; geçmiş test: `trend_breakout_backtest.py`;
paper/Telegram: `trend_breakout_live.py`. İki yürütücü aynı `evaluate()` ve
`make_plan()` fonksiyonlarını çağırır. Otomatik emir kodu yoktur.

35 birim testi geçti. Ayrıca CSV üzerinden toplamlar, bütün 2R hedefleri,
2,5 ATR sınırı, aynı coin tekrarı ve portföy limitleri doğrulandı.
GitHub CI: https://github.com/hizbyavuz/-avci2-v2/actions/runs/39992649698

Canlı workflow ayrı ve schedulesizdir. Kullanıcı onay dosyası `false` kalır;
rapor hash'i ve `TREND_BREAKOUT_LIVE_ENABLED=true` olmadan gönderemez.
Saatlik `3 * * * *` şablonu `research` klasöründedir, çalıştırılmaz.
Haftalık özet, SQLite kayıt, kaçan saatleri işleme ve çizimli mesaj kodu
hazırdır; gerçek Telegram teslimi ve üretim çalışması bu görevde doğrulanmadı.

## Eski motorların durumu

GitHub'da 25 eski motor/Telegram workflow'u `disabled_manually` olarak
doğrulandı. Kaynak dosyaları, frozen config'ler ve eski veritabanları değiştirilmedi.
Durdurma çalışması: https://github.com/hizbyavuz/-avci2-v2/actions/runs/39987860500

**Railway durdurması tamamlanmadı.** Son kontrolde `long-short-motor` ve
`temporary-futures-history-readonly-20261013` hâlâ online. `gate-trader` ve
iki geçici Binance probe offline. İki online servis için veriyi silmeyen,
hemen çıkış yapan başlangıç komutları hazırlanmış durumda; patch
`b5be37f3-7222-4214-9d42-21e9395832d7`, 3 bekleyen alan değişikliği.

Uygulama çağrısı araç tarafından şu cevapla iptal edildi:
“Cancelled — the user did not approve this action. No changes were made.”
Bu yüzden tüm motorlar kapandı iddiası yapılamaz. Bekleyen değişiklikler
buradan incelenip uygulanabilir:
https://railway.com/project/ba2b84a7-22c6-4703-aefc-c6f95393c1b6?environmentId=5fc78d8b-4f54-4b50-867d-c544cbd74aaa

## Açık sınırlamalar

Tarihsel ürün sınıflandırması ayrı exchangeInfo arşiviyle tam doğrulanmadı.
Funding gerçek oranların işaretli toplamıdır; değişen mark-price nominali
modellenmedi. Yayınlanmamış aylar için %0,01/8 saat gider varsayıldı
(kilitli testte sınırlı portföyde 74, sınırsızda 182 işlem).
Maksimum düşüş kapanan işlemlerden hesaplanır; açık pozisyonların anlık
zararını kapsamaz. Canlı ve testin sonlu indikatör başlangıç tarihleri
farklı olabilir; aynı girdi geçmişinde aynı fonksiyon aynı sonucu verir.
Bu sınırlamalar negatif sonucu pozitif olarak sunmak için kullanılmaz.
