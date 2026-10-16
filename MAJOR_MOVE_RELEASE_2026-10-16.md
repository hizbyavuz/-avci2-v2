# Büyük hareket motoru — 16 Ekim 2026

Amaç: Telegram önerilerini küçük yerel fiyat kırılımlarından, büyük hareketin başlangıcı/devamı veya büyük yükselişin bozulması hipotezlerine taşımak. Bu sürümün kârlılığı henüz doğrulanmadı. Otomatik emir vermez.

## Değişen seçim

| Durum | Aranan kanıt |
|---|---|
| Başlangıç LONG | Saatlik sıkışma, normalin en az 1,5 katı hacim, BTC karşısında dört saatte en az 2 puan güç, kutu üstü kapanış |
| Devam LONG | Önceki büyük bacak en az %10; yükselişin en az yarısı korunmuş; yükselen saatlik dip ve yeniden güçlenme |
| Bozulma SHORT | Önceki büyük bacak en az %10; daha düşük saatlik tepe; teyitli saatlik dip altında kapanış; BTC karşısında zayıflık |

Çok yükselmiş olması tek başına SHORT nedeni değildir. Yeni coin/eksik geçmiş, eski fiyat ve eksik mumlar işlem adayı üretmez. En az 30 günlük dört saatlik geçmiş aranır; bu nedenle çok yeni listelenen coinler kapsam dışındadır. Evren, doğrulanmış Binance USD-M USDT sözleşmeleri ve en az 8 milyon USDT günlük işlem hacmiyle sınırlıdır.

Giriş için taze 15 dakikalık kapanış, kırılan seviyenin korunması ve geç kalınmamış fiyat gerekir. Yapısal stop uzaklığı en fazla %8'dir. Dört saatlik teyitli ilk engel %10'dan yakınsa aday elenir. Engel bulunamaması %10 hareket olacağı anlamına gelmez. Hacim ve BTC karşısında güç adayları sıralar; son saatlik alış/satış akışı mesajda destek veya çelişki olarak gösterilir. OI, funding veya haber kaynaklı bir neden doğrulanmış gibi sunulmaz.

%10 fiyat tahmini veya otomatik çıkış değildir. İlk ölçüm eşiğidir. Eşik görüldükten sonra tamamlanmış ve tamamen bildirim sonrasına ait UTC saatlik mumun dibi/tepesi, ilk saatlik ATR'nin %15'i kadar payla takip eden stopu belirler. Stop yalnızca sıkılaşır ve sonraki dakikadan itibaren geçerlidir. En fazla 72 saat takip edilir. Hareketin %20–100 eşiklerine ulaşıp ulaşmadığı ayrıca kaydedilir. Her mesajda aynı getiriyi beklemek veya garanti etmek mümkün değildir.

## Mesaj ve sonuç kaydı

- Yalnızca giriş koşulları tamamlanan aday bildirilir. Aynı coinde 24 saat bekleme; en fazla 12 açık plan; tarama başına en fazla 3 yeni mesaj vardır.
- Stop değişimi ve plan kapanışı aynı Telegram kartını günceller. Daha yeni bir önerisi olan coinde eski öneri yeniden gönderilmez.
- Bildirimden önceki yükseliş kazanç sayılmaz. Ölçüm başlangıcı Telegram kabulünden sonraki taze referans fiyattır; bu fiyat yoksa sonuç belirsizdir.
- Her sinyal için 6/24/72 saat fiyat yönü, lehte/aleyhte hareket, ilk stop/eşik sırası, büyük hareket eşikleri ve varsayımsal plan sonucu ayrı tutulur.
- Stop olmuş planın sonucu sabittir. Sonradan %30 yükselmesi planı kazanan yapmaz; stop sonrası yol 72 saate kadar ayrıca izlenir.
- Bir dakikalık OHLC kullanılır. İlk kısmi dakikada stop ihtimali varsa net sonuç belirsiz kalır. Aynı dakikada stop/eşik görülürse sıralama belirsizliği işaretlenir. Boşluk üzerinden atlanmaz; veri tamamlanana kadar sonuç bekler. 96 saat sonunda onarılamamış boşluklar ayrıca saklanır.
- Net sonuç toplam %0,30 masraf varsayımıdır; gerçek hesap PnL'si değildir. Funding, kişiye özel komisyon, kayma ve emir gerçekleşmesi tam simüle edilmez. Eşit ağırlıklı sinyal sonuçları bir portföy getirisi olarak sunulmamalıdır.
- Yeniden başlatmada yarım kalmış gönderim tekrar denenmez; teslim durumu belirsiz olarak korunur.

## Kaynak kullanımı

Yedi sürekli görev beşe düşer: evren, mum/fiyat, mevcut native türev veri servisi, geçmiş onarımı ve yeni motor. Eski chart motoru, ayrı eski sonuç işçisi ve işlem bazında taker toplayıcısı çalışmaz. Saatlik alış/satış verisi mevcut mumlardan okunur. Aynı eski arşivleme işleri her başlangıçta tekrar koşmaz. Mevcut veri ve yedekler silinmez.

SQLite bağlantı önbellekleri 2 MiB; sonuç işi geçiş başına en fazla 24 kayıt ve kayıt başına 240 mum okur. Mum yazma kuyruğu 5.000 kayıtta yeni veri alımına geri basınç uygular; veritabanı kilidi çözülene kadar belleği büyütmez. Arşiv iş kuyruğu da sınırlıdır. RAM ölçümünde süreç RSS, dosya önbelleği ve container toplamı ayrı loglanır. Kör bir sert RAM limitiyle süreç öldürülmez.

Değişiklik öncesi Railway ölçümü: son 6 saat ortalama RAM 0,919 GB; son örnek 1,243 GB; tepe 2,297 GB. Yardımcı geçmiş servisi yaklaşık 0,032 GB. Bunlar yeni sürümün tasarruf sonucu değildir; canlı karşılaştırmanın başlangıç değerleridir.

## Doğrulama ve geri dönüş

91 yerel test geçti: gerçek OHLC'den aday üretimi, yanlış SHORT engeli, stop sonrası ralli, kısmi dakika ve veri boşluğu, takip eden stop, tekrar bildirim, eski Telegram taşıyıcısı, veri akışı, yedek bütünlüğü ve bellek geri basıncı. Ağ/Telegram çağrıları testlerde sahteleştirildi. Bu testler teknik davranışı doğrular; stratejinin para kazandırdığını kanıtlamaz.

Kod ve yapılandırma hash'i ile sürüm ayrılır. Tüm uygun evrenin 15 dakikalık anlık görüntüsü ve reddedilen adayların nedenleri de saklanır; sonraki karşılaştırma yalnızca başarılı coinlerden kurulmaz. Eşikler bu sürümde açıkça tanımlanmış araştırma varsayımlarıdır; yeni dönemin verisinde değerlendirilmelidir.

Geri dönüş: önceki sabit commit `da8bb84dca5cb17092374c5d9a6e2c244c82a6a2`. Alternatif olarak bu sürümde `LS_MAJOR_MOVE_V1=0` eski yedi görev profilini seçer. Eski açık episode kayıtları geçişte süresi dolmuş olarak kapatılır; eski değerlendirme ve sonuçlar korunur. Yeni motorun dosyası `major_move_v1.db` günlük yedek envanterine dahil edilir.
