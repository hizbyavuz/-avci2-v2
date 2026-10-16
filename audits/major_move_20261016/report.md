# LONG/SHORT motor denetimi ve bağımsız piyasa karşılaştırması

16 Ekim 2026. Saatler Türkiye saati (UTC+3). Piyasa ana kesiti 20:30; seçilmiş örneklerin ikinci kesiti 20:45. Canlı çalışma kanıtı yaklaşık 19:50–20:53 aralığından alınmıştır.

## Sonuç

Motor çalışıyor; fakat çalışması, doğru fırsatları yakaladığı veya para kazandırdığı anlamına gelmiyor. İncelenen örneklemde mevcut kurallar çok az aday üretiyor ve girişe hazır sinyal üretmiyor. Bunun yanında bazı WATCH (bekle) kayıtlarında, beklenen kırılma seviyesindeki stop mesafesi motorun %8 sınırını aşıyor. Bu, giriş gerçekleşmeden görülebilen bir plan uygunluğu eksikliği.

Bağımsız incelemede yükseliş ve düşüş yönünde araştırmaya değer coinler bulundu. Ancak bunları doğrulanmış, %10 kazandıracak işlemler olarak sunmak mümkün değil. Özellikle düşüş adaylarının çoğu yakındaki desteğe gelmişti; bazıları sonraki 15 dakikada ters yönde hareket etti. Bu denetim bağımsız yöntemin daha kârlı olduğunu göstermiyor.

Canlı kurallar, eşikler ve dağıtım değiştirilmedi. Hiçbir otomatik emir veya deneme amaçlı Telegram mesajı gönderilmedi.

## Ne incelendi?

- Canlı sürüm: GitHub `43da26dd9978242c0fe081954ca86b1a90528cb6`, motor `MAJOR_MOVE_V1_2026_10_16`. Yerel inceleme tabanı aynı dosya ağacını taşıyor.
- Veri toplama → kapanmış mumlar → aday seçimi → stop/hedef mesafesi → Telegram gönderim kontrolü → sonuç izleme kodu ve canlı kayıtları incelendi.
- Binance aktif USDT sürekli vadeli sözleşmelerinden 24 saatlik işlem hacmi en az 8 milyon USDT olan 154 coin seçildi. 15 dakika, 1 saat ve 4 saat için toplam 462 veri yanıtı alındı; indirme hatası yok.
- Aynı kapanmış mumlarla hem değişmemiş üretim kararı hem bağımsız araştırma filtresi hesaplandı. Referans fiyat son kapanmış 15 dakikalık mumdur; canlı WebSocket fiyatı veya gerçekleşmiş işlem fiyatı değildir.
- Son 24 saat için 97 kapanış anında aynı 154 coin yeniden değerlendirildi: 14.938 coin-zaman değerlendirmesi. Aynı coinin tekrarları bağımsız işlem sayılmadı.
- İlgili veri, karar, mesaj, sonuç ve kaynak yönetimi testlerinden 112'si geçti. Test başarısı stratejinin kârlılık kanıtı değildir.

## 1. Kuralların elediği yerler

20:30 kesitindeki 154 coin:

| Sonuç | Coin sayısı | Anlamı |
|---|---:|---|
| Aranan büyük hareket yapısı yok | 138 | Üç mevcut senaryodan hiçbirinin bütün koşulları sağlanmıyor |
| Stop çok uzak | 11 | Yapı var ama mevcut fiyata göre stop mesafesi %8'i aşıyor |
| Önünde yeterli mesafe yok | 4 | En yakın 4 saatlik tarihsel engel %10'dan yakın |
| Yeterli 4 saatlik geçmiş yok | 1 | CTUSDT için 180 kapanmış mum bulunmuyor |
| Hazır / bekle | 0 / 0 | Bu kesitte yayımlanabilir öneri yok |

Son 24 saatin yeniden değerlendirmesinde:

| Sonuç | Coin-zaman değerlendirmesi |
|---|---:|
| Yapı yok | 14.264 |
| Stop çok uzak | 361 |
| Önünde yeterli mesafe yok | 172 |
| Geç kalınmış giriş | 14 |
| Geçmiş eksik | 97 |
| Bekle | 30 |
| İşleme hazır | **0** |

30 bekle kaydı 30 ayrı fırsat değildir. Aynı coinlerin farklı kapanışlardaki tekrarlarını içerir. Bugünkü likidite listesi geçmişe uygulanmıştır; o anların gerçek coin listesi ve hacmi yeniden kurulmamıştır. Canlı fiyat tazeliği, Telegram gecikmesi ve işlem maliyeti de bu tekrarın parçası değildir. Dolayısıyla bu çalışma geçmiş kazanç testi veya geçmişte gerçekten gönderilmiş mesaj dökümü değildir.

Bu örneklem, “mesaj yoksa kesin arıza var” demek için yeterli değil. Fakat “bekleyelim, her şey tamam” demeyi de desteklemiyor: kuralların fırsat yakalama kapsamı ayrıca doğrulanmalı.

## 2. Stop mesafesinde doğrulanan plan eksikliği

Mevcut motor stopu rastgele sabit bir yüzdeye koymuyor:

- İlk yükseliş: saatlik sıkışma kutusunun altı.
- Büyük yükselişte devam: son teyitli saatlik dip altı.
- Tepeden düşüş: son teyitli saatlik tepe üstü.
- Buna saatlik ortalama hareket genişliğinin (ATR) %15'i kadar tampon ekleniyor. Sonra fiyat ile stop arasındaki mesafe %8'i aşıyorsa reddediliyor.

Sorun: WATCH aşamasındaki risk mevcut fiyattan hesaplanıyor; beklenen giriş seviyesinde aynı hesabın mümkün olup olmadığı ayrı kontrol edilmiyor. 30 WATCH kaydının **19'unda**, fiyat gerekli kırılma seviyesine geldiğinde aynı stopla mesafe %8'i aşıyor. Dağılım: SPORTFUN 8, BTW 6, PYTH 3, EPIC 2 kayıt.

Örnek SPORTFUN SHORT: gözlenen fiyat 0,01543; gerekli kırılma seviyesi 0,01423; stop 0,0164351. Mevcut fiyatta stop mesafesi yaklaşık %6,51, fakat kırılma seviyesindeki girişte **%15,50**. Seviyeler değişmezse bu bekleyen fikir motorun giriş risk sınırını karşılayamaz.

Canlı AKE WATCH örneğinde de 0,032959 giriş seviyesi ve 0,0280621 stop yaklaşık **%14,86** risk oluşturuyor. Daha aşağıdaki güncel fiyattan hesaplanan mesafe daha küçük görünüyor.

Bu, motorun %8'den büyük riskli bir mesajı mutlaka gönderdiği anlamına gelmez. Gönderimden hemen önce canlı fiyatla risk yeniden kontrol edilip reddediliyor. Eksik olan, uygun olmayan bekleyen fikrin baştan açıkça işaretlenmesi. Girişe kadar yeni bir yapının oluşması seviyeleri değiştirebilir.

Önerilen düzeltme: bekleme aşamasında hem güncel fiyat hem planlanan giriş için stop, engel mesafesi ve risk/ödül hesaplanmalı; seviyeler değişmeden gerçekleştirilemeyecek plan ayrı bir nedenle gösterilmeli. Stopu sırf %8'e sığdırmak için gelişigüzel yaklaştırmak çözüm değil.

## 3. Bağımsız aday araştırması

Araştırma filtresi sonuçlar görülmeden tanımlandı: 4 ve 12 saatlik yön uyumu, BTC'ye göre 4 saatlik güç, 24 saatlik hacim ağırlıklı fiyatın doğru tarafında bulunma, son saatte normalin üzerinde işlem hacmi, son 4 saatin 15 dakikalık kırılma seviyesine yakınlık ve yerel 15 dakikalık yapıya göre en fazla %8 stop mesafesi.

Bu ayrı filtre test edilmiş bir strateji değildir. Motorun saatlik yapısıyla aynı giriş ve stop mantığını kullanmadığı için aday sayıları tek başına başarı karşılaştırması sayılamaz.

154 coinin 140'ı son 4 saatte düşüyordu; ortanca değişim yaklaşık -%2,91. Bağımsız filtre 43 SHORT kırılma araştırması ve 39 SHORT bekleme adayı buldu. **Bu 82 kaydın 80'inde en yakın tarihsel 4 saatlik destek %10'dan yakındı; ikisinde böyle bir destek bilinmiyordu. Bilinmeyen destek, boş ve güvenli alan anlamına gelmez.** Bu filtrede bütün koşulları sağlayan LONG çıkmadı; aşağıdaki LONG'lar araştırma örneğidir.

### Aynı anda görülen somut örnekler

Fiyat ve mesafeler 20:30 kapanışına aittir. “Engel mesafesi” bir kazanç tahmini değildir.

| Coin / yön | Bağımsız bulgu | Motor neden elemiş? | Değerlendirme |
|---|---|---|---|
| BASED LONG | 4 saatte +%10,22; son saat hacmi normalin 24,32 katı; referans 0,05371 | Devam senaryosunda tepenin yeterince eski olması koşulu sağlanmıyor; ilk hareket koşulları da uygun değil | Güçlü yükseliş incelemeye değer. Ancak yerel stop bile %8,07; doğrudan uygun giriş diye sunulamaz |
| INJ SHORT | Referans 8,646; kırılma 8,627; yerel stop 9,07191 (%4,93); tarihsel destek 7,802 (%9,76 mesafe) | Daha alçak teyitli saatlik tepe yok | Motorun beklediği yapıdan daha erken bir düşüş fikri; giriş teyidi ve destek dikkate alınmalı |
| ZEC SHORT | 4 saatte -%6,55; hacim 3,23 kat; yerel stop %6,06 | Önceki yükseliş bacağı %10'dan küçük ve daha alçak saatlik tepe yok | Düşüş var ama yakın destek yalnızca %1,50 uzakta; kullanıcının büyük hareket hedefi için temiz örnek değil |
| ZIL SHORT | Referans 0,002549; kırılma 0,002524; stop %2,21 | Öncesinde en az %10 yükseliş şartı yok | Genel düşüş devamı mevcut SHORT modelinin kapsamı dışında; bilinmeyen alt destek kazanç garantisi değil |
| BOME SHORT | Fiyat kırılma seviyesinde; henüz aşağı kırılma teyidi yok | Öncesinde en az %10 yükseliş şartı yok | İzlenebilir, fakat sırf düşüyor diye hazır işlem sayılmaz |
| AGT LONG | Yükseliş sürüyor ama son saat hacmi normalin 0,79 katı | Saatlik stop yaklaşık %14,49 uzakta | Bu kesitte eleme gerekçesi makul; yön doğru olsa bile giriş uygun olmayabilir |
| ICNT LONG | Güçlü yükselmiş, fakat yerel stop yaklaşık %9,94 ve kırılma seviyesi uzakta | Tepe çok yeni; erken hareket için günlük yükseliş fazla | Yükselişi kovalamak riskli |
| ZEREBRO SHORT | 4 saatte -%16,54; yerel stop %25,47, saatlik stop %66,64 | Stop çok uzak | Büyük düşmüş olması yeni SHORT girişini iyi yapmıyor; eleme makul |

### Sonraki 15 dakika ne gösterdi?

20:45 kapanışında BASED 0,05447'ye yükseldi; başlangıç referansına göre +%1,42. Aynı yerel stopla risk artık %9,36'ydı. INJ 8,761'e (+%1,33), ZEC 1161,65'e (+%1,94), ZIL ve BOME de yukarı döndü: bu hareketler SHORT yönünün tersiydi. ICNT 0,1681'den 0,1572'ye yaklaşık %6,48 düştü.

Bunlar gerçekleşmiş işlem kârı/zararı değil; iki kapanış arasındaki fiyat farkıdır. Tetiklenmemiş adayları işlem yapılmış saymak yanlış olur. Kısa gözlem, bağımsız adayların da hatalı veya erken olabildiğini gösterir; 24–72 saatlik başarı sonucu yerine geçmez.

## 4. Kapsam ve gecikme

**SHORT kapsamı dar:** motor genel düşüş devamını aramıyor; önce en az %10 yükselmiş, sonra saatlik yapısı bozulmuş coinleri arıyor. ZIL/BOME gibi genel zayıflama örnekleri bu yüzden kapsam dışında kalıyor. Bu bilinçli model sınırı olabilir; fakat kullanıcının bütün büyük düşüşleri yakalama hedefini karşılamıyor.

**LONG tarafında yeni zirve boşluğu var:** ilk hareket senaryosu günlük yükselişin %10'dan küçük olmasını istiyor. Devam senaryosu ise tepenin en az iki saatlik mum geride kalmasını ve fiyatın bu tepenin altında olmasını istiyor. Çoktan %10 yükselmiş, yeni zirve yapmayı sürdüren coin iki modelin arasında kalabiliyor. BASED/ICNT bunun örneği; her ikisinin de iyi giriş olduğu sonucu çıkmaz.

**Dakikalık tarama, dakikalık karar verisi demek değil:** canlı taramalar yaklaşık 60–62 saniyede bir. Ana yapı ve göreli güç son kapanmış saatlik mumdan geliyor; saat içinde yeni bilgi bu özelliklere henüz yansımamış olabilir. Saatlik teyitli dip/tepeler için iki sonraki mum da bekleniyor. 4 saatlik arka plan da kapanmış mumlardan oluşuyor.

Gönderim ayrıca yeni 15 dakikalık kapanışın ilk 90 saniyesinde olmayı, en çok 5 saniyelik fiyatı ve kırılma seviyesinden en çok yarım 15 dakikalık ATR uzaklaşmayı istiyor. Bunlar geç kalmış girişleri azaltmayı amaçlıyor; dar onay penceresiyle birlikte gerçek kaçırma oranı ayrıca ölçülmeli. İncelenen aralıkta bazı turlarda 0–31 coin taze fiyat şartından elendi; bunun belirli bir iyi işlemi kaçırdığı gösterilmedi.

## 5. Canlı çalışma, Telegram ve sonuç ölçümü

| Alan | Denetim bulgusu | Sınır |
|---|---|---|
| Süreçler | Beş ana çalışan ayakta, gözlenen süreç kimlikleri sabit | Sonsuza kadar hatasız çalışacağı anlamına gelmez |
| Kapsam | Genel beslemede yaklaşık 745 sembol; bağımsız aktif USDT sürekli vadeli liste 525; ayrıntılı likit tarama 152–154 coin | “Binlerce coin taranıyor” ifadesi doğru değil |
| Mum akışı | Yaklaşık 153 coin / 918 akış; gözlenen bekleyen yazım ve tekrar deneme sıfır | Bazı turdaki taze fiyat eksikliği ayrıca görülüyor |
| Eksik geçmiş | CTUSDT 1 Ekim 2026 10:45 TR listelenmiş; 180 adet 4 saatlik mum yaklaşık 30 gün gerektiriyor | Bu eksiklik sıradan indirme beklemesiyle tamamlanamaz; yeni coinler dışarıda kalır |
| RAM | Son ölçüm yaklaşık 402 MB; bir saatlik ortalama 732 MB, en yüksek 1,256 GB | Tek anlık süreç RSS'si toplam bellek değildir; burada bellek taşması kanıtı yok |
| Disk | Yaklaşık %22 kullanım; 12 veritabanı yedekleme başarılı | İncelenen ham veri/aday tablolarında genel yaşlandırma sınırı görülmedi; uzun vadeli büyüme izlenmeli |
| Telegram | Ayar mevcut; yayımdan önce taze fiyat, risk ve gecikme tekrar kontrol ediliyor | Yeni sürümde 20:53'e kadar doğrulanmış teslim olayı görülmedi. Gerçek uçtan uca yeni öneri teslimi henüz doğrulanmış değil |
| OI/funding/akış | Gözlemsel bağlam olarak kaydediliyor; yön seçme veya veto şartı değil | Eksik türev bilgisi karar kanıtı gibi gösterilmemeli |
| Sonuç izleme | 1 dakikalık mumlarla yön, en iyi/en kötü hareket, önce stop/hedef ve stop sonrası devam ayrı tutuluyor | Kod ve test doğrulaması var; yeni sürümün gerçek teslim edilmiş sonuç örneklemi yok |

Telegram bildirimi/sonuç işlemleri aynı sinyal çalışanında senkron yürütülüyor. Ağ zaman aşımı altında sonraki taramayı geciktirme potansiyeli var; mevcut kayıtlarda sürekli böyle bir aksama görülmedi. Başarılı gönderimden sonra alınan fiyatın mesajdaki giriş referansından farklı olabileceği de ölçümde ayrı tutuluyor. Bunlar otomatik gerçekleşmiş emir fiyatı değildir.

Sonuç motoru stop sonrası hareketi takip ediyor; “sonunda yön doğru çıktı” ile “önce stop oldu” aynı sonuç sayılmıyor. Aynı dakikada olay sırası belirsizse belirsizlik tutuluyor. Hesaplanan net referans getiride sabit %0,30 maliyet varsayımı var; gerçek spread, komisyon kademesi, kayma, funding ve kaldıraçlı hesap getirisi eksiksiz modellenmiyor. Mevcut kanıtlarla gerçek kazanç oranı veya beklenen kâr ilan edilemez.

## Düzeltme önceliği

1. WATCH için planlanan girişteki risk/engel hesabını eklemek; “şimdiki fiyat uygun ama beklenen giriş uygun değil” durumunu görünür yapmak.
2. Kullanıcının hedefini ayrı, açık senaryolarla kapsamak: yeni zirvede devam eden LONG; yükseliş sonrası bozulan SHORT; genel düşüşün devamı SHORT. Bunları tek kapının eşiklerini rastgele gevşeterek birleştirmemek.
3. Her senaryoda yapı, giriş ve stop zaman dilimini uyumlu belirlemek. Büyük hareket hedefini “en yakın her tarihsel engel mutlaka %10 uzakta olsun” şartıyla özdeşleştirmeden, engelin gerçekten aşılıp aşılamadığını ayrı incelemek. Bu değişikliklerin daha iyi olduğu henüz kanıtlanmış değil.
4. Mevcut sürüm ve araştırma sürümünü aynı anda, emir vermeden ve zaman damgalı kayıtlarla karşılaştırmak. Aday olmayan coinleri de tutarak kaçan büyük hareketleri ölçmek. Sonraki dönemi kural seçimi için kullanmamak; maliyetli net sonuç, stop-first ve stop sonrası toparlanmayı birlikte raporlamak.

Sırf mesaj sayısı artsın diye stop veya mesafe sınırını kaldırmak önerilmiyor. Öncelik daha çok mesaj değil, hangi kuralların gerçekten işe yaradığının ölçülebilmesi.

## Kanıt ve yeniden çalıştırma

Ham veri Binance'in herkese açık `https://www.binance.com/fapi/v1/ticker/24hr`, `exchangeInfo` ve `klines` yanıtlarından alındı. Mum dosyalarında istek/yanıt zamanı, kaynak URL ve alınan yanıt özeti bulunur. Ortak kapanış kesiti kullanıldı; sonradan indirilen veri, o anda sistemin elinde olduğuna kanıt değildir.

Kanıt paketinde `data/market` (462 yanıt), seçilmiş ikinci kesit `data/current`, piyasa listesi, karşılaştırmalar, 24 saatlik tekrar, canlı tarama kayıtları, test çıktısı ve grafik bulunur. Kod denetim dalındadır. Arşiv açıldıktan sonra depo kökünde:

```bash
MAJOR_AUDIT_DATA_DIR=/absolute/path/to/data python audits/compare_major_snapshot.py
MAJOR_AUDIT_DATA_DIR=/absolute/path/to/data python audits/replay_major_audit.py
```

Bu komutlar saklanan verileri kullanır; canlı servise, Telegram'a veya emir sistemine yazmaz. Yeniden veri indirmek aynı zaman kesitini yeniden üretmez. `manifest.json` paket dosyalarının SHA-256 özetlerini içerir.
