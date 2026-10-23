# Çizim ve karar motoru — teslim ve sınırlar

23 Ekim 2026. Yeni sürüm `STRUCTURE_CONTEXT_V1_2026_10_23` ayrı hazırlanmıştır.
Eski motorlar değiştirilmedi; Railway'e dağıtım, Telegram gönderimi veya gerçek
emir yapılmadı. Bu sürümün kârlı olduğu gösterilemedi; canlı kullanıma onaylı değildir.

## Neyi yapıyor?

4 saatlik önemli bölgeleri ve 1 saatlik yapıyı, 15 dakikalık yaklaşma/kırılım/geri
dönüş senaryosuyla birlikte kaydeder. 30 dakika ayrı alternatiftir. İzleme,
koşulun oluşması ve geç kalınmış hareket ayrıdır. Hedefe mesafe masrafı
karşılamıyorsa izleme kaydı silinmez; işlem planının neden uygun olmadığı yazılır.

Grafik ile mesaj aynı kayıtlı koordinatları kullanır: anlamlı dip/tepe, karar
bölgesi, kırılım seviyesi, yapısal stop ve ilk karşı bölge. Fibonacci varsa
A/B noktaları gösterilir; yön kararı için bağımsız oy sayılmaz. Gelecekteki
mumlarla geçmiş çizim yeniden yapılmaz. IFVG/FVG veya likidite süpürmesi
dedektörü bu sürümün kapsamında değildir; bu etiketler uydurulmaz.

## Doğrulama

52 otomatik test geçti. Testler gelecekteki verinin dışlanmasını, dip/tepe
onay zamanını, Fibonacci hesabını, çizim/karar koordinatlarının eşleşmesini,
sabit retest stopunu, tekrar bildirim engelini, bozulmuş fikirlerin korunmasını,
mesaj teslim makbuzlarını ve gecikmeli 1 dakikalık sonuç ölçümünü kapsar.
Telegram testlerinde gerçek gönderim yerine taklit istemci kullanılmıştır.
Docker imajı ve canlı tam evren yük testi yapılmamıştır.

BTC, ETH, SOL, ADA, DUSK ve KERNEL için 12 resmî Binance aylık 1 dakika
arşivinin SHA256 kontrolü yapıldı. 15–21 Ağustos ve 15–21 Eylül 2026 UTC
dönemlerinde toplam 12.096 zaman penceresi değerlendirildi. Parametreler
sonuçlara bakılarak değiştirilmedi. Bu küçük örnek tüm coinleri temsil etmez.

| Dönem / senaryo | İzleme | Koşul oluştu | Geç | Uygun plan | Plan başına ortalama sonuç* |
|---|---:|---:|---:|---:|---:|
| Ağustos / 15 dk | 304 | 178 | 64 | 4 | +%0,150 |
| Ağustos / 30 dk | 156 | 79 | 49 | 0 | Ölçülemedi |
| Eylül / 15 dk | 330 | 193 | 65 | 15 | −%0,363 |
| Eylül / 30 dk | 149 | 98 | 36 | 5 | −%0,313 |

*Varsayımsal giriş, stop/hedef veya 3 saat sonu çıkışı; %0,30 toplam
komisyon/kayma varsayımı düşülmüştür. Funding ve gerçek emir gerçekleşmesi
bilinmiyor. Bunlar hesap/portföy getirisi değildir. Örnekler bağımsız değildir.
İzleme kayıtları işlem/kazanç sayılmaz. Rastgele kontrol ve basit 20 mum
kırılım karşılaştırmaları da `summary.json` içinde korunmuştur.

**Ayrı Eylül döneminde sonuç olumsuzdur.** Çizimlerin matematiksel olarak
tutarlı olması kazanç kanıtı değildir. Başarı oranı veya kesin yön iddiası yoktur.
Bu döneme bundan sonra dokunulmamış test dönemi denemez.

## Kanıtlar ve tekrar üretim

`evidence-release/` son kodun özeti, manifestosu, arşiv adresleri/kontrol
toplamları, tüm olaylar, kontrol grupları ve örnek çizimleri içerir.
Örnekler Ağustos'ta her durum/yön için ilk görülen olaydır; kazanan seçilmemiştir.
`evidence/` ilk çalışmanın, `evidence-final/` ara çalışmanın özet ve manifestosunu
korur. Son çalışmanın tüm olay/kontrol kayıtları ve kohort sonuçları önceki
iki çalışmayla birebir eşleştiği doğrulanmıştır; aynı ham kayıtlar çoğaltılmadı.
İlk sonuçtan sonra yalnızca teslim/çalıştırma tamamlamaları, çizimin kesilen
kenarlarının düzeltilmesi ve bildirim simülasyonunun sınırının açıklanması
yapıldı; karar eşikleri optimize edilmedi. Son çalışma aynı kapsamda tekrarlandı.

Bildirim simülasyonu yalnızca kota ve bekleme süresi tahminidir; terminal
durumların bildirim engeli, canlı veri gecikmesi ve Telegram teslimi dahil
değildir. Gerçekte kaç mesaj geleceğinin garantisi değildir. Tüm elenen
kayıtlar ve nedenleri saklanır.

## Çalıştırma ve maliyet durumu

Ana Railway motoru ve geçmiş veri yardımcısının ayarı tekrar kontrol edildi:
başlatma komutu `/bin/sh -c 'true'`, yeniden başlatma `NEVER`, bekleyen değişiklik
yok. Yeni sürüm ayrı Git dalındadır; mevcut dağıtım dalı değiştirilmedi.

Son incelenen 24 saatlik ölçümde ana servisin en yüksek RAM kullanımı yaklaşık
1 GB, disk kullanımı yaklaşık 2,2 GB idi. Bunlar fatura veya kalan kredi
ölçümü değildir. Güncel fatura, kalan kredi ve hesap harcama limiti doğrulanamadı.
Durdurulmuş servisin saklanan diski ücret oluşturabilir; sıfır maliyet denemez.

Yeni süreçte 768 MiB RAM, 4 GiB durum dizini ve 512 MiB boş disk alt sınırı
korumaları vardır. Sınırda veriyi silmek yerine süreç durur. Bunlar Railway
hesap harcama sınırı değildir; mevcut diskin üstüne eklenmesi de ayrıca
ölçülmelidir. Canlı yük/teslim doğrulaması ve maliyet sınırları belirlenmeden
yeniden başlatılmamıştır.
