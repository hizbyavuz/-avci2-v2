# LONG/SHORT modelinin bütün olarak kontrolü

İnceleme kesimi: 15 Ekim 2026, 05:00:30 Türkiye saati.
Çalışan sürüm: `3633edf`, geometri V4.1.

**Bu kısa pencerede sistem yön seçebiliyor, fakat giriş–stop–hedef koşullarını
birlikte geçen işlem planı üretemiyor. Tıkanma mum teyidinden önce oluşuyor.**
Bu bulgu yalnızca incelenen kayıtları açıklar; bütün piyasanın fırsatsız olduğunu
veya filtrelerin kârlı olduğunu kanıtlamaz.

## Sayılar

04:53:42–05:00:10 arasındaki yaklaşık 6,5 dakikalık kayıtlar incelendi.
77 okunabilir geometri kaydından aynı coin/yön/5 dakika kovasındaki 17 tekrar
çıkarıldı. Kalan 60 tarama kaydı 37 farklı coine ait. Bunlar bağımsız işlem değil.
Bir geometri satırı karışmış/okunamaz durumda; yön kayıtlarında da bir karışmış
satır ve bir farklı tür satır dışarıda bırakıldı. Veri eksikleri gizlenmedi.

| Ölçüm | Sonuç |
|---|---:|
| Yön kaydıyla tekil zaman eşleştirmesi kurulabilen plan | 57 / 60 |
| Yön koşulları geçen plan kaydı | 21 |
| Yön ve geometri birlikte geçen | 0 |
| Yönü geçenlerde girişin karşıt bölge içinde kalması | 17 |
| Yönü geçenlerde hedef alanı / net R yetersizliği | 3 |
| Yönü geçenlerde yerel tetik bölgesi bulunamaması | 1 |

Tüm 60 planda: 47 giriş karşıt bölge içinde, 10 geometri ekonomik olarak
yetersiz, 3 yerel tetik bölgesi yok. Bölge içinde kalan 47 kaydın 23'ünde engel
4 saatlik, 13'ünde 1 saatlik, 6'sında 30 dakikalık, 5'inde 5/15 dakikalık bölge.

Bu, küçük giriş seviyesi ile büyük zaman dilimi engellerinin sık çatıştığını
gösterir. Büyük bölgeyi silmek veya giriş seviyesini uzak kenarına taşımak için
tek başına gerekçe değildir; ikisi de ayrı strateji hipotezidir.

## Matematikteki sıkışma

Geçerli sıralanmış giriş–stop–hedefi olan 10 kaydın 7'sinde brüt hedef mesafesi,
varsayılan %0,30 gidiş-dönüş masrafından küçük/eşit. Bu masraf gerçek gerçekleşen
emir maliyeti değil, mevcut modelin tahminidir; funding ayrıca ölçülür.

Mevcut 1 net R koşulu, hedefin yalnızca %0,9'dan büyük olmasını istemiyor:

`Gerekli brüt hedef = max(%0,9; stop mesafesi + 2 × %0,30)`

Bu eşitlik, `(hedef − masraf) / (risk + masraf) >= 1` koşulundan gelir.

| Kaydedilmiş örnek | Stop mesafesi | Brüt hedef | 1 net R için gerekli hedef |
|---|---:|---:|---:|
| UNI SHORT | %0,659 | %0,024 | %1,259 |
| USELESS SHORT, ilk kayıt | %1,632 | %0,107 | %2,232 |

Her iki örneğin yön koşulu geçmişti. Belirlenen yakın hedefe ulaşmaları bile,
bu masraf varsayımı altında net kazanç anlamına gelmiyor. Hedefi uzatıp yakındaki
engelleri yok saymak veya stopu sayıyı güzelleştirmek için daraltmak çözüm diye
uygulanmadı.

## Henüz ölçülemeyenler

- Bağımsız sanal işlem defteri boş: `groups={}`. Kazanma oranı, net beklenti,
  teyit gecikmesinin gerçek işlem sonuçlarına etkisi henüz hesaplanamaz.
- Geçmiş 24 saatlik aşama özetleri eski sürümleri de kapsıyor; yeni V4.1'e mal
  edilmedi. Mesajların eski gözlemsel yön başarısı işlem kârı sayılmadı.
- OI/funding gibi özelliklerin çıkarıldığı mevcut denemeler yalnızca yön kararı
  karşılaştırması. Masraf sonrası artı değerleri kanıtlanmış değil.
- Son kapsam kaydı 152 uygun coin ve tur başına 30 seçim gösteriyor. Bu rapor
  seçili derin analizlerden oluşuyor; tam piyasa fırsat yakalama oranı değil.
- Yön/geometri eşleştirmesi aynı coin için 1 saniye içindeki tekil önceki loga
  dayanıyor; ortak olay kimliği yok. Üç belirsiz eşleşme onaylı sayılmadı.

## Bu çalışmada yapılan

Çalışan kodun SHA-256 özeti ve strateji kimliği doğrulanıp `frozen_contract.json`
içine kaydedildi. Giriş, çıkış, masraf, zaman sınırı ve sayım birimi açık hale
getirildi. `model_consistency_report.py` bu ham kayıtlardan raporu yeniden üretir;
farklı sürümü birleştirmez, bozuk satırları ve eksik eşleşmeleri ayrıca sayar.

Canlı sinyal kuralları bu çalışma kapsamında değiştirilmedi. Böylece mevcut
sürümün sonuç toplama dönemi bir ölçüm güncellemesi yüzünden yeniden başlamadı.
Karar kodunun özeti öncesi/sonrası aynı: `c599da9c448c764e00f2d729d6a05a1e0c931a87bb46a2d15eeb72ab480d7e36`.

Bir sonraki strateji deneyi, yerel tetik ile aynı işlem ufkuna ait stop/hedef
kurmanın net sonuçlarını karşılaştırmalıdır. Tüm yakın seviyelerin sert engel
sayılması ile güçlü/yapısal seviyelerin ayrılması test edilecek bir hipotezdir;
bu rapor herhangi bir alternatifi başarılı ilan etmez. Mevcut sürüm, bu karşılaştırma
için sabit referans olarak tutulur.

Yeniden üretim:

```sh
python audits/model_consistency_report.py --evidence audits/model_review_20261015/runtime_evidence.json --contract audits/model_review_20261015/frozen_contract.json
python -m unittest discover -s audits -p 'test_model_consistency_report.py'
```
