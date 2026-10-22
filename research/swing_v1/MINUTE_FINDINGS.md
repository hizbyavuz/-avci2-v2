# Stoplu işlem denetimi — 22 Ekim 2026

Mevcut karar sürümü ve çıkış kuralları değiştirilmeden eldeki dakika verisinin
tamamı çalıştırıldı. 625 olay kaydı korundu. **Bu çalışma motorun kârlılığını
doğrulamaz: olayların %97,76’sında giriş dakikası verisi yoktur.**

## Kapsam ve sonuç

| Durum | LONG | SHORT | Toplam |
|---|---:|---:|---:|
| Bütün adaylar | 436 | 189 | 625 |
| Giriş dakikası verisi eksik | 424 | 187 | 611 |
| Girişte koşul bozulduğu için işlem açılmıyor | 2 | 0 | 2 |
| Giriş ve 168 saatlik yol tam ölçüldü | 10 | 2 | 12 |
| %0,30 ücret/kayma varsayımından sonra artıda | 8 | 0 | 8 |
| Aynı varsayımla ekside | 2 | 2 | 4 |

**Funding (vadeli pozisyon taşıma ödemesi) bilinmiyor; tam net sonuç yok.**
Ölçüm kağıt plandır; borsada gerçekleşmiş emir/fill değildir. Kaldıraç kullanılmadı.
Bu 12 işlemin oranları, 625 adayın genel kazanma oranı değildir. Sekiz sözleşmenin
arşivi önceki sekiz olaylık teknik örnek için indirilmişti; o örneğin sonuçları
zaten biliniyordu. Aynı sözleşmelerden birden fazla olay bağımsız örnek sayılmaz.
Tarihsel varlık türü doğrulanmadığından kapsam hâlâ kripto dışı USDT ürünleri
içerir; yalnız coinlerden oluşan canlı evrenin testi olarak okunmamalıdır.

## Verisi bulunan 14 adayın tamamı

Saatler Türkiye saati (Europe/Istanbul). Plan sonucu ilk işlem büyüklüğüne göre
yüzdedir ve yalnız %0,30 ücret/kayma varsayımını düşer. “Ham lehte hareket”,
stop sonrası yolu da kapsar; elde edilmiş kazanç değildir.

| Aday | Yön | Karar saati | Plan sonucu | Ham lehte hareket | Planda kalma |
|---|---|---|---:|---:|---:|
| AIXBTUSDT | LONG | 09.08 23:00 | -9.84% | 4.83% | 42.9 saat |
| WLDUSDT | LONG | 10.08 11:00 | +3.18% | 5.82% | 168.0 saat |
| ZECUSDT | SHORT | 11.08 11:00 | -3.37% | 4.30% | 142.2 saat |
| ATOMUSDT | LONG | 11.08 21:00 | +5.62% | 9.39% | 62.5 saat |
| SNDKUSDT | LONG | 12.08 16:00 | Giriş geçersiz: fiyat uzaklaşmış | — | İşlem yok |
| SMCIUSDT | LONG | 12.08 17:00 | Giriş geçersiz: fiyat uzaklaşmış | — | İşlem yok |
| ATOMUSDT | LONG | 13.08 15:00 | -4.63% | 8.34% | 116.0 saat |
| SNDKUSDT | SHORT | 19.08 02:00 | -3.79% | 10.64% | 8.4 saat |
| ZECUSDT | LONG | 19.08 20:00 | +29.94% | 59.64% | 60.1 saat |
| LDOUSDT | LONG | 20.08 00:00 | +5.97% | 23.91% | 78.5 saat |
| NEOUSDT | LONG | 20.08 01:00 | +18.23% | 26.10% | 55.1 saat |
| AIXBTUSDT | LONG | 20.08 12:00 | +11.25% | 23.58% | 44.1 saat |
| WLDUSDT | LONG | 20.08 21:00 | +5.23% | 24.04% | 35.1 saat |
| ATOMUSDT | LONG | 21.08 06:00 | +3.37% | 13.00% | 26.1 saat |

**Somut ayrım:** SNDK SHORT ham yolda %10,64 düşüş gördü; fakat yaklaşık 8,4
saatte stop olduğu için kağıt sonuç −%3,79 oldu. Bu kayıt “başarılı SHORT”
sayılmadı. ZEC LONG ise yaklaşık 60,2 saatte +%29,94 sonuçlandı; ham yoldaki
%59,64 hareketin tamamı elde edilmiş kazanç sayılmadı. AIXBT’nin ilk LONG’u
−%9,84, sonraki LONG’u +%11,25; yalnız başarılı olan kayıt seçilmedi.

İki geçersiz giriş (SNDK LONG ve SMCI LONG) kayıp işlem değildir: sonraki ilk tam
dakika açılışı kırılım seviyesinden izin verilen mesafeyi aştığı için açılmaz.
Referans fiyat eski kapanışa geri çekilmedi. İşlem süresi medyanı 57,6 saattir;
bu yalnız ölçülebilen 12 kaydın özelliğidir.

## Ek maliyet kontrolü

| Toplam ücret/kayma varsayımı | 12 kayıt ortalaması | Başlangıç riski cinsinden ortalama |
|---|---:|---:|
| %0.30 | %+5.10 | +0.62 R |
| %0.60 | %+4.80 | +0.56 R |
| %1.00 | %+4.40 | +0.49 R |

R, başlangıç stopuna kadar alınan fiyat riskinin katıdır. Maliyet senaryoları
gerçek kayma tahmini değildir. Funding hepsinde bilinmiyor. Bu küçük, mevcut
dosyalara bağlı örneğin olumlu ortalaması bütün sistem için beklenen kazanç
olarak kullanılamaz. Genel strateji kazanma oranı ve beklenen değer JSON’da boş
bırakıldı. 24/72 saat özetleri o anda açık olan planları da içerir.

## Tamamlamak için gereken veri

393 aday sözleşmesi için 479 sembol/ay dakika dosyası gerekiyor. Sekizi mevcut;
**471 dosya eksik: 384 Ağustos ve 87 Eylül 2026 dosyası.** Ay sınırından taşan
yedi günlük ölçümler Eylül dosyasını da ister. Kaynak URL’leri, kesin zaman
aralıkları ve her dosyanın durumu `MINUTE_DATA_REQUESTS.json` içinde. Bu liste
yalnız Swing planlarının veri ihtiyacıdır; basit stratejinin ortak işlem planlı
karşılaştırması ayrıca tamamlanmalıdır.

Ayrıca her ilgili aralık için eksiksiz funding tahakkuku (zaman, oran, mark
fiyatı ve kapsam doğrulaması) ve o tarihte geçerli coin/ürün sınıfı gerekiyor.
Yalnız ilk/son funding satırı kapsamanın tam olduğuna kanıt sayılmaz.

Ortam daha önce `data.binance.vision` erişimini ağ politikası nedeniyle
engelledi. Bu çalışmada ağ isteği yapılmadı ve erişim engeli aşılmaya çalışılmadı.
Devam için bu dosyaların erişime izin veren bir ortamda elde edilip aktarılması
veya bu çalışma ortamına normal, yetkili arşiv erişimi sağlanması gerekir.
Kodu yeniden çalıştırmak eksik fiyatları kendiliğinden tamamlayamaz.

Çalıştırma:

```bash
python research/swing_v1/minute_audit.py --cache /path/to/swing-archive-cache
```

Önceki sekiz dosyanın hash’i kontrol edilir; farklılaşmış veya kaybolmuşsa çalışma
durur. Araç yalnız yerel dosyaları okur; adayları silmez, sonraki mumu eksik giriş
yerine kullanmaz. Yeni veri geldiğinde aynı kurallarla bütün kayıtlar yeniden
ölçülebilir; önceki çıktı ayrıca sürümlenmelidir.

Altı yeni denetim testi geçti; önceki 119 ile toplam 125 geçiş kaydı var.
Karar/stop/çıkış kodu değişmedi. Canlı motor ve Telegram açılmadı.

Kanıtlar: `MINUTE_AUDIT_PROTOCOL.md`, `MINUTE_AUDIT.json`,
`MINUTE_DATA_REQUESTS.json`, `minute_audit.py`,
`evidence/2026-08-minute-audit.json.gz`.
