# SHORT yeniden tasarımı: iki aday da kabul edilmedi

22 Ekim 2026. Kullanıcının son talimatı: SHORT tarafı da düzeltilmeden sinyalleri
açma. Bu çalışma sırasında Railway yapılandırması, deployment veya Telegram
değiştirilmedi. Canlı servis kontrolünde motor kapalıydı.

## Neyi değiştirdik?

Eski SHORT, 72 saatlik dip kırılımında satış arıyordu. Yeni araştırma dosyası
`long_short_short_retest.py` iki ayrı yapı arıyor:

1. **Başarısız yukarı kırılım:** Fiyat eski tepenin üstüne kapanır, tekrar aralığın
   içine döner, seviyeyi geri almaya çalışıp başarısız olur; geri test mumunun
   dibi kapanışla kaybedildiğinde SHORT adayı oluşur.
2. **Kırılan desteğin geri testi:** Daha düşük tepelerin ardından destek kaybolur;
   fiyat kırdığı desteğe yükselip altında kalır ve geri testin dibini yeniden
   kaybederse SHORT adayı oluşur. Geri test olmadan düz düşüşe satış yapılmaz.

İki model de kapalı saatlik mum, gerçekleşmiş satış baskısı, yapı stopu, kaçmış
girişin reddi ve bilinen desteğe kadar masraf sonrası en az 2R alan kontrolü
kullanır. Önceden bilinen destek yoksa alan "bilinmiyor" olarak kalır.
OI tek başına yön seçmez. Eski başarısız türev filtreleri yeni model adıyla
tekrar sunulmadı. Yeni modeller araştırma dosyasındadır; canlı seçiciye bağlanmadı.

Kurallar ve deney şartları ilk sonuçtan önce GitHub'a kaydedildi. Parametre
taraması yapılmadı; başarısız sonuçtan sonra stop, hedef veya eşikler değiştirilmedi.
LONG seçimi ve V1'in dakika bazlı çıkış hesabı değişmedi. +2R sonrası 4 saatlik
yapıyla takip ve en fazla 7 günlük elde tutma aynı kaldı.

## İlk teşhis

Eski modelin Ağustos'taki 157 geçerli SHORT planında 44 artı, 113 eksi sonuç vardı.
131 plan stopla, 26 plan süre dolumuyla kapandı. Bunların yalnızca 27'si işlem
içinde 2R lehe hareket gördü; ilk dört saatte stop olan sadece dört plandı.
Bu, bütün problemi çok dar ve erken stoplara bağlamayı desteklemiyor.

## Yeni modellerin sonuçları

Ağustos 833, Eylül 869 sembolün mevcut saatlik verisi tarandı. Eski sinyal
listesiyle sınırlandırılmadı. Toplam 1.523 model-adayı kaydedildi. İki modelin
örtüşen adayları ayrı deney kayıtlarıdır; bağımsız işlem sayısı değildir.

Aşağıdaki tablo **tam 168 saatlik dakika yolu olan altkümeyi** gösterir.
Toplam %0,30 ücret/kayma varsayımı dahil, funding hariçtir. Bir R başlangıç
fiyatı ile stop arasındaki risktir; kaldıraç veya portföy getirisi değildir.

| SHORT modeli | Dönem | Tüm adaylar | Tam ölçülen | Artıda kapanan | Ortalama R |
|---|---|---:|---:|---:|---:|
| Başarısız yukarı kırılım | Ağustos | 256 | 156 | 31 (%19,87) | −0,582 |
| Desteğin başarısız geri testi | Ağustos | 421 | 192 | 43 (%22,40) | −0,533 |
| Başarısız yukarı kırılım | Eylül | 291 | 27 | 10 (%37,04) | −0,139 |
| Desteğin başarısız geri testi | Eylül | 555 | 43 | 12 (%27,91) | −0,337 |

İki model de ölçülebilen altkümelerde iki ayda da negatif. Bu sonuçlar
"SHORT düzeldi" demeyi desteklemiyor. Önceden belirlenen ücret sonrası pozitif
beklenti koşulu sağlanmadığı için ikisi de canlı aday olarak kabul edilmedi.

## Veri sınırları gizlenmedi

- 922 adayda tam giriş dakikası yok. 77 adayda giriş planın dışında kalmış.
  524 adayda ölçüm başladı; 418'inin tam yolu var. Diğer 106'sında ileri yol eksik;
  84'ünde plan boşluktan önce kapanmıştı, 22'sinde işlem sonucu hâlâ bilinmiyor.
  Bu erken kapanan altküme tam yollu örneklemle birleştirilerek başarı oranı
  iyileştirilmedi. Ayrı tablolar sayısal raporlarda duruyor.
- Özellikle Eylül dakika kapsamı zayıf; tabloda görülen oranlar bütün evrenin
  başarısı diye genellenemez. Eksik sonuçlar sıfır maliyet veya kazanç sayılmadı.
- Tarihsel evren kripto dışı sözleşmeler de içeriyor. İki ay daha önce incelenmiş
  geliştirme dönemleri; bağımsız doğrulama değil. Bütün maliyetler sonrası net
  sonuç ve portföy başarısı iddia edilmiyor.
- Eylül saatlik ve kullanılan dakika ZIP'leri resmi CHECKSUM ile önceden
  doğrulanmıştı; tekrar hash kontrolü yapıldı. Ağustos saatlik dosyalarında eski
  yerel hash doğrulandı, resmi CHECKSUM kanıtı yok. Bu ayrım sonuçtan önce
  `EXECUTION_NOTES.md` içinde düzeltildi. Ağ isteği yapılmadı.

## Kod doğrulaması ve teslim durumu

10 hedefli test geçti: her iki gerçek yapı, geri testin zorunluluğu, seviyenin
geri alınmasıyla iptal, kırılım öncesinde teyitli düşük tepe, gelecekteki verinin
geçmiş kararı değiştirmemesi, eksik/eski veri, akış işareti, masraf/kovalama,
eksik girişin paydada kalması ve stop sonrası veri boşluğunun ayrı ölçülmesi.

Yeni kod teknik olarak çalışıyor; ekonomik olarak başarılı SHORT stratejisi
doğrulanmadı. `research_only=true`, `actionable=false`, `win_probability=null`.
Bu dosyalar sonradan aynı ölçümle kıyaslanabilecek başarısız deney kaydıdır.
Sinyal sayısını artırmak için bunları etkinleştirmek kullanıcının istediği
güvenilir long/short motorunu sağlamaz.

Kaynaklar: `BASE_DIAGNOSTIC.json`, `PROTOCOL.md`, `EXECUTION_NOTES.md`,
`2026-08-SUMMARY.json`, `2026-09-SUMMARY.json`, her iki ayın `EPISODES.json.gz`
dosyaları ve `VALIDATION.json`.
