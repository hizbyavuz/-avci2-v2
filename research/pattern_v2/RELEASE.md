# 15dk / 30dk işlem kurulumu — V2

Kullanıcı işlem fikrinin kendisini 15 veya 30 dakikalık grafiklerde almak
istediğini netleştirdi. Önceki 15dk/1s tasarımı bu tercihe uymuyordu.

- Kurulum ve giriş aralıkları yalnız **15dk ve 30dk**. Saatlik kurulum reddedilir.
- Formasyon, kırılım/geri test, ATR, Fibonacci ve yapısal SL kendi kurulum
  aralığından hesaplanır. 30dk mevcut yerel Binance mum toplayıcısında zaten var;
  yeni veri servisi veya başka borsa eklenmedi.
- 4s grafik daha büyük fiyat engelleri ve bağlam için kullanılır. İlk küçük
  engeli otomatik TP sayan önem ayrımı bu değişiklikle çözülmüş değildir;
  daha önce açıklanan hedef mesafesi sorunu hâlâ ayrı doğrulama gerektirir.
- 15dk/30dk, zorunlu işlem tutma süresi değildir. İlk TP/SL çıkışı korunur.
  Her iki aralığın 72 saatlik araştırma zaman aşımı yalnız azami takip
  sınırıdır; önerilen veya beklenen tutuş süresi değildir, optimize edilmedi.
- Maliyet/R, kovalama, formasyon ve yardımcı veri kuralları gevşetilmedi.
  24 saat bildirim kotası ve otomatik emir bulunmaması korunur.

Karar sürümü `PATTERN_STRUCTURE_RESEARCH_V2_2026_10_23`, açık çalışma profili
`PATTERN_STRUCTURE_V2`, sonuç deposu `pattern_structure_v2.db`. V1 kayıtları
silinmez; V1'in 253 reddedilmiş adayı V2 başarısı veya başarısızlığı sayılmaz.
V1 araştırması `53b8262` commit'inden yeniden üretilebilir. V2'nin piyasa
performansı veya sinyal sıklığı ölçülmüş değildir.

Doğrulama 15dk ve 30dk saat hizasını, aynı fiyat yapısının her iki aralıkta
kendi stop/hedefini üretmesini, 1s girişin reddini ve gerçek toplayıcının 30dk
şemasıyla tarama → sahte Telegram ACK → dakika çıkış zincirini içerir.
`VALIDATION.json` çalıştırılan testleri ve kaynak hash'lerini kaydeder.

Canlı deployment ve gerçek Telegram teslimatı yapılmadı. Önceki otomatik
onay reddi devam ediyor. Hazırlanan `deployment_plan.json` uygulanmış
yapılandırma değildir. Gerçekleşmiş funding kapsamı eksik olduğundan tam net
sonuç bilinmiyor; komisyon/kayma varsayımı sonrası sonuç ayrıca etiketlenir.
