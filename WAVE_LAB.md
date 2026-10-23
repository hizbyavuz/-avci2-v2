# Wave Lab — 22 Ekim 2026 deneysel başlangıç

Bu dal, trend geri çekilmesi ve sıkışma kırılışı için ayrı LONG/SHORT
hipotezlerini ölçmek üzere hazırlanmıştır. Kârlılığı doğrulanmış bir motor
değildir. Mevcut daemon, Telegram gönderimi, emir mekanizması ve dondurulmuş
Avcı kuralları değiştirilmez. Otomatik olarak başlamaz; üretime bağlanmaz.

## Kaynak ve kapsam

- Başlangıç: `main`, `00946c6d923f91c4acce43368e94b246cb7514e0`.
- Railway'de son görülen ana motor kaynak dalı farklıdır:
  `feat/major-delivery-v3-2026-10-17`, commit `d74f382c6e858c3bfc007dea44bbc5c43d9fb9dc`.
  Bu çalışma o dalın yerine geçirilmemiştir.
- `futures_closed_klines` veya `closed_klines` tablosunu salt okunur açar.
  Kaynak Binance USD-M Futures olmalıdır; spot veya başka borsa verisi karıştırılmaz.
- 5 dakikalık mumlardan sadece tam kapanmış 15 dakika, 1 saat ve 4 saat
  mumları üretilir. En az 960 ardışık 5 dakika mumu gerekir; en fazla 2880 okunur.
- Önceki tamamlanmış UTC gün/hafta seviyeleri yalnızca pencere tam ise kullanılır.
- `--universe-db` verilirse oradaki **tüm semboller** taranır; eksik geçmişli
  coinler de hata/gözlem kaydı alır. Verilmezse kapsam yalnızca mum
  veritabanındaki coinlerdir; rapor buna tam Binance taraması demez.
- Bir mum ileri test anında henüz alınmamışsa kullanılmaz (`received_ms`).
  Tarihsel arşiv incelemesi ayrı `PRICE_REPLAY` modudur; ileri testle birleştirilmez.

## Başlangıç kuralları (öneri, istatistiksel kalibrasyon yok)

ATR burada 15 dakikalık son 14 mumun ortalama gerçek aralığıdır.

| Kural | Geri çekilme | Sıkışma |
|---|---|---|
| Yapı | Son teyitli 1 saatlik iki tepe ve iki dip aynı yönde ilerler | Önceki 12 adet 15 dakika mumunun aralığı, önceki 24 mum aralığının en çok %75'i |
| Hazırlık | Son 24 adet 15 dakika mumundaki tepe/dipten 0,5–3 ATR çekilme | Son dört mumun karşı tarafı ilk dört muma göre seviyeye yaklaşır |
| Koruma | Son teyitli saatlik dip/tepe korunur | Fiyat kırılış seviyesine en fazla 0,75 ATR uzaktadır |
| Faaliyet | Son 3 adet 5 dakika mumunun ortalama hacmi, önceki 36 mumun en az 0,8 katı | Aynı |
| Tetik | Son tamamlanmış toparlanma mumundan önceki üç 5 dakika mumunun tepesi/dibi | Önceden oluşmuş sıkışma sınırı |
| Geç kalma | Tetik ötesinde 0,75 ATR'den fazla uzama adaylığı engeller | Aynı |

LONG ve SHORT yönleri ayrı sonuçlanır. 4 saatlik yapıya ters yön ayrıca etiketlenir;
bütün zaman dilimlerinin aynı yönde olması zorunlu değildir. Her başarısız koşul
da kaydedilir; keyfî birleşik puan veya başarı olasılığı yoktur.

`WATCH → BREAKOUT → CONFIRMED`: kırılış için 5 dakika kapanışı ve hacim oranı
en az 1,2 gerekir. Bir sonraki gözlenen kapanış da seviyeyi korursa teyit oluşur.
Teyit seviyesi ve stop ilk kayıttan itibaren sabittir. Aradaki mumlar atlanmaz.
Stopa fitil değmesi fikri bozar; teyitten sonra seviyenin kaybedilmesi de fikri bozar.
Hazırlık ömrü 6 saat; aynı coin/strateji/yön için tekrar gözlem aralığı 24 saattir.
Sağlıksız veri durumunda durum ilerlemesi durur ve hata raporlanır.

## Ölçüm

- `WATCH` ve `CONFIRMED` ayrı kohortlardır; aynı episode_id ile bağlantılıdır.
  Bunları bağımsız iki başarı gibi toplamayın.
- Saatler UTC milisaniye olarak kaydedilir. Bildirim metni yalnızca önizlemedir;
  Telegram'a gönderilmediğinden `signal_ms` bir teslimat zamanı değildir.
- Deneme girişi gözlemden 60 saniye sonrasından önce olmayan ilk 1 dakika
  mumunun açılışıdır. Sinyal mumunun geçmiş fiyatından giriş yapılmaz.
- 1/2/3/24/72 saat ayrı etiketlenir. Hedef veya stopun önce gelmesi sonucu
  belirler; aynı 1 dakika mumunda ikisi de varsa stop önce sayılır.
- Stop boşluğunda teorik stop yerine ilk daha kötü açılış kullanılır.
- Her yönde %0,05 ücret ve %0,10 fiyat kayması varsayımı vardır. Bunlar
  gerçek işlem ölçümü değildir. Funding bilinmediğinde `net_pct` ve `net_r`
  **null** kalır; sadece funding hariç modellenmiş sonuç verilir.
- İlk engel geçmişte gözlenen seviyelerden seçilir. Hedef yoksa veya gecikmeli
  girişte getiri/risk 1,2'den düşükse sadece fiyat gözlemi tutulur; hayalî hedef
  yaratılmaz. 1,2 başlangıç araştırma eşiğidir, kanıtlanmış optimum değildir.
- Sinyal öncesi 1 saatlik getiri, teyide kadar getiri ve teyit süresi ayrıdır.
- Ham tüm dönem MFE/MAE (en iyi/en kötü fiyat hareketi) ve +3/+5/+7/+10/+15
  seviyelerine erişim süreleri, stop/çıkış öncesi erişimlerden ayrıdır. Stop sonrası
  yükseliş deneme işleminin kazancı değildir. Ham metrikler gerçek satış kotasyonu değildir.
- Veri boşlukları `DATA_FAILURE` olarak kalır. Gecikmeli veriyle çözülürse önceki
  etiket silinmeden yeni kayıt eklenir. Rapor son etiketi kullanır; eksikleri kayıp
  sayan ayrı kötümser hedef-erişim oranı da verir.
- Tüm taramalar, reddedilen koşullar, kullanılan ham mumlar, kural özeti,
  config hash ve git commit saklanır. Kural değişikliği ayrı sürüm/hash gerektirir.

## Çalıştırma

Python 3.10+; ek bağımlılık gerektirmez. **Bu komutlar sunucuda çalıştırılmadı.**
Kaynak yollarını mevcut dosyalarla eşleştirin; kaynak ile sonuç dosyası aynı olamaz.

```bash
python long_short_wave_runner.py \
  --source-db /app/.long-short-state/binance_futures_kline_shadow.db \
  --universe-db /app/.long-short-state/binance_futures_universe.db \
  --research-db /app/.long-short-state/wave_lab_v1.sqlite

python long_short_wave_runner.py \
  --research-db /app/.long-short-state/wave_lab_v1.sqlite --action report

python -m unittest discover -s tests -p 'test_long_short_wave_lab.py' -v
```

Her çağrı tek tarama/etiketleme yapar. Planlanan ileri testte her 5 dakika
kapanışından sonra 0–120 saniye içinde çağrılmalıdır. Otomatik zamanlayıcı,
yeniden başlatma veya Telegram bağlantısı bu dalda kurulmamıştır.

## Canlı denemeden önce kalan işler

1. Railway kalıcı diskindeki mum/kayıt varlığı ve tazeliğini salt okunur doğrula;
   mevcut verilerin korunmasını sağla. Son görülen deployment `REMOVED` durumundaydı.
2. Tüm uygun coinler için kesintisiz 5 dakika geçmişini ve takip için 1 dakika
   verisini sağla. Mevcut toplayıcının sınırlı kapsamı bu kodla kendiliğinden genişlemez.
3. Spread, pozisyon büyüklüğüne bağlı kayma, coin yaşı/listelenme, işlem durumu,
   BTC/sektör karşılaştırması, spot/türev destek verileri ve funding katmanını bağla.
4. Eşleştirilmiş rastgele/near-miss ve basit strateji kontrollerini ekle; ileri
   test başlangıcı, eşikler ve başarı ölçütlerini sabitle. Bu aşamada p-değeri,
   başarı olasılığı veya üstünlük iddiası yoktur.
5. Kullanıcının daha önceki “Deploy'a basma / motoru değiştirme” talimatını
   gözeterek yeni deneme servisini ayrıca gözden geçir. Mevcut kaynak bağlantısını
   silmeye yönelik bekleyen Railway değişikliklerini topluca onaylama.

Testler sentetik veride kodun davranışını sınar; piyasa performansı kanıtı değildir.
