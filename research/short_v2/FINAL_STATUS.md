# Motorun teslim durumu — 22 Ekim 2026

İstenen sonuç, güvenle işlem değerlendirmesinde kullanılabilecek iki yönlü canlı
sinyal motoruydu. **Bu sonuç henüz sağlanmadı.** Kod ve ölçüm düzeltmeleri
tamamlandı; SHORT için olumlu işlem beklentisi gösterilemedi. Yeni deployment
yapılmadı, Telegram etkinleştirilmedi, otomatik emir yok. Son salt okunur Railway
kontrolünde servisler kapalıydı; bu aşamada yeniden Railway sorgusu yapılmadı.

## Son teşhis

625 eski aday ve iki yeni SHORT modelinin 1.523 adayı birlikte incelendi.
2.148 adayın tamamı korundu; bunların 974'ünde tam dakika yolu vardı. Aşağıdaki
tablo Ağustos'taki tam yollu altkümeleri gösterir. Bir R ilk stop riskidir.
Ücret/kayma varsayımı %0,30; funding dahil değildir. Dört ve 24 saat sütunları,
o zamana kadar kapanmış planları gerçekleşmiş çıkışta, açık planları o andaki
fiyatta değerlendirir. Bunlar önerilen yeni çıkış kuralları değildir.

| SHORT modeli | Tam yol | 4 saat ortalama R | 24 saat ortalama R | Sabit planın son ortalama R'si |
|---|---:|---:|---:|---:|
| Eski 72 saatlik dip kırılımı | 157 | −0,189 | −0,305 | −0,441 |
| Başarısız yukarı kırılım | 156 | −0,243 | −0,472 | −0,582 |
| Kırılan desteğin geri testi | 192 | −0,308 | −0,434 | −0,533 |

Eski 157 SHORT'un 93'ü işlem içindeyken 1R lehe hareket bile görmedi. 27'si
en az 1R gördükten sonra zararla kapandı. En az 2R gördükten sonra zararla kapanan
yoktu. Bu, zararın tamamını izleyen stopun kazancı geri vermesine bağlamayı
desteklemiyor. Girişten dört ve 24 saat sonraki ham yön getirileri de Ağustos'ta
üç model için ortalamada negatifti; yalnız işlem maliyeti açıklaması yetersiz.

Karar anından önceki BTC getirisine göre ayrım da tek başına çözüm olmadı:

| SHORT modeli | BTC önceki 24 saatte yükselmiş: n / ortalama R | BTC düşmüş: n / ortalama R |
|---|---:|---:|
| Eski model | 64 / −0,608 | 93 / −0,327 |
| Başarısız yukarı kırılım | 111 / −0,727 | 45 / −0,225 |
| Desteğin geri testi | 98 / −0,554 | 94 / −0,510 |

Eylül'de bazı küçük altkümeler pozitifti; örneğin BTC düşerken başarısız yukarı
kırılım modeli yalnız yedi tam ölçülen adaya sahipti. Bu altküme seçilip yeni
kural ilan edilmedi. Her iki ay önceden görülmüş geliştirme verisidir; koşullu
tablo nedensellik, tüm evren başarısı veya bağımsız doğrulama sayılmaz.
Tarihsel evren kripto dışı sözleşmeler de içerir. Eksik girişler/ileri yollar
rapordan çıkarılarak başarı oranı yükseltilmedi.

## Uygulanan kod düzeltmeleri

- `long_short_signal_policy.py`, grafik koşulunun READY olmasını yayın izninden
  ayırır. Eski SHORT ve iki yeni SHORT modeli ret durumundadır. Bildirim ayarı
  açık olsa bile fiyat sorgusu veya Telegram gönderimi başlamadan durdurulur.
  Bunların yerel araştırma kayıtları ve sonuç ölçümleri korunur.
- LONG yalnız açıkça etiketlenen araştırma bildirimi için uygundur. Bu,
  doğrulanmış işlem önerisi veya başarı olasılığı değildir. Hiçbir model
  `actionable=true` veya tahmini kazanma oranı üretmez.
- Saatlik çalışma katmanı `SWING_RUNTIME_V2_2026_10_22` olarak sürümlendi.
  Eski veritabanı manifestleri silinmez; yeni çalışma sürümü ayrı kaydedilir.
  Giriş kuralları, stoplar ve V1 dakika çıkış hesabı değiştirilmedi.
- Rapor LONG/SHORT sonuçlarını ayrı verir. Her yönde eksik/açık kayıt sayıları
  ve 24/72/168 saatlik açık+kapalı plan değerlendirmeleri vardır. Toplu LONG
  kazancı, kaybeden SHORT tarafının yerine başarı kanıtı olarak kullanılamaz.
- Daemon açık `LS_RUNTIME_PROFILE=SWING_STRUCTURE_V1` seçimi ister. Eski ortam
  bayraklarıyla MAJOR'a sessiz geri dönüş yoktur. Kontrol, Telegram alıcısı
  keşfi veya durum dizini değişikliği başlamadan yapılır. Bu dal eski deployment
  üzerine yalnız kod değiştirilerek çalıştırılmamalıdır; varsayılan profil yoktur.

Yeni araştırma modeli veya eşik araması yapılmadı. Üç SHORT modelinden birini
başarılı göstermek için stop, hedef, filtre veya örneklem değiştirilmedi.

## Doğrulama

Seçilen regresyon gruplarında 173 test geçti: karar/çıkış/rapor, SHORT modelleri,
türev bağlamı, funding hesabı, eski MAJOR uyumluluğu, veri işçileri, yayın
engeli ve başlangıç seçimi. Son rapor değişikliğinden sonra ilgili 23 çalışma
katmanı testi ve yedi yeni teşhis/yayın testi tekrar geçti.

Daha geniş `test_runtime_cleanup.py` çalıştırmasında 17 testin 16'sı geçti;
bir yedekleme testi bu ortamda `botocore` kurulu olmadığından hata verdi.
İlgili dört RuntimeTests ayrıca geçti. Yedekleme testi başarılı gösterilmedi;
bu işlemde üretim yedeklemesi çalıştırılmadı.

Teşhis tamamen çevrimdışıydı. Kullanılan dakika ZIP'lerinin korunmuş resmi
CHECKSUM kanıtı ve yerel hash'i kontrol edildi. Ağustos BTC saatlik kaynağında
korunmuş yerel hash, Eylül'de resmi CHECKSUM kanıtı vardır. Bu ayrım korunur.
Sentetik testlerde gelecekteki BTC mumu geçmiş kararı etkilemez; veri boşluğu
bilinmeyen kalır; stop sonrası düşüş, kapanmış SHORT zararını kazanca çevirmez.

## Henüz tamamlanamayan bölüm

SHORT için sabit kurallı, maliyet sonrası üstünlük gösteren bir model yok.
LONG için görülen olumlu geliştirme sonucu bağımsız ve ileri doğrulamadan geçmiş
değil. Tam tarihsel kripto evreni, eksiksiz dakika/funding kapsamı, aynı işlem
planıyla kontrol karşılaştırması ve kullanılacak büyüklükte yürütme ölçümü de
tamamlanmadı. Bunlar bir yapılandırma anahtarıyla çözülecek eksikler değil.

Canlı veri akışı ve Telegram teslimatı bu sürümle doğrulanmadı. Önceki Railway
incelemesindeki eksik alıcı ayarı, kapalı veri servisleri ve bekleyen eski kaynak
kaldırma değişikliği uygulanmadı. Bu teslim, çalışan/kârlı canlı motor belgesi
değildir. Kullanıcının "SHORT'u da düzelt, sonra aç" koşulu karşılanmadığı için
sinyaller başlatılmadı.

## Yeniden üretim

Gerekli resmi kaynak ZIP'leri daha önce doğrulanmış yerel önbellekte olmalıdır.
Komut ağ isteği yapmaz; mevcut sonuçları sessizce ezmez:

```bash
python research/short_v2/diagnose.py --cache /path/to/swing-archive-cache --workers 4
python -m unittest discover -s tests -p 'test_short_diagnosis_policy.py'
```

Sayısal kanıt: `CAUSE_DIAGNOSTIC.json`, tüm kayıtlar `CAUSE_RECORDS.json.gz`.
Kod/test/hash kaydı: `RUNTIME_VALIDATION.json`. Önceki deneyler ve başarısız
sonuçları `FINDINGS.md`, `DECISION.json` ve iki ayın `EPISODES.json.gz`
dosyalarında aynen korunur.
