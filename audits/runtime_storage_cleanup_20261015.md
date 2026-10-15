# LONG/SHORT çalışma ve depolama düzeni — 15 Ekim 2026

Kullanıcı isteği: gereksiz motorları kapatmak ve depolamayı düzenlemek.

## Önceki durum

- 14:23 UTC disk: 3.718 GB / 4.686 GB, %79.34 dolu.
- Eski kırılım gözlemcisi: 1.280 GB; eski analiz DB'leri toplam 1.142 GB;
  eski canlı havuz DB'leri toplam 0.506 GB.
- Yedek alanı: 6.670 GB; bunun 3.891 GB'ı iki kalıcı V4 ham arşivi.
- Yeni grafik motorunun yanında eski analist, devam/dönüş izleyicileri,
  kırılım gözlemcisi, veri fırsatı araştırması ve periyodik araştırmalar çalışıyordu.
- Gate motoru ve geçici test servisi zaten kapalıydı. Diğer eski test
  servisinde çalışan süreç yoktu. Gerekli Binance REST aktarıcısı korunuyor.

## Uygulanan düzen

`LS_LEAN_RUNTIME=1`, native ve grafik motoru açıkken altı süreç başlatır:
grafik motoru, piyasa listesi, mum/fiyat akışı, geçmiş mum tamamlama,
türev destek verisi ve alış/satış akışı. Eski motor ve araştırma döngüleri
başlatılmaz. Veri toplayıcılarının coin listesi eski analiz DB'sinden ayrılır;
aktif grafik fikirleri ve son üç saatlik bildirimler öncelikli tutulur.

Grafik stratejisi ve eşikleri değişmedi. Strateji kimliği:
`47d18d5ee7d920b7856699bf0687dd744247a62de50da53998bee86097e03b37`.

Yalnızca açıkça listelenmiş eski dosyalar arşivlenir. SQLite checkpoint ve
bütünlük kontrolü, gzip sıkıştırma, tekrar açarak SHA256 doğrulaması ve kalıcı
kurtarma manifesti tamamlanınca gereksiz ham kopya kaldırılır. Kullanımdaki veya
değişen dosyalarda işlem durur. Aktif grafik kararları, elenme nedenleri, sonuçlar,
mesaj kayıtları ve Binance mum verileri bu listenin dışındadır.

Emekli motorların tamamlanmamış deneyleri arşivde olduğu gibi korunur;
sonuç uydurulmaz. Güncel grafik motorunun sonuç takibi devam eder.

Günlük aktif veritabanı yedekleri sıkıştırılır. Eski arşivler içerik kimliğiyle
bir kez yüklenir. Eski günlük yedek ancak yeni eksiksiz envanter ve yüklenen
nesneler doğrulandıktan sonra kaldırılır. V4 ham uzak arşivleri de uzaktan tekrar
indirilip açılan kopyanın SHA256 ve boyutu doğrulanmadan kaldırılmaz.

Rutin ağır tablo boyutu taramaları günlük hafif disk kontrolüne dönüşür;
iki dakikalık sağlık kaydı disk kullanımını ve güncel grafik kayıtlarını raporlar.

## Doğrulama

101 ilgili süreç, veri toplayıcı, grafik, geçmiş mum ve yedek testi geçti.
Uzak arşivin bozuk kopyasında ham veriyi koruma ve eksiksiz geri açma için
iki ek test eklendi. Temizleme test grubu toplam 15 testtir.

## Canlı sonuç — 14:55–14:59 UTC

- Dağıtım `027e2f9a-0863-409e-8b02-3ff58d7664d4` başarılı.
- Altı gerekli süreç çalışıyor; emekli süreçler yeniden başlamadı.
- 15 eski dosya: 3,251,804,283 bayttan 409,306,096 bayta indirildi; hata yok.
- Disk kullanımı: 3,741,974,528 → 862,007,296 bayt (%18.40 doluluk).
- Güncel grafik stratejisi kimliği aynı; 156 coin taraması devam ediyor.
- 11 aktif veritabanının sıkıştırılmış günlük yedeği: 147,332,537 bayt.
- Yeni yedek doğrulandıktan sonra 20 eski yedek nesnesi kaldırıldı.
- Kalıcı V4 arşivi: 1,945,526,272 → 555,078,198 bayt. Uzaktan geri açılarak
  SHA256 doğrulandı. Aynı boyuttaki diğer kopyanın kimliği yalnızca okunuyor.
  Geniş kapsamlı kopya temizliği dağıtılmadı ve çalıştırılmadı.

Eski motorlara geri dönülürse yalnızca bayrağı değiştirmek yeterli değildir:
ilgili dosya `legacy-archive/manifest.json` kaydından açılmalı ve SHA256 kontrol
edilmelidir. Güncel grafik ve mum veritabanlarının geri yüklenmesi gerekmez.
