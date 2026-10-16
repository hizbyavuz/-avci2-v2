# Sinyal anındaki veri ile sonraki yönü karşılaştırma

Bu ekleme ana büyük hareket seçim kurallarını, eşiklerini ve sıralamasını değiştirmez. OI, funding veya alış/satış baskısı bir gönderim vetosu veya gizli puan değildir. Yeni sürekli görev açılmaz; mevcut motorun içinde yerel veritabanı okunur. Ağdan ek veri sorgusu yapılmaz.

Her gönderim girişiminden önce mevcut taze türev verisinin ham kopyası, zaman damgaları, bir saatlik OI değişimi, aynı saat aralığındaki fiyat değişimi, funding ve long/short hesap oranı kaydedilir. Son saatlik alış/satış baskısı da saklanır. Telegram kabulü olmayan gönderimler ana performans raporuna girmez. WATCH/READY adaylarının 15 dakikalık gözlem kayıtları da korunur; bunlar ayrı aday gözlemleridir ve teslim edilmiş sinyal sayılmaz.

`context_snapshots` tablosu yeni kaydı ekler, eski kaydı değiştirmez. Daha sonra gelen OI/funding verisi geçmiş bir sinyale eklenmez. Eski sinyaller `LEGACY_NO_CONTEXT` olarak ayrı tutulur. Bozuk, eski, başka piyasadan veya geleceğe ait veriler eksik olarak işaretlenir; sıfır ya da destek sayılmaz.

## Neyi karşılaştırır?

| Ayrım | Kesin tanım |
|---|---|
| Akış destekliyor | LONG için alış payı >%50 ve aynı saatlik fiyat getirisi >0; SHORT için ikisi ters yönde |
| Akış çelişiyor | Baskı ile fiyatın yönü birbiriyle çelişiyor |
| Akış ve fiyat ters | Baskı da saatlik fiyat hareketi de önerilen yöne ters |
| Nötr / eksik | Sıfır hareket, %50 pay veya doğrulanamayan veri ayrı tutulur |
| OI–fiyat | Açık sözleşmeler artıyor/azalıyor/değişmiyor × fiyat sinyal yönünde/tersinde/yatay |
| Funding | Sinyalin yönü için dönemsel ödeme maliyeti/alacağı/sıfır/eksik |

Akış uyumu gözlemsel sınıflamadır; tahmin başarısı değildir. OI artışı tek başına LONG, OI düşüşü tek başına zayıflık sayılmaz. Pozisyon kapanması, short squeeze veya yeni long açılması kesin teşhis edilmez. Long/short hesap oranı pozisyon büyüklüğünü veya para akışını gösteriyormuş gibi kullanılmaz. Funding için geçmişe dayalı aşırılık iddiası kurulmaz.

## Sonraki yön ve getiri

Bildirim kabulünden sonra 15 dk, 30 dk, 1 sa, 3 sa, 6 sa, 24 sa ve 72 sa ölçülür. Her vadede giriş referansı, sonraki fiyat, gerçek yukarı/aşağı hareket, önerilen yöne uyum, en büyük lehte/aleyhte hareket, %10–100 eşikleri, stop ve plan kapanışı ayrı alanlardır. Ana önceden ilan edilen değerlendirme 24 saat olarak kalır; kısa vadeler yardımcı gözlemdir.

Örneğin LONG önerisi önce stop olup sonra yükseldiyse yön hanesi doğru olabilir; plan hanesi kayıptır. Stop sonrası büyük hareketlere erişim `post_stop_milestones` içinde ayrıca bulunur. Aynı dakikadaki sıra bilinmiyorsa stop sonrası diye kesin işaretlenmez. Net getiri önceki %0,30 toplam masraf varsayımını korur; gerçekleşmiş hesap getirisi değildir.

Eksik veri ve ilk kısmi dakikadaki belirsizlik ayrı sayılır. Sonuç süresi henüz dolmamış kayıtlar başarı oranına katılmaz. Yönün doğru olması ile kapanmış planın pozitif olması farklı oranlardır. Açık bir planın vade sonundaki varsayımsal getirisi ayrıca `mean_reference_net_pct` alanıdır; kapandığı iddia edilmez.

## Sonradan sorgulama

Motor saat başında son 30 günlük 1/24/72 saat karşılaştırmasını `MAJOR_CONTEXT_COMPARISON` loguyla verir. Bütün vadelerin raporu aynı anda `major_context_comparison.json` dosyasına atomik yazılır. Ham kayıtlar SQLite'ta saklandığından özel tarih aralığı yeniden hesaplanabilir:

```bash
python long_short_major_context_report.py --since 2026-10-16T19:30:00+03:00 --hours 1
python long_short_major_context_report.py --since 2026-10-16T19:30:00+03:00 --details
```

İlk komut gruplara göre kaç sinyal, kaç ölçülebilir yön, doğru/ters yön, %10 erişim, kaç kapanmış plan ve ortalama varsayımsal net sonucu verir. İkinci komut her coini tek tek gösterir. Rapor salt okunurdur, mesaj veya emir göndermez.

Her raporda örnek sayısı, ayrı coin sayısı ve yön oranı için Wilson aralığı bulunur. Model/yön kırılımı ayrı verilir; farklı strateji ve gözlem sürümleri birleştirilmez. Küçük örnekler, sinyal kümelenmesi ve farklı piyasa koşulları nedeniyle grup farkı tek başına nedensel katkı kanıtı değildir. Bu gözlem yeni bir eleme eşiğini otomatik devreye almaz.

## İşletim

Beş sürekli görev korunur. Gözlem kayıtları mevcut `major_move_v1.db` dosyasında olduğundan mevcut yedek envanterine dahildir. Rapor SQLite satırlarını akış halinde işler; tüm tarihçe RAM'e yüklenmez. Kod sürümü, gözlem kurallarının hash'i ve kurulum müdahalesi `manifests` tablosuna kaydedilir. Eski kayıtlar veya sonuçlar silinmez.

Yerel doğrulamada 112 test geçti. Bunlar veri zamanları/kaynakları, sonradan kayıt değiştirmeme, OI'nin yön oyu olmaması, eksik veriyle sinyali engellememe, kısa vadeli yön, stop sonrası toparlanma, grup raporu, mesaj tekrar koruması, yedek ve veri akışı kontrollerini kapsar. Ana seçim dosyası ve beş görevli profil önceki hazır sürümle birebir aynıdır. Testler gerçek Telegram'a mesaj göndermez ve kârlılık kanıtı değildir.
