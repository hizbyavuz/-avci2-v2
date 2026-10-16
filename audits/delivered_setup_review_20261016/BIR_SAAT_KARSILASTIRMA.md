# Bir saatlik mesaj karşılaştırması

16 Ekim 2026, saat 18.03 Türkiye saati itibarıyla mevcut sürümün geçmiş kayıtları. Bu inceleme sinyal kurallarını değiştirmedi.

**Somut ipucu bulundu: dar aralıktan kırılanlar, yalnızca yerel bir seviyeyi kıranlardan daha iyi yön seçmiş. Ancak daha iyi görünen grubun bile ortalama net sonucu negatif. Kanıtlanmış, kazandıran bir kural bulunmuş değil.**

## Gerçekte ne olmuş?

127 ayrı giriş fikri, 68 coin üzerinden incelendi. Bir saat sonunda 48 doğru, 78 ters, 1 aynı fiyat. Yalnızca son fiyata bakmak bazı hareketleri yanlış sınıflandırıyor:

- Ters kalan 78 mesajın 17'sinde önce mesajdaki hedefe ulaşılmış, sonra fiyat geri dönmüş.
- Doğru kalan 48 mesajın 3'ünde önce stopa ulaşılmış, sonra fiyat toparlanmış.
- 127 mesajın tamamı hem 15 hem 60 dakikada karşılaştırıldı: 15. dakikada doğru olan 63 mesajın 29'u bir saat sonunda ters kalmış; 34'ü doğru kalmış. İlk 15 dakikada ters olanlardan 14'ü sonradan doğruya dönmüş. Bu, tek başına 15. dakikada çıkmanın kârlı olacağını göstermez.

Hedef/stop sırası ve süre sonunda çıkış hesabı: 49 hedef, 39 stop, 37 süre sonu, 2 ilk dakika belirsizliği. Hedefe ulaşmak da her durumda maliyet sonrası kâr demek değil: bir FIL kaydında gönderim fiyatıyla hedef arasındaki alan varsayılan maliyetin çok az altında kalmış.

125 hesaplanabilir sonuçta 56 artı, 69 eksi var. Ortalama artı **%0,751**, ortalama eksi **%1,495 kayıp**; toplam örneklemde ortalama net sonuç **%-0,489**. Her sinyale eşit tutar varsayımıyla birkaç büyük kayıp küçük kazançları siliyor. Stop mesafesine göre normalize edilen ortalama sonuç da negatif: **-0,291 R**. Bunlar %0,30 toplam maliyet varsayımıyla referans hesaplar; kullanıcının gerçek işlemleri veya hesabı değil.

## Doğru ve ters kalanların ortak özellikleri

Her satır, mesaj gönderildiğinde kaydedilmiş verilerden oluşturuldu. Sonraki fiyatlar yalnızca sonucu belirliyor. Aşağıdaki grupların bazıları örtüşür; tablo satırları toplanmaz.

| Mesaj anındaki özellik | Mesaj | Bir saat sonunda doğru | Doğru oranı | Ortalama net referans sonuç |
|---|---:|---:|---:|---:|
| Dar aralıkta sıkışma sonrası kırılım | 29 | 17 | %59 | %-0,303 |
| Yerel bir seviyenin kırılması | 66 | 20 | %30 | %-0,522 |
| Geri çekilmeden sonra devam fikri | 27 | 9 | %33 | %-0,534 |
| Yapısal tepe/dip kırılımı | 5 | 2 | %40 | %-0,902 |
| Son 15 dk hacmi önceki 20 mum ortalamasından düşük | 86 | 37 | %43 | %-0,136 |
| Son 15 dk hacmi bu ortalamaya eşit/yüksek | 41 | 11 | %27 | %-1,212 |
| Günlük yönle aynı | 57 | 23 | %40 | %-0,652 |
| Günlük yöne ters | 62 | 21 | %34 | %-0,317 |
| Saatlik alış/satış baskısı ve fiyat birlikte destekliyor | 90 | 35 | %39 | %-0,407 |
| Bu ikisi birlikte desteklemiyor | 37 | 13 | %35 | %-0,684 |

Sıkışma grubundaki yön farkı LONG ve SHORT tarafında ayrı ayrı da görülüyor. Tek bir coini veya üç saatlik bir zaman bloğunu çıkardığımızda da işaret değişmiyor. Bunlar aynı veri üzerinde hassasiyet kontrolleridir; bağımsız doğrulama değildir. Coinlere eşit ağırlık verildiğinde de bu grubun ortalama net sonucu negatif kalıyor.

Yüksek hacim bu örneklerde daha iyi sonuç vermemiş. Bu bulgu “düşük hacim seçelim” kuralı değildir. Hacmin hareket başlarken mi, hareket uzadıktan sonra mı arttığını ayrıca ölçmek gerekir. Beş dakikalık hacim, açık pozisyon değişimi ve alım/satım desteği de tek başına güvenilir bir ayrım göstermedi.

## Kod neden bu mesajları seçmiş?

Motor yönü ağırlıkla bir saatlik grafiğin tepe/dip yapısından seçiyor. Ardından beş dakikalık yerel seviye kırılımını kontrol ediyor. Günlük yön ve 15 dakikalık terslik çoğu durumda uyarı; dört saatlik yöne ters fikirlerde ayrıca saatlik akış teyidi var.

“Yerel seviye” modeli, sıkışma veya belirli bir geri çekilme şekli gerektirmiyor. Önceki kapanışın aşmadığı seviyenin yeni kapanışta geçilmesi, güncel fiyatın doğru tarafta olması ve diğer kontrollerin geçilmesi giriş için yeterli olabiliyor. Bu nedenle bütün giriş mesajları aynı güçte bir grafik oluşumuna dayanmıyor. Zayıf devam sinyalleri seçilmesi olası açıklamadır; gözlemsel fark tek başına sebep-sonuç kanıtı değildir.

“Hep geç kaldık” açıklaması da şu an kanıtlanmadı. İzle mesajıyla eşleştirilebilen ve iki fiyatı da bulunan örneklerde, girişe kadar gerçekleşmiş yönlü hareketin ortancası doğru çıkanlarda %0,79; ters kalanlarda %0,54. Son saatlik hareket ve kırılım seviyesinden uzaklık da ters kalanlarda daha yüksek çıkmadı. Bu, geç kalma sorunu olmadığı anlamına gelmez; mevcut kaba ölçüler iddiayı doğrulamıyor.

“Büyük resim” sorusu açık: bu dışa aktarımda üst grafiklerin yön etiketleri var; fiyatın dört saatlik/günlük aralık içindeki yeri, son büyük hareketin başlangıcı, hacmin birkaç günlük seyri ve eşzamanlı BTC/sektör karşılaştırması yok. Bunların geçmişteki gerçek değerleri ham mumlardan, yalnızca mesaj anında bilinen verilerle yeniden hesaplanmalı. Sonradan oluşan tepe/dipler geçmiş sinyale taşınmamalı.

## Hangi veriye, hangi soru için bakacağız?

| Soru | Gerekli kayıt | Bu incelemenin cevabı |
|---|---|---|
| Kırılım öncesinde fiyat gerçekten hazırlanmış mı? | Sıkışma, geri çekilme yapısı, seviye yaşı | Sıkışma grubu araştırılacak aday; henüz kârlı değil. |
| Fiyat doğru gittikten sonra ne zaman bozuluyor? | Dakikalık yol, kırılan seviyeye dönüş, hedef/stop sırası ve süre | 17 ters kapanış önce hedefe gitmiş; 29 mesaj 15 dk doğruyken 60 dk ters. |
| Küçük kazanca karşı ne kadar kayıp kabul edilmiş? | Mesaj anındaki hedef/stop uzaklığı, maliyet, gerçekleşen kayıp ve R | Kayıpların ortalaması kazançların yaklaşık iki katı. Sadece 1:1 filtre eklemek bu örneklerde çözüm göstermiyor. |
| Hareketin başında mıyız, sonunda mı? | Önceki hareketin büyüklüğü ve yaşı, 4 saat/günlük aralıktaki konum, hacim seyri | Yalnızca “günlük yukarı/aşağı” etiketi soruyu cevaplamıyor. Ayrıntılı geçmiş hesap burada henüz yok. |
| Coin kendi gücüyle mi, piyasa ile mi hareket ediyor? | Aynı zamanlı BTC, benzer coinler ve piyasa geneli | Bu dışa aktarımda ölçülmedi; neden atfetmek için eksik. |

İlk araştırılacak konu, sıkışma kırılımını daha basit yerel seviye fikrinden ayırıp aynı maliyet ve çıkış hesabıyla karşılaştırmak. Giriş seçimi ve çıkış/risk değişikliği ayrı sınanmalı; birlikte değiştirilirse iyileşmenin nereden geldiği anlaşılmaz. Yeni bir kural seçilmeden önce ölçütleri yazılmalı ve sonraki, henüz görülmemiş mesajlarda doğrulanmalı. Bu dosya hiçbir filtreyi veya eşiği canlıya almaz.

## Örnekler

- **SAND LONG, 15 Ekim 23.05:** referans fiyat 0,05647; hedef 0,05685. Yaklaşık 14. dakikada hedefe ulaşmış, bir saat sonunda mesaj fiyatının ters tarafında kalmış. “Bir saat sonra ters” demek bu hareketin hiç çalışmadığı anlamına gelmiyor.
- **STBL LONG, 16 Ekim 04.10:** sıkışma modeli; referans fiyat 0,03617, stop 0,0312343. Yaklaşık 14. dakikada stop, varsayılan maliyet sonrası %-13,95. İyi görünen giriş sınıfında bile kayıp büyüklüğü ayrıca ele alınmalı.
- **BTW LONG, 16 Ekim 09.00:** yerel seviye modeli; referans fiyat 2,0393, hedef 2,0615, stop 1,8634886. Bir saat içinde hedef veya stopa değmeden süre sonu referans sonucu %-6,12. Stopa ulaşmamış olmak düşük kayıp anlamına gelmiyor.

## Kapsam ve tekrarlama

138 mevcut-sürüm giriş mesajının 127'sinde bir saatlik fiyat sonucu var; 9'unun süresi henüz dolmamış, 2 olgun kayıt dışa aktarımda yok. Bunlar başarısız sayılmadı. Eşleştirilen 127 kaydın 2'sinde ilk dakika hedef/stop sırası belirsiz; net ana hesaba alınmadı. Aynı coinler ve aynı piyasa saatleri bağımsız örnekler değil. Birçok özelliğe aynı küçük örneklemde bakıldı; farklılıklar kalıcı avantaj kanıtı değil. Bir saat kullanıcının istediği ikincil inceleme süresi; dondurulmuş ana ölçüm üç saat olmaya devam ediyor.

Kaynak: Telegram ACK ile kaydedilmiş motor gönderimleri, bunlara bağlı yerel Binance Futures mumlarından hesaplanan sonuçlar; yeni bir Telegram geçmişi indirmesi veya gerçek kullanıcı işlem dökümü değil. Ham girdiler `one_hour_input.json`, tüm karşılaştırmalar `one_hour_comparison.json`. Tekrarlama: `python audits/delivered_setup_review_20261016/compare_one_hour.py`.
