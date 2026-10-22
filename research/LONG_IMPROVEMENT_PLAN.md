# LONG geliştirme yönü — 22 Ekim 2026 UTC

Amaç isabet oranını tek başına yükseltmek değil; yanlış girişleri ve maliyetleri
azaltırken iyi hareketleri korumaktır. Mevcut LONG seçimi/çıkışı değişmez.
Bu belge yeni bir stratejinin uygulandığını veya başarılı olduğunu söylemez.

## Eldeki deneyler ne söylüyor?

- BASE LONG: 399 tam plan, 217 pozitif; ücret/kayma varsayımı sonrası +0,846R.
- Türev bağlamı eklenen CONTEXT: 99 plan, %55,56 pozitif, +0,793R. Farklı
  seçilmiş altküme; daha yüksek isabet, daha yüksek ortalama getiri göstermedi.
- Aynı 399 planda sabit 2R: 223 pozitif ama ortalama +0,516R. Mevcut çıkışı
  değiştirmek için destek sağlamadı.
- Güncel eşleşmiş kontrolün LONG fark aralığı sıfırı içerir. Geliştirme verisinde
  görülen pozitif sonucu gelecekteki başarı olasılığı olarak kullanmayacağız.

## Öncelikli tek aday: kırılan seviyenin geri testini beklemek

Gerekçe: hacimli kırılımdan hemen sonra geri düşen girişleri ayırmayı denemek.
Bedeli: geri dönmeden yükselen coinleri kaçırmak ve bazı girişleri geciktirmek.
Bu hipotezin avantajı henüz ölçülmedi. Tam zaman penceresi, geri test toleransı,
giriş anı ve iptal koşulları, uygulama ve yeni sonuçlar görülmeden ayrı bir
deney sözleşmesinde sabitlenecek. Bu belge sayısal ön kayıt yerine geçmez.

Karşılaştırma aynı temel aday listesi üzerinden yapılacak: hemen giriş ve
beklenen giriş ayrı gerçek zaman referanslarıyla, stop/geometri yeniden
kontrolüyle ölçülecek. Temel adayın ilk fiyatı gecikmiş girişe taşınmayacak.
Hiç geri test vermeyen, beklerken stop olan ve verisi eksik coinler kaybolmayacak.
Kaçırılan kazanç, isabet, tüm adaylar başına sonuç, işlem başına net R, en kötü
kayıplar, süre ve maliyet ayrı raporlanacak. Funding eksikse tam net bilinmez.

BTC yönü ve göreli güç zaten kaydediliyor; önce sonuçları bu gruplarda ayıracağız.
Tek bir iyi geçmiş altkümeden yeni veto/filtre üretmeyeceğiz. OI/funding/akış
gözlemleri de kayıt olarak kalır. Yeni deney Telegram seçimlerini değiştirmez.

## Araştırma dayanağı

Bailey ve arkadaşlarının araştırmaları, aynı geçmişte çok sayıda varyant
denemenin aşırı uyum riski taşıdığını gösterir. Bu nedenle yalnız yeni bir
gerekçesi olan az sayıda deney, bütün başarısız varyantların kaydı ve sonraki
veride değişmeyen kurallar kullanılacak. Bu yaklaşım başarı garantisi değildir.

- https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf
- https://www.davidhbailey.com/dhbpapers/overfitting.pdf
- OI'nin tanımı ve yönü tek başına belirlememesi:
  https://www.binance.com/en/academy/articles/what-is-open-interest

## Başlatma girişiminin durumu

`long_short_launch_check.py`, yalnız mevcut kalıcı diski okuyan, ağ isteği ve
Telegram gönderimi olmayan tek seferlik kontroldür. Üç yerel testi geçti:
eksik dizini oluşturmama, SQL yazısının engellenmesi ve sırların çıktıya
girmemesi, bozuk kaydın açık hata olması. Hiçbir canlı fiyat testi yapılmadı.

Kod commit'i: `62dfea5986699f1df5cad60d5329d3f430ed6fd6`.
Motor servisinde bu commit'e sabit kaynak, `Dockerfile.launch-check`,
`python -u long_short_launch_check.py`, `restartPolicyType=NEVER`,
`LS_RUNTIME_PROFILE=SWING_STRUCTURE_V1`, `SWING_NOTIFICATIONS=0` hazırlandı.
Bölge ve volume mount değişmedi; başka servise dokunulmadı.

Railway `accept_deploy` çağrısı "Cancelled — the user did not approve this
action. No changes were made." sonucu verdi. İptalden sonra salt okunur
kontrolle yeni deployment olmadığı doğrulandı; son deployment hâlâ
17 Ekim tarihli `REMOVED` kaydı. Canlı kaynak eski d74f382, başlangıç komutu
eski daemon olarak kaldı. Hazırlanan dokuz değişiklik
`888082fd-1fde-47e2-a23d-37b5bc2c568e` patch'inde STAGED durumda.

Bu iptal otomatik güvenlik incelemesi reddi olarak yorumlanmadı. Kullanıcıdan
yeniden onay alınmadan başka araç/CLI/bölge üzerinden aynı işlem denenmedi.
**Sinyal motoru başlamadı, disk raporu alınamadı, Telegram gönderilmedi.**
