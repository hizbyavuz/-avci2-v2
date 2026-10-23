# 5. adım — canlı veride eski/yeni gözlemsel karşılaştırma

23 Ekim 2026. Kullanıcının “Devam bağla bitir” onayıyla hazırlanan bağlantı.
Bu dosya uygulama ve test raporudur; dağıtımın başarılı olduğunu kendi başına iddia etmez.
Canlı dağıtım sonucu ayrıca Railway durumu ve `TA_SHADOW_HEALTH` kaydıyla doğrulanır.

## Ne bağlandı?

`structure_shadow_start.py` mevcut `structure_live_start.py` sürecini aynı ayarlarla çalıştırır.
Mevcut ortak mum/market veritabanları hazır olunca ayrı `technical_observer_v1.shadow_runtime`
sürecini başlatır. Yeni HTTP/WebSocket toplayıcısı, servis, gerçek emir veya Telegram gönderimi eklenmez.
Eski motorun Telegram akışı ve uygunluk kararları devam eder. Yeni seviyeler henüz otomatik
olarak işlem önerisine yükseltilmez; kayıt, karşılaştırma ve ölçüm içindir.

Veriler `/app/.long-short-state/technical_observer_shadow_v1` içinde ayrı tutulur.
Mevcut mum ve market veritabanları `mode=ro` ve `query_only=ON` ile açılır.
Eski olay/kağıt takip geçmişine yazılmaz, geçmiş silinmez ve yeniden etiketlenmez.

## Kayıtların kapsamı

- Her kapalı 15m mum için her mevcut evren sembolü taranır. Aday yok, eski ret,
  yeni zayıf kırılım, eksik hedef/stop ve veri hazırlığı hataları ayrı durumlarla saklanır.
- Eski `assess` aynı karar zamanı ve toplayıcının son 220 mumuyla yeniden hesaplanır.
  Bu karşılaştırmadır; geçmişte Telegram’a gerçekten gönderildiğine dair makbuz değildir.
- Yeni motor ilk geçerli 200–220 mumla sabit başlangıç oluşturur. Daha sonra yalnız kapanmış
  ve karar anında alınmış mumları sona ekler. Toplayıcının eski mumları budaması bu başlangıcı değiştirmez.
  Zaman boşluğu doldurulmaz; son 8 mumdaki OHLC/hacim revizyonu akışı karantinaya alır.
  Daha eski kaynak revizyonları yeniden taranmaz; saklanan ilk gözlem değişmez.
- Yeni setup için 2–3. adımın tam JSON nesnesi saklanır: girdiler, onaylı/reddedilen swing’ler,
  bölgeler, trendler, giriş referansı, SL/TP referansları, gerekçeler ve çizimin kullanacağı görünüm.
  Aynı nesneyi `technical_observer_v1.presentation` okuyabilir; grafik yeniden seviye hesaplamaz.
- Gerçek toplayıcının koruduğu Binance `PRICE_FILTER` kullanılır. Sembol, kaynak etiketi,
  gözlem zamanı, bir saatlik güncellik ve pozitif tick denetlenir. Kaynak pazar satırı ve katalog
  hash’i kayda eklenir. Eksik/eski filtrede tahmini tick üretilmez; eşleşme doğrulanmış sayılmaz.
- Kanıtlar içerik hash’iyle adlandırılmış sıkıştırılmış JSON dosyalarıdır. Var olan dosya
  değiştirilmez. SQLite tarama, setup, grup, sonuç ve sağlık tabloları bu dosyalara işaret eder.

## 100 örnek sayacı tam olarak ne sayıyor?

Mevcut üç saatlik ölçüm ufku ve bir sonraki tam dakika başlangıcı korunur.
Aynı sembolde yön ve zaman dilimi farklı olsa da çakışan üç saatlik gözlem pencereleri tek grup olur.
Grubun ilk kaydı temsilcidir; sonraki güncellemeler saklanır fakat yeni bağımsız örnek sayılmaz.
Karşılaştırmada ilk kaydın tüm eski/yeni varyantları, retler dahil, aynı sonraki 1m fiyat yolunda ölçülür.

Birincil sayaca yalnız **aktivasyondan sonra kapanan mumdan oluşmuş, eski/yeni girdileri ve tick’i
doğrulanmış, sonraki üç saatlik 1m yolu eksiksiz gruplar** girer. Başlangıç tekrarı, veri eksikliği,
aynı sinyal tekrarı ve sentetik testler 100’e eklenmez. Hatalı ilk kayıt sonradan iyileşse bile
grubun ilk temsilcisi değiştirilmez; ilk kayıt sorunluysa o grup birincil sayıdan hariç kalır.
Bu, zaman çakışmasını azaltan operasyonel tanımdır; farklı coin’lerin istatistiksel bağımsızlığını kanıtlamaz.

Sonuçta lehte/aleyhte azami hareket ve SL/TP **referanslarına temas** kaydedilir.
Girişin gerçekleştiği varsayılmaz; PnL, kazanma oranı veya işlem başarısı uydurulmaz.
Aynı 1m mumda iki referans görülürse sıra “bilinmiyor” olur. Eksik yol bir saat yeniden beklenir,
sonra çözümlenmemiş olarak korunur; kayıp sayılmaz. Geç gelen veriler bu sürümde nihai sonucu değiştirmez.

100 tamamlanınca yalnız `review_ready=true` olur. Eşik değiştiren, işlem açan veya yeni mantığı
aktif Telegram karar motoruna taşıyan otomatik bir yol yoktur. 100 örnek de kârlılık kanıtı değildir.

## Kaynak ve hata sınırları

Yeni gözlem için 256 MiB disk, 160 MiB süreç tepe RSS, akış başına 4096 mum sınırı vardır.
Son sınır ilk 220 mumdan sonra 15m akışında yaklaşık 40 gündür; otomatik budama/yeniden tohumlama yoktur.
Sınıra gelince gözlem durur veya ilgili akış veri sorunuyla kaydedilir; geçmiş korunur.
Tüm canlı dönemin tamamlanacağını veya 100 örneğin belli bir saatte dolacağını garanti etmez.

Başlatıcı mevcut motoru, toplayıcı torun süreçlerini ve yeni gözlem sürecini birlikte ölçer.
Mevcut 768 MiB toplam RSS sınırında yeni gözlem süreci durdurulur; eski motor kendi değişmeyen
güvenlik kontrolüyle devam eder. Railway 1 CPU / 1 GB konteyner limiti, kalıcı disk, bölge ve
yeniden başlatma politikası değiştirilmez. Gözlem hatası eski motoru durdurmaz ve otomatik yeniden başlatma döngüsü yaratmaz.

Yerel, açıkça sentetik 25 sembollük mühendislik denemesi: 25 kayıt, 3.017 saniye,
9.875 MiB gözlem dizini ve 29.383 MiB tepe RSS. Bunlar canlı yük/ücret garantisi veya piyasa örneği değildir.

## Doğrulama ve değişmeyen parçalar

`STEP5_RESULTS.json` yeniden üretilebilir test sonuçlarını ve kaynak SHA256’larını içerir.
Çalıştırma:

```sh
python -m research.technical_observer_v1.run_step5_validation --output /tmp/step5-results.json
```

Önceki tanımlar/çizim/regresyon testleri ve yeni salt-okunur bağlantı, tekrar ayıklama,
veri boşluğu, kaynak revizyonu, kayıt değişmezliği, süreç izolasyonu ve kaynak sınırı testleri birlikte çalışır.
Gerçek MAGMA ham karar snapshot’ına bağlı üç test ayrıca “doğrulanmadı” kalır.
MAGMA’nın doğru düzeltildiği veya yeni mantığın kârlılığı bu rapordan çıkarılamaz.

Eski `structure_engine`, `structure_live_start.py`, `long_short_*.py`, Dockerfile ve bağımlılıklar
canlı taban commit’i `c39383597fc8ac68849fd80ee7a0a932e053d3d7` ile aynıdır.
Frozen 42/12 dahil mevcut eşikler ve güvenlik kapıları değiştirilmemiştir.
Yeni analiz config hash’i: `ba3f99e9804c6eaa4c508a78e3aaaee028de28ac26642de504d550de4b29f6f3`.

## Yayın ve geri dönüş

Yalnız ana servisin Git kaynağı yeni exact commit’e sabitlenir ve başlangıç komutu
`python -u structure_shadow_start.py` yapılır. Eski başlangıç komutu
`python -u structure_live_start.py`, eski kaynak commit’i yukarıdaki canlı tabandır.
Geri dönüşte yeni gözlem dizini silinmez. Yardımcı relay servisine ve değişkenlere dokunulmaz.

Yayından sonra kontrol: doğru commit ile deployment `SUCCESS`, `TA_SHADOW_STARTED`,
`TA_SHADOW_HEALTH` durum/örnek/kaynak sayaçları ve mevcut `STRUCTURE_HEALTH` akışı.
Eksik TF geçmişi veya tick varsa sağlık kaydı bunu gösterir; “bağlandı” ifadesi tüm sembollerin
analize hazır veya işlem için uygun olduğu anlamına gelmez.
