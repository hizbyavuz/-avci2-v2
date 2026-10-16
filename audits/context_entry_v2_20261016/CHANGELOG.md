# MAJOR V2: büyük yapı ve giriş zamanı

Bu sürüm, 16 Ekim 2026 tarihli kullanıcının “Yap abi” talebiyle hazırlandı. Otomatik emir vermez. Önceki fikirleri, negatif sonuçları ve stop sonrası hareketleri silmez.

## Değişiklik

- Güncel fiyatın zamanı SQL okumasından sonra alınır. Okuma sırasında gelen güncel fiyat artık yanlışlıkla gelecek zamanlı sayılmaz. Gerçekten eski veya gelecek tarihli veri reddedilmeye devam eder; tazelik sınırı 5 saniye.
- Kapanmış 4 saatlik mumların EMA20 eğimi/fiyatı büyük yapıyı sınıflar. Kapanmış günlük mumlar bağlam olarak gösterilir. Güncel 4 saatlik getiri ve BTC göreli gücü, kapanmış 15 dakikalık mumlardan hesaplanır; yeni saatlik mum beklenmez.
- Dört senaryo: yükseliş başlangıcı, büyük yükselişin/yeni zirvenin devamı, yükseliş sonrası saatlik desteğin bozulması, genel düşüşün devamı. Genel düşüş için öncesinde yükseliş şartı yoktur. Başlangıç dışındaki devam senaryolarında geçmişte en az %10 hareket kanıtı aranır; gelecekte %10 hareket olacağı iddia edilmez.
- Giriş: önceki 8 kapalı 15 dakikalık mumun seviyesi kırılır veya son 4 mumdaki kırılma seviyesi yeniden korunur. Yeniden testte ilk kırılmanın seviyesi sabit tutulur. Kırılmadan sonraki kapanışlar seviyeyi korumalıdır. Gerçekleşmemiş mumlar kullanılmaz.
- Yakındaki kırılmamış 4 saatlik engel (bir ATR15 içinde), aşılması gereken giriş seviyesine dönüşür. Diğer engellerin mesafesi kaydedilir. Sabit “en az %10 boşluk” vetosu kaldırılır. Yeni zorunlu risk/getiri oranı eklenmez; kullanıcının önceki kararı korunur.
- Stop, en son uygun ve doğrulanmış 15 dakikalık salınım + 0,15 ATR15 tamponudur. Salınım yoksa yerel mum aralığı kullanılır. Onay mumunun dip/tepesi stopun dışında bırakılmaz. Stop mesafesi %8 sınırını aşıyorsa fiyatı zorla yakınlaştırmak yerine aday reddedilir.
- Aday sonraki 15 dakikalık kapanışa kadar değerlendirilebilir. Gönderimde en güncel fiyat, kırılan seviyenin korunması, en fazla 0,5 ATR15 takip mesafesi, stop, engel, 24 saat coin beklemesi ve 12 aktif fikir sınırı yeniden kontrol edilir. Her denemede en çok 3 gönderim.
- 2 saniyede bir endeksli kapanış işaretçisi kontrol edilir; bütün mumlar sadece yeni kapanışta veya eksik veri için 10 saniyelik yeniden denemede okunur. Bekleyen giriş fiyatları 5 saniyede bir yeniden kontrol edilir. Bu aralıklar kesin uçtan uca gecikme garantisi değildir: veri gelişi, SQLite ve Telegram süresi eklenir.
- Ölçüm ve bildirim güncellemeleri giriş taramasından sonra yapılır. Güncelleme kuyruğundan tur başına en fazla bir kart işlenir. Mevcut 5 işçi ve aynı servis kullanılır; coin başına en fazla bir bekleyen aday bellekte tutulur.

## Eski/yeni karşılaştırması

V1.1 karar modülü `long_short_major_move_v1_1_frozen.py` içinde değişmeden korunur. `BASELINE_ID`, gerçekten çalışan d5b750b sürümünün kimliğidir. Eski ve yeni kararlar aynı kapanmış mumlar, fiyat, hacim ve zamanla kaydedilir.

`research_ideas` / `research_outcomes` ileriye dönük, gönderilmemiş varsayımsal referanslardır. `ideas` / `outcomes` gerçek Telegram onaylı kayıtlar olarak ayrı kalır. Araştırma kayıtları her strateji/coin için 24 saat aralıklıdır; portföy kapasitesi sınırı uygulanmaz. Bu nedenle araştırma getirileri gerçek portföy getirisi değildir. OI, funding ve alış/satış baskısı karar oyu olmadan sınıflandırılıp sinyal anında dondurulur; eksik olanlar açıkça eksik sayılır.

Her iki kayıtta 15 dakika, 30 dakika, 1, 3, 6, 24 ve 72 saatlik yön, lehte/aleyhte azami hareket, stop, stop sonrası devam ve masraflı varsayımsal sonuç ölçülür. Birincil karşılaştırma 24 saattir. Aynı dakikada stop ve hedef görülürse muhafazakâr sıra korunur; belirsiz ilk dakika ve eksik veri ayrı etiketlenir. %10 ölçüm eşiğidir, otomatik çıkış/fiyat tahmini değildir.

Araştırma raporu: `python long_short_major_context_report.py --research --hours 24`. Mevcut Telegram raporu aynı komutun `--research` olmayan biçimidir. Saatlik `MAJOR_PAIRED_COMPARISON` günlükleri ve `major_paired_comparison.json` araştırma sonuçlarını gösterir. Eksik karşılaştırma grubu, sıfır başarı oranı demek değildir.

## Doğrulama

- 74 test geçti: eski model regresyonları; dört yeni senaryo; kırılma/yeniden test; eksik, eski ve gelecek veriler; fiyat zamanlaması; mükerrer mesaj engeli; gerçek/araştırma kayıtlarının ayrılığı; masraf, stop ve stop sonrası takip; çalışma profili.
- Aynı arşiv günü: 154 coin × 97 kapanış = 14.938 değerlendirme. Eski V1.1: 0 READY, 4 WATCH. V2: 252 READY, 1.429 WATCH. Coin başına ilk aday alındığında 77 coin: 25 LONG, 52 SHORT. İlk olayların 60'ı kırılma, 17'si yeniden test.
- İlk olaylar: 2 başlangıç LONG, 23 devam LONG, 15 yükseliş sonrası SHORT, 37 düşüş devamı SHORT.
- Tam SQLite akışı, evren filtresinden geçen 153 coinle yerelde çalıştırıldı. İlk tarama 776 ms, aynı mumdaki 20 boş kontrol ortalaması 0,72 ms. Yerel süreç tepe belleği yaklaşık 27 MB. Sahte yayıncıyla 3 onaylı kayıt, sıfır tekrar ve 14 ayrı araştırma referansı oluştu. Bunlar Railway RAM/gecikme veya gerçek Telegram teslim ölçümü değildir.
- Arşiv günü tasarımda zaten görüldü; mevcut likidite evreni ve kapanış fiyatları kullanıldı. Bu bir kapsam/işleyiş kontrolüdür. Kazanç, kazanma oranı veya üstünlük doğrulaması değildir. Eşikler bu sayılara göre tekrar optimize edilmedi.

Yeniden çalıştırma: `audits/compare_context_entry_v2.py` ve `audits/smoke_context_entry_v2.py` için `--data-dir <arşiv> --output <json>`. Arşiv dosyalarının SHA256 değerleri comparison.json içindedir. Gerçek canlı doğrulama dağıtım sonrasında ayrıca kaydedilmelidir.

## Geri dönüş

Önceki kaynak: `hizbyavuz/-avci2-v2`, `fix/major-planned-entry-2026-10-16`, commit `d5b750b88e24333c2bd3853ff0c6e6f564abfd47`. Yeni tablo ekleri eski kodu bozmaz. Eski kaynağa dönüş veri silmeyi gerektirmez.
