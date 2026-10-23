# Grafik ve Fibonacci araştırma motoru — 23 Ekim 2026

Durum: yeni kod yazıldı ve yerel yazılım kontrollerinden geçti. **Canlıya alınmadı,
Telegram kabulü doğrulanmadı, kârlılık gösterilmedi.** Bu belge çalışan canlı
servis veya eksiksiz piyasa verisi iddiası değildir.

## Değişen davranış

Mevcut mum, piyasa kataloğu, fiyat ve türev veri toplayıcıları kullanılır. Eski
MAJOR/SWING karar ve sonuç kayıtları silinmez. Yeni karar ve ölçüm, ayrı
`pattern_structure_v1.db` içinde sürümlenir. Yeni açık profil
`PATTERN_STRUCTURE_V1`; başlangıç `long_short_pattern_daemon.py`.

LONG ve SHORT için fiyat yapısı ana karardır. İlk kırılım ve teyitli geri test
ayrı giriş türleridir. Yatay aralık; yükselen/alçalan/simetrik üçgen;
yükselen/düşen kama; bayrak/flama; çift dip/tepe; omuz-baş-omuz ve tersi için
kodlanmış geometrik hipotezler vardır. Bunlar 12 ayrı isimdir, 12 kanıtlanmış
strateji değildir. Pencereler 16/32/64 mum; pivotlar iki sağ mumla teyit edilir.
Çizgi uyumu, daralma ve dokunma toleransları ATR ile tanımlıdır. Bütün değerler
`long_short_patterns.CONFIG` ve kaynak koduyla dondurulur.

15dk ve 1s kapanmış mumlar ayrı kurulum aralıklarıdır; 4s yapı bağlamı ve
engelleri kullanılır. Her kurulum kendi zaman aralığının yapısal stopunu kullanır.
Bir yönde formasyon olması diğer yönde başarının kanıtı sayılmaz. Her coinde
aynı anda en fazla bir aktif plan; toplam en fazla 10 aktif plan ve kayan 24
saatte en fazla 10 plan bildirimi. Bu sınırlar portföy sermaye/risk yönetimi
veya günlük sinyal üretme hedefi değildir. Evren güncel doğrulanmış COIN türünde
USDT perpetual ve 24s hacmi en az 2M USDT; toplayıcının 166 sembol sınırı ve
dönüşümlü kapsamı korunur, eksik kapsam ayrıca bildirilir.

## Giriş, SL, TP ve Fibonacci

İlk kırılım önceki tamamlanmış yapının sınırından yeni kapanış gerektirir.
Aynı yapının daha önce kırılmış olması yeni kırılım sayılmaz. Geri test, en
fazla altı mum önceki kırılımın sabit sınırına bir önceki mumda dönüş ve
seviyenin korunmasını, ardından güncel mumun geri test mumunun yön tarafındaki
ucunun ötesinde kapanmasını gerektirir. Geri test stopu bu geri testin
geçersizliğini ölçer; ilk kırılımın geniş taban stopu sessizce daraltılmaz.
Geri testin eski kırılımı o an bilinen geçmiş mumlardan yeniden kurulur;
geçmişte gönderilmiş sinyal olarak yazılmaz.

Stop yapı pivotu / geri test ucu dışında 0,2 ATR tamponla konur. En yakın bilinen
engel atlanamaz. Hedef olarak formasyon boyu projeksiyonu kullanıldığında bunun
geçmişte doğrulanmış destek/direnç olmadığı mesajda açıkça belirtilir. En fazla
0,75 ATR kovalama, %12 fiyat riski, varsayılan maliyet sonrası en az 1,5R şartı
vardır. Bu başlangıç eşikleri optimize edilmiş veya istatistiksel olarak
kanıtlanmış değildir. Daha fazla mesaj için gevşetilmemiştir.

Yön işareti `s=+1` LONG, `s=-1` SHORT; giriş `E`, stop `S`, hedef `T`:

- Risk yüzdesi = `100 × s × (E−S) / E`
- Hedef mesafesi = `100 × s × (T−E) / E`
- Varsayımsal maliyet sonrası ödül/risk = `(hedef mesafesi−0,30) / (risk+0,30)`

Bu işlem başına sabit nominal ölçüdür, kaldıraçlı hesap getirisi değildir.
Gerçek kayma ve ücretler farklı olabilir. SL emir garantisi değildir; dakika
açılışı stopun ötesindeyse daha kötü açılış fiyatı kullanılır.

Fibonacci iki kronolojik teyitli pivot arasındaki yönlü bacağı kullanır.
%38,2 / %50 / %61,8 geri çekilme ve 1,272 / 1,618 projeksiyonları gösterilir.
%50 orta noktadır; Fibonacci oranı değildir. Uygun teyitli bacak yoksa seviye
üretilmez. Fibonacci, hacim, taker alış payı, OI, funding ve hesap oranı yön
puanı veya zorunlu onay değildir. Yardımcı veri eksikse açıkça belirtilir;
gelecekte ölçülmeden karar ağırlığı eklenmez.

## Bildirim ve sonuç sözleşmesi

Bildirim ancak `PATTERN_NOTIFICATIONS=1` ile ve mevcut doğrulanmış alıcı
ayarından yapılır. Alıcı çelişirse durur; rastgele Telegram güncellemesinden
alıcı seçmez. İlk mesaj bağlantı testi olarak etiketlenir. Başlangıç mesajına
Telegram ACK alınmazsa sinyal işçisi durur. Yeni plan, iptal, veri boşluğu ve
SL/TP/süre sonucu ayrı mesajlardır; sessiz kart düzenlemesine dayanılmaz.
Kalıcı outbox belirsiz teslimi tekrar göndermez. ACK sonrası plan güncellemesi
öncesinde çökme olursa, kayıtlı ACK üzerinden kurtarılır.

Kağıt giriş mesaj ACK zamanından **sonraki ilk tam dakika** açılışıdır.
Bu fiyatta şartlar bozulmuşsa giriş iptal edilir. İlk TP veya SL tüm planı
kapatır; kısmi çıkış/izleyen stop/otomatik emir bu sürümde yoktur. 15dk kurulum
en fazla 72s, 1s kurulum en fazla 168s tutulur. Aynı dakika SL ve TP görülürse
SL önce kabul edilir; mum açılışı zaten TP ötesindeyse bilinen açılış sırası
korunur. Stop sonrası fiyat hareketi ham gözlemde kalır, işlem başarısına girmez.
Veri boşluğu atlanmaz; belirsiz sonuç ayrı kalır.

Funding muhasebesi zaman, oran, mark fiyatı ve tam kapsamı kanıtlanan gerçekleşmiş
ödeme dizisini kabul eder. **Canlı runtime bu tam ödeme dizisini henüz sağlamaz;
bu yüzden `full_net_pct=null`, funding UNKNOWN kalır.** Güncel funding oranı
gerçekleşmiş tarihsel funding tahakkuku yerine kullanılmaz. Raporlardaki
komisyon/kayma varsayımı sonrası sonuç tam net kâr olarak adlandırılmaz.

## Yerel kanıt ve sınır

Yazılım testleri yönlü geometri, teyit gecikmesi, Fibonacci çıpaları, geçersiz
veri, destekleyici verinin karara oy vermemesi, geri test, taze fiyat reddi,
Telegram ACK/çift gönderim/çökme, gerçek toplayıcı şemasıyla tarama ve dakika
çıkış zincirini denetler. Ağ ve gerçek Telegram kullanılmadan yürütülür.

`breakout_only_operational_replay.json`: başlangıç geliştirme denemesi;
8 sabit coin, Ağustos 2026, 4.992 coin-saat, 226 aday, READY yok.
`operational_replay.json`: geri test de tamamlandıktan sonraki aynı dar operasyon
örneği; 253 aday, READY yok. Bu iki geliştirme varyantı saklandı; başarısız
deneme silinmedi. Eşikler istenen mesaj sayısını tutturmak için düşürülmedi.
Bu örnekte gerçek veriden gönderilebilir plan elde edilmediği için **canlı
sinyal sıklığı ve performans doğrulanmış değildir.** Sentetik READY örneği
bağlantı/hesap testidir, piyasa fırsatı veya kârlılık kanıtı değildir.

Tam evren, bağımsız dönem, dakika yürütmesi, likidite/kayma ve gerçekleşmiş
funding ile strateji doğrulaması henüz yapılmadı. Birkaç günlük ileri gözlem
teknik hataları ve mesaj kullanışlılığını gösterebilir; güvenilir başarı oranı
veya kârlılık garantisi oluşturmaz.

## Canlıya alma engeli

23 Ekim çağrısında Railway `accept_deploy` otomatik onay denetimi tarafından
reddedildi. Gerekçe, önceki “Ama bir dk” mesajının durdurma talimatı sayılması
ve bekleyen değişiklikleri uygulamanın salt okunur olmamasıydı. Son kullanıcı
mesajı yeniden başlatmayı istiyor; ret farklı bir araç veya CLI ile aşılmadı.

Mevcut bekleyen patch yalnız eski `62dfea5` salt okunur launch-check sürümünü
başlatır. Yeni motoru başlatmaz. Ona körlemesine onay verilmemelidir. Canlı
disk, mevcut alıcı, veri servisi ve yeni sürümün teslimi doğrulanmadan
“Telegram başladı” denemez. Önerilen ayarlar `deployment_plan.json` içindedir;
bu dosya uygulanmış yapılandırma değildir.

## Yeniden üretim

```bash
python -m unittest discover -s tests -p 'test_pattern_engine.py' -v
python -m research.pattern_v1.offline_smoke --cache /path/to/existing-cache --output /tmp/pattern-smoke.json
LS_RUNTIME_PROFILE=PATTERN_STRUCTURE_V1 python long_short_pattern_runtime.py --report
```

İlk iki komut dış ağa çıkmaz. Son komut yerel ayrı araştırma veritabanından
rapor okur/ilk açılış şemasını oluşturur; Telegram veya veri işçisi başlatmaz.
