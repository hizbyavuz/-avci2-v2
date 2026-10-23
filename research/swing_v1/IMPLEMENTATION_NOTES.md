# Durum — 22 Ekim 2026

Mevcut sistemin veri toplama, Telegram kartı ve yedekleme altyapısı korunarak
ayrı bir karar ve ölçüm sürümü hazırlandı. Bu bir araştırma sürümüdür; canlıya
alınmadı, Telegram açılmadı, otomatik emir eklenmedi. İlk kod: PR #133.

## Tamamlananlar

* Ayrı LONG ve SHORT hipotezleri, saatlik yapı stopu, kapanmış dört saatlik
  blokla takip ve yedi günlük zaman sınırı.
* Aynı hareketin yeni zirvelerini tekrar tekrar bildirmeyen kalıcı episode
  kaydı; araştırma ve teslim edilmiş mesaj sonuçları ayrı.
* Dakikalık yol ölçümü: stop önce, gap açılışı, işlem içi lehte hareket alt/üst
  sınırları, stop sonrası ham hareket ve eksik veri ayrı. Açık planlar
  24/72/168 saat ölçülerine dahil. Funding bilinmiyorsa tam net sonuç boş.
* Canlı evrende kaynak katalogdaki `underlyingType=COIN` şartı. Varlık sınıfı
  eksik veya eskiyse sinyal yok. Eski modellerin katalog üyeliği değiştirilmedi;
  tam katalog yeni tabloda korunuyor. Canlı evren sürümü:
  `VERIFIED_COIN_USDT_PERP_V1_2026_10_22`.
* 114 test geçti: 25 yeni test, 68 mevcut MAJOR testi, 15 native-chain ve
  6 history-worker testi. Geçici SQLite dosyaları ve sahte gönderici kullanıldı.
  Bunlar yazılım doğrulamasıdır, kârlılık kanıtı değildir.

## Gerçek arşiv denemesinin sınırı

İlk tarama resmi USD-M aylık arşiv kataloğundaki 900 USDT sembolünden başladı.
Ağustos 2026 için 833 yayımlanmış dosya, 67 HTTP 404 var. İlk okumada beş ASCII
dışı sembolün URL kodlaması hatalıydı. Düzeltildi; eski raporlar ayrıca korundu.
Karar eşikleri aynı kaldı. Nihai tarama 467.222 coin/sözleşme-saatini değerlendirdi;
223.052'si hacim/geçmiş bakımından panelde yer aldı.

**Bu katalog yalnız kripto coin içermiyor.** Hisse bağlantılı ve başka USDT
sözleşmeleri de kapsıyor; geçmiş tarihli varlık sınıfı üyeliği doğrulanmadı.
Bu nedenle aşağıdaki geniş arşiv denemesi canlı coin evreninin doğrulaması değildir.
Araştırma evreni: `UNCLASSIFIED_USDT_ARCHIVE_V1`. Bugünkü coin listesini geçmişe
uygulayarak bu boşluk gizlenmedi.

| Ağustos geniş arşiv taraması | LONG | SHORT |
|---|---:|---:|
| Ayrı sinyal olayı | 436 | 189 |
| Ardında tam 7 günlük saatlik yol bulunan | 388 | 148 |
| Ham yolda yönünde %30'a erişen | %23,71 | %0,00 |
| Eşleştirilmiş kontrolde aynı ham erişim | %12,63 | %2,03 |
| Ham lehte hareket medyanı | %14,57 | %4,36 |
| Ham ters hareket medyanı | %5,75 | %7,61 |

**Bu yüzdeler kazanma oranı veya işlem getirisi değildir.** Stop sonrası hareketi
de kapsayan keşif ölçüleridir; saatlik sonraki açılış sadece tarama referansıdır.
Kontroller aynı anda hacim/oynaklık mesafesi en yakın beş adaydan sabit tohumla
seçildi; mesafe raporda var, kesin eşleşme garantisi yok. Basit momentum ve eski
MAJOR kıyasları, gün bloklu güven aralıkları, piyasa rejimleri ve hareketin ilk
bölümünü yakalama ölçüsü tamamlanmadı. LONG için üstünlük/kârlılık sonucu yok;
SHORT için bu dönemde olumlu bir büyük-hareket işareti görülmedi.

24 sinyal gününde toplam 625 olay, tek bir günde en fazla 94 olay var. Günlük
altı bildirim sınırı yalnız kullanılabilirlik sınırıdır; model seçiciliği veya
en iyi altı işlemi bulduğu anlamına gelmez. Bildirim sırasının ayrıca sınanması gerekir.

Önceden belirlenen hash sırasıyla sekiz olay dakikalık veride kontrol edildi:
yedisi tam ölçüldü, birinin takip eden dakika açılışında giriş şartı bozuldu.
Yedi sonuçta da funding kapsamı bilinmediğinden tam net getiri hesaplanmadı.
Bu küçük örnek yalnız uygulamanın gerçek mumlarla çalışmasını kontrol eder.

Eylül için indirme, ortamın `data.binance.vision` ağ erişimini engellemesiyle
kesildi. 900 sembolden 322'sinin saatlik dosyası önbellekte; kalanlar delist veya
başarısız aday olarak sayılmadı. Eylül doğrulaması **tamamlanmadı**. Kısmi,
indirme sırasına bağlı evrenden başarı oranı çıkarılmadı. Ayrıntı `RUN_STATUS.json`.

## Değişiklik ve kaynak kaydı

İlk ön kayıt dosyası ve hash'i korunuyor. Sonradan bulunan URL düzeltmesi ve canlı
coin sınıfı kontrolü `INTERVENTIONS.jsonl` içinde. Canlı ileri test başlamadığı
için hiçbir geçmiş canlı işlem yeniden etiketlenmedi. Yeni canlı sonuçlar eski
MAJOR veya sınıflandırılmamış arşiv sonuçlarıyla birleştirilmemeli.

Binance kaynakları:

* [Exchange Information / underlyingType](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data)
* [SNDKUSDT hisse bağlantılı sözleşme duyurusu](https://www.binance.com/en/support/announcement/detail/80549fadb3e447d1a859b8cedd0ccd69)
* [Resmi veri arşivi](https://data.binance.vision/)

## Çalıştırma ve kalan kapılar

Kod çalıştırma yolu ön kayıt dosyasında. `SWING_NOTIFICATIONS=0` varsayılanıyla
kağıt izleme hazırlanmıştır. `--report` bütün kayıt durumlarını sayar. Arşiv
tarayıcı `--offline` ile yalnız kayıtlı katalog ve yerel dosyaları kullanabilir;
eksik dosya `NOT_CACHED` olur, ağ isteği yapılmaz.

Canlıya geçmeden önce: mevcut private history relay'in çalışma durumu ve COIN
alanını taşıması, tam evren mum kapsamı, kalıcı disk/yedek durumu doğrulanmalı.
Eski kolektörün 166 sembollük sınırı artırılmadı; bu sınır aşılırsa tam evren
görüldüğü iddia edilmez. Bu sürüm kapasite sorunu çözülmüş sayılmıyor.

İşlem avantajı iddiasından önce: geçmişte geçerli kripto üyeliği, tamamlanmış
ikinci dönem, bütün adaylarda dakikalık plan sonuçları, gerçek funding tahakkuku,
maliyet stresi ve basit kıyaslar gerekir. Bu eksikler yüzünden sürüm taslakta
ve bildirimler kapalı kaldı. LONG ve SHORT ayrı değerlendirilmelidir.

## Sonraki tamamlanan kıyas

Basit 24 saat momentum kıyası tamamlandı; eşikler değişmedi. Ayrıntı ve güncel
karar `BASELINE_FINDINGS.md`, sayısal kayıt `MOMENTUM_COMPARISON.json` içinde.
Bu ek çalışma beş yeni testi geçti; önceki 114 ile toplam 119 geçiş kaydı var.
İlk tablolar olay ağırlıklı, yeni kıyas saat ağırlıklıdır; sonuçlar birbirinin
yerine yazılmadı. Eylül, kripto üyeliği, tam dakika planları ve funding eksikleri sürüyor.

## Dakika verisi kapsam denetimi

625 olayın tamamı denetlendi: 12 tam kağıt plan, 2 geçersiz giriş, 611 eksik
giriş verisi. Karar ve ölçüm kuralları değişmedi. Altı yeni test geçti; toplam
125 geçiş kaydı var. Küçük örnek sonuçları `MINUTE_FINDINGS.md`, tam veri isteği
`MINUTE_DATA_REQUESTS.json` içinde. Funding ve genel kârlılık hâlâ bilinmiyor.


## 22 Ekim: ağ erişimi düzeldi, kapsam tamamlandı

Güncel sonuç `DATA_REFRESH_FINDINGS.md`: Ağustos için 479/479 dakika arşivi, 625 adayın 556 geçerli tam planı ve 69 geçersiz girişi. LONG ücret/kayma sonrası, funding öncesi ortalama +%4,02; SHORT −%1,35. Evren hâlâ sınıflandırılmamış USDT sözleşmeleridir. Önceki 14 bilinen kayıt birebir korundu.

Eylül: 869 saatlik arşiv doğrulandı, 31 gerçek arşiv 404; 615 aday. LONG ham %30 erişimi %11,91, eşleştirilmiş kontrol %12,54. Ağustos bulgusunu genel avantaj saymak için destek yok. Eylül dakika planı ve funding tamamlanmadı. Karar/çıkış kuralları aynı, canlı/Telegram kapalı. Ayrıntı ve kanıtlar yeni raporda; önceki kısmi raporlar tarihsel kayıt olarak korunur.
