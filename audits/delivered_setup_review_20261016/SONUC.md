# Gönderilen önerilerin ilk karşılaştırması

Ölçüm raporu: 16.10.2026 17:40:33 Türkiye saati.

**Sonuç: İncelenen dört öneri türünün hiçbirinin üç saatlik ortalama referans getirisi pozitif değil. Henüz bir türü başarılı ilan edip Telegram önerisi olarak öne çıkarmıyoruz.**

## Üç saatlik ana değerlendirme

Hedef veya stop daha önce görülürse o seviye, aksi halde süre sonu fiyatı kullanılıyor. Hesapta %0,30 gidiş-dönüş maliyet varsayımı var. Yüzdeler kaldıraçsız işlem büyüklüğüne göre; gerçek hesap kazancı değil.

| Öneri türü | Sonucu ölçülen | Artıda sonuç | Ortalama net referans getiri | Belirsiz/eksik sonuç kaydı |
|---|---:|---:|---:|---:|
| Sıkışmadan kırılım | 28 | 19 | %-0.185 | 0 |
| Seviye geçişi | 56 | 28 | %-0.781 | 3 |
| Geri çekilme sonrası devam | 25 | 9 | %-0.459 | 1 |
| Tepe/dip kırılımı | 5 | 2 | %-0.840 | 0 |

## Mesaj anında görülen somut sorun

135 giriş bildirimi incelendi. 134 tanesinde hesap geometrisi geçerli; 1 tanesinde bildirim onayı anındaki fiyatla hedef mesafesi varsayılan maliyeti karşılamıyor.

Geçerli 134 planın 124 tanesinde net hedef kazancı, stopta hesaplanan kayıptan küçük. 82 tanesinde yalnızca hedef/stop ile kapanıldığı varsayılırsa başa baş için %75'in üzerinde hedefe ulaşma oranı gerekiyor; 28 tanesinde bu oran %90'ın üzerinde.

Bu oranlar gerçekleşmiş başarı oranı değil, hedef/stop mesafelerinin matematiğidir. Süre sonunda kapanışlar ve değişken işlem büyüklüğü farklı sonuç verir.

Örnek: MAGICUSDT SHORT, 15 Ekim 23:00 Türkiye. Kaydedilen net hedef alanı yaklaşık %0,113; masraf dahil stop kaybı %1,740. Sadece bu iki çıkış kullanılsa bir stop yaklaşık 15,4 hedef kazancını siler. Bu, bu işlemin gerçekte stop olduğu iddiası değildir.

Sıkışma sonrası devam LONG alt grubunda üç saatlik 13 sonucun 11'i artıda olmasına rağmen ortalama net sonuç yaklaşık -%0,308. Bu küçük alt grup, yüksek artıda kapanış sayısının tek başına olumlu beklenti sağlamadığını gösteriyor; bir strateji seçme gerekçesi değil.

## Ölçümde kalan boşluk

| Mesaj türü | Süre | Kayıt | Ölçülmüş | Belirsiz/veri sorunu | Süresi dolmamış | Süresi dolmuş, sonuç yok |
|---|---:|---:|---:|---:|---:|---:|
| Giriş oluştu | 15 dk | 135 | 129 | 4 | 2 | 0 |
| Giriş oluştu | 60 dk | 135 | 123 | 4 | 8 | 0 |
| Giriş oluştu | 180 dk | 135 | 115 | 4 | 16 | 0 |
| İzle | 15 dk | 415 | 0 | 0 | 6 | 409 |
| İzle | 60 dk | 415 | 0 | 0 | 19 | 396 |
| İzle | 180 dk | 415 | 0 | 0 | 60 | 355 |

Bu envanter 16 Ekim 17:43 Türkiye saati civarındaki ayrı kayıt kontrolüdür. Üstteki model tablosuyla farklı rapor anına aittir. İzle mesajlarına varsayımsal işlem açılmış gibi kazanç atanmıyor.

## Uygulanan ve engellenen işlem

Ölçüm kuyruğu düzeltmesi GitHub üzerinde hazır. Railway dağıtım aracı, kullanıcının yazılı onayına rağmen iki girişimde de `Cancelled — the user did not approve this action. No changes were made.` döndürdü. Yeni sürüm canlıya alınmadı. Bu engeli aşmak için farklı bir dağıtım yolu denenmedi.

Canlı commit: `360d29aebe530c0b578fecc454f78537ee6b4777`.
Hazır ölçüm commit'i: `da8bb84dca5cb17092374c5d9a6e2c244c82a6a2`.

Sinyal kuralları, hedef/stop seviyeleri, maliyet varsayımı veya Telegram mesaj politikası bu incelemeyle değiştirilmedi.

## Sonraki kararın koşulları

1. Hazır ölçüm sürümü dağıtılınca önce SUCCESS ve sonuç kuyruğunun gerçekten ilerlediği doğrulanacak.
2. Tek tek mesaj sonuçları geldiğinde aynı sürüm, model ve sürede; günlük/4 saatlik yön, akış desteği ve mevcut kazanç/kayıp oranı ile sonuçlar eşleştirilecek. Eksik veriden ortak özellik uydurulmayacak.
3. Eski eşik olan net kazanç/kayıp >=1 ayrı gözlemsel karşılaştırma olarak incelenebilir. Bu eşik tek başına kârlılık garantisi değildir; hedef küçültülmeyecek veya stop sonradan değiştirilmeyecek.
4. Yeni bir kural adayı açıkça sabitlendikten sonra, henüz görülmemiş sonraki dönemde mevcut kuralla aynı fırsatlarda karşılaştırılacak. Daha önce incelenmiş mesajlar doğrulama dönemi sayılmayacak.
5. Ortalama getiri yanında eşit riskle sonuç, kayıp büyüklüğü, veri kapsamı ve aynı coin/zaman bağımlılığı incelenecek. Mevcut toplu rapordan bunların tümü hesaplanamıyor.

## Tekrarlanabilirlik

`python audits/delivered_setup_review_20261016/analyze.py` komutu kaydedilmiş girdiyle aynı raporu üretir. Kaynak, Railway loglarının `CHART_FIRST_OUTCOMES`, `CHART_RELIABILITY_COHORT` ve `CHART_FIRST_DELIVERY` kayıtlarıdır. `input.json` veri anını ve ham kayıtları; `report.json` hesaplanan karşılaştırmayı içerir.
