# Dört filtre araştırması — 27 Ekim 2026

**Dört hipotezin hiçbiri, LONG veya SHORT tarafında önceden belirlenen geçme şartlarını sağlamadı.** Bazı ortalamalar iyileşti; hiçbir filtre 1,5 kat maliyette pozitif günlük ortalama net R üretemedi. Eşik araması veya filtreleri birlikte kullanma denemesi yapılmadı.

Binance arşivinde Ekim 2025–Temmuz 2026 sinyal mumları, 650 dönem içi sözleşme, 94.395 strateji adayı ve 72.078 kapanmış simülasyon işlemi incelendi. Bunlar Telegram üzerinden gönderilmiş veya gerçek parayla yapılmış işlemler değildir.

## Net R sonuçları

R, her işlemin başlangıçtaki planlanan kaybına göre normalize edilen maliyet sonrası sonucudur. Ana ölçü önce her UTC giriş günündeki kapanmış işlemlerin ortalamasını, sonra bu günlerin eşit ağırlıklı ortalamasını alır. Yoğun günler daha ağır sayılmaz.

| Deneme | Yön | Kapanmış işlem | Giriş günü | Normal maliyet | 1,5× maliyet | En iyi 3 coin çıkarılınca, 1,5× | Karar |
|---|---|---:|---:|---:|---:|---:|---|
| Mevcut strateji | LONG | 25062 | 304 | -0.217477 R | -0.277436 R | — | Referans |
| Mevcut strateji | SHORT | 47016 | 305 | -0.082684 R | -0.150335 R | — | Referans |
| Taker akışı | LONG | 12960 | 303 | -0.242077 R | -0.303539 R | -0.308437 R | Geçmedi |
| Taker akışı | SHORT | 27329 | 305 | -0.078137 R | -0.144792 R | -0.149178 R | Geçmedi |
| Open Interest | LONG | 5428 | 302 | -0.145484 R | -0.205917 R | -0.228893 R | Geçmedi |
| Open Interest | SHORT | 10571 | 304 | -0.046409 R | -0.114139 R | -0.119019 R | Geçmedi |
| Funding | LONG | 21183 | 304 | -0.218344 R | -0.279660 R | -0.286735 R | Geçmedi |
| Funding | SHORT | 37703 | 305 | -0.094119 R | -0.163438 R | -0.165413 R | Geçmedi |
| BTC göreli güç | LONG | 20921 | 304 | -0.205855 R | -0.264645 R | -0.270556 R | Geçmedi |
| BTC göreli güç | SHORT | 38564 | 305 | -0.081896 R | -0.149342 R | -0.151322 R | Geçmedi |

Birleşik referans: normal maliyette **-0.110479 R**, 1,5× maliyette **-0.176495 R**. İşlem ağırlıklı ikincil referans sırasıyla -0.101962 R ve -0.166940 R; ana ölçüyle karıştırılmamalıdır.

## Önceden sabitlenen kontroller

- Dört filtre tek tek sınandı: taker LONG >%55 / SHORT <%45; OI son 4 saatte artarken fiyatın işlem yönünde hareket etmesi; funding 30 günlük dağılımda LONG için üst %10, SHORT için alt %10 dışlanması; 72 saatlik BTC göreli güç yön uyumu.
- Her yön için en az 100 kapanmış işlem ve 60 giriş günü şartı sağlandı. Başarısızlık küçük örneklemden kaynaklanan otomatik bir ret değildir.
- Sekiz filtre/yön kolunun tamamında 1,5× maliyet ortalaması ve en iyi üç coin çıkarıldıktan sonraki ortalama negatiftir.
- 20.000 sabit tohumlu takvim haftası blok örneklemesi; 24 karşılaştırma için tek yönlü Bonferroni eşiği 0,05/24. Hiçbir kol mevcut stratejiye veya rastgele yön kontrolüne karşı düzeltilmiş üstünlük şartını geçmedi.
- Dört SHORT kolu rastgele zamanlama kontrolünden anlamlı şekilde daha iyiydi. Mutlak getirileri negatif kaldığı ve diğer şartlar sağlanmadığı için bu tek başına geçiş sayılmadı.
- Bilinmeyen sonuçları -1R kabul eden ayrı duyarlılık hesabında da sekiz kolun tamamı negatiftir. -1R gerçek sonuç veya garanti edilen en kötü durum olarak kullanılmadı.

## Kapsam ve veri eksikleri

- 650/650 sözleşme işlendi; süreç ve indirme hatası yok. İndirici 788 bulunamayan arşiv kaydetti. Bu sayı işlem sayısı değildir; listeleme öncesi veya delist sonrası bulunmayan dosyalar da kapsamdadır.
- Referans adayların 13.391’i girişte elendi, 7.884’ü iptal edildi. 516 işlem 72 saatlik gözlem sonunda açıktı; 526 işlemin funding maliyeti kesinleştirilemedi. Bu 1.042 olay ana kapanmış işlem ortalamasına kazanç, kayıp veya sıfır olarak eklenmedi.
- 9.109 strateji adayında funding yüzdelik sırası, 432 adayda OI özelliği kullanılamadı. Taker ve göreli güç özellikleri tüm adaylarda mevcuttu. Eksik özelliklerin kapsamı her kol için JSON raporunda kayıtlıdır.
- 302 günlük OI dosyası şema kontrolünden geçmedi. Tam rapordaki 1.542 MISSING_FILE okuma kaydı, indiricideki tekil 788 eksik dosya ile aynı sayaç değildir.
- Son Temmuz mumunun gecikmeli girişi 1 Ağustos’a taşabildiği için 305 UTC giriş günü vardır. Sinyal mumu penceresi değişmedi; Ağustos ayındaki önceki araştırma örneklemi bu çalışmaya eklenmedi.
- Takvim hafta sayısı 45; on binlerce çakışan işlem bağımsız deneyler değildir.

## Portföy sonucu neden ayrı tutuldu?

Kapasite ve risk sınırı uygulayan ikincil portföy yaklaşımında bir belirsiz pozisyon sonraki girişleri durdurdu. Referans yalnızca 16 kapanmış işlem ve 3 giriş günü üretti. Bu nedenle on aylık uygulanabilir portföy kârlılığına ilişkin yeterli bir sonuç değildir. Bazı filtrelerde az sayıdaki portföy işleminin pozitif görünmesi ana başarısız sonucu değiştirmez. 72 saatte zorunlu çıkış uydurulmadı.

## Yorumun sınırı ve bırakılan durum

Bu çalışma keşif amaçlıdır: dönemin daha önce hiç incelenmediği doğrulanamadı. Tarihsel emir defteri, kesin tick/lot kuralları, arşiv yayın gecikmeleri ve tam tarihsel enstrüman metadata bilgisi yeniden kurulmuş değildir. Bu sonuç bütün olası stratejilerin çalışamayacağını göstermez; bu stratejinin bu dört sabit filtreyle kârlılığı doğrulanmadı.

Canlı strateji, emir politikası, Telegram davranışı ve yayın sürümü değiştirilmedi. Dört filtre canlı sinyal kararına geçirilmedi. Yeni eşik araması yapılmadan mevcut gözlem düzeni bırakıldı. Funding zaman damgası düzeltmesi yalnızca araştırma uyarlayıcısındadır; canlı gözlem yüzdelik sırası aynı yuvarlama toleransını henüz içermez. Ham canlı kayıtların tutulması ile bu alanın kullanılabilirliği birbirine karıştırılmamalıdır.

## Yeniden üretim ve denetim

- Hesaplama sürümü: `c59fff9bd9a7bb9e57b18b0faed0fdaed934222e`.
- Canlı temel sürüm: `7f80c3d7189a8c4e100314a814533900b78626c0`.
- 165 mevcut test ve 22 araştırma testi geçti. Kod/protokol hashleri ve günlük ortalamalar ayrıca bağımsız toplama hesabıyla doğrulandı; ayrıntılar `integrity_verification.json` içindedir.
- İlk sürümde funding kayıtlarındaki milisaniye farkları veri boşluğu sanıldı. Kapsam kontrolünde tespit edildi; toplu getirilere bakılmadan belgelenen uyarlayıcı düzeltmesi yapıldı. İlk 25 sözleşme çıktısı ayrı saklandı, tüm sonuçlar ikinci sürümde yeniden hesaplandı. Eşikler değişmedi.
- `report.json` bütün karşılaştırmaları, örneklem kapsamını, ay/rejim betimlemelerini, protokolü ve arşiv evrenini içerir. `summary.json` kısa makine çıktısıdır. Sıkıştırılmış denetim paketinde aday/sonuç kayıtları ve kaynak dosya checksumları ayrıca korunmuştur. Ham arşiv ZIP dosyaları pakete dahil değildir; kaynağın gelecekte aynı kalacağı garanti edilmez.
