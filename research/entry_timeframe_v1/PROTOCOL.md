# LONG giriş mumu karşılaştırması — ön kayıt, 23 Ekim 2026 İstanbul

Bu deney 15/30/60 dakikalık kapanışların saatler–günler süren aynı yapı
fikrine girişte ne değiştirdiğini ölçer. Kısa vadeli stop veya scalp deneyi
değildir. Mevcut seçim, outcome, runtime, SHORT yayın engeli değişmez.
Sonuç görülmeden bu belge, kod, test ve kaynak manifesti commit edilir.

## Sabit kapsam

- Yalnız LONG; üç varyant, başka eşik taraması yok.
- Kaynak: daha önce resmi checksum ile doğrulanmış, yerelde bulunan Ağustos
  2026 1 dk arşivlerinin tamamı (392 sözleşme). Kazanan eski 399 plan üzerinden
  seçim yapılmaz: her sözleşmenin bütün uygun kapanışları taranır.
- Giriş mumlarının açılışları 8 Ağustos 00:00 dahil, 24 Ağustos 00:00 UTC hariç.
  İlk yedi gün ısınma, son bölüm yedi günlük sonuç yolu içindir. Bu takvim
  yeni sonuçlara bakılmadan seçilmiştir; Ağustos zaten görülmüş geliştirme verisidir.
- Mevcut dakika kapsamı önceki araştırmalardan seçilmiş, rastgele olmayan bir
  altkümedir. Tarihsel kripto dışı sözleşmeler ayrıştırılamıyor. Tam borsa evreni,
  saf kripto örneklemi veya bağımsız doğrulama olarak sunulmaz.
- Ağ isteği yok. Manifest/hash uyuşmazlığı hata; eksik/bozuk dakika üzerinden
  mum doldurma yok. Eksik mum nedeniyle reddedilen değerlendirmeler sayılır.

## Aynı saatlik yapı, üç farklı giriş mumu

1. Bütün 15/30/60 mumlar doğrulanmış 1 dk mumlardan UTC hizalı üretilir.
   İçinde eksik veya bozuk dakika olan toplama mumu geçersizdir.
2. Her UTC saatin BAŞINDA önceki 168 tamamlanmış saatten yapı sabitlenir.
   Son 72 saatin tepesi kırılacak seviye, son 168 saatin dibi uzama referansıdır.
   ATR14, teyitli saatlik pivot stopu (yoksa 72 saat dibi), 0,25 ATR tamponu,
   son 24 saat hacmi ve bilinen 4 saat engelleri yalnız bu geçmişten gelir.
   Aynı saatin sonraki verisi küçük mumun kararına sızamaz.
3. Giriş mumu kapanışı bu tepenin üzerinde; kendi UTC zaman diliminin önceki
   yedi gündeki hacim medyanının en az üç katı; taker alış payı >%50.
   15 dk hacmi 1 saat hacmiyle karşılaştırılmaz. Hacim toplama penceresi de
   değiştiğinden deney yalnız birkaç dakika erkene kaydırılmış aynı sinyal değildir.
4. Önceki tamamlanmış saatte 24 saat hacmi >=2M USDT. Dipten uzama <=%25,
   stop uzaklığı <=%12, kırılan seviyeden uzaklık <=1 saatlik ATR. Bilinen üst
   engel varsa >=2R alan gerekir; bilinmeyen engel açıkça UNKNOWN kalır.
5. Her varyant kendi ilk READY olayını alır; aynı sembolde yeniden olay için
   eski tetik seviyesinin içinde/altında art arda altı tamamlanmış SAAT gerekir.
   Eksik saat bu ardışıklığı bozar. İşlem çıkışı, geçmiş olayı geriye dönük silmez.
   Günlük mesaj kotası/ranking uygulanmaz; bütün olaylar araştırma kaydına girer.

Bu ortak 60 dk referansı önceki dondurulmuş V1 ile aynı değildir: ATR/hacim24/
engel hesabı teyit saatini içermez. Bu deneyin 60 dk oranı eski 399 planın
%54,4 oranıyla doğrudan kıyaslanmaz. Eski kod/hash ve bulgular korunur.

## Giriş ve çıkış ölçümü

- Gözlem kapanış+1 saniye; bundan sonraki ilk tam 1 dk açılışı giriş referansı.
  Bu fiyatta geometri yeniden doğrulanır. Geçersiz girişler kaybolmaz.
- Eski SWING_OUTCOME_V1 aynen: stop önce, gap durumunda kötü açılış; +2R'de
  takip devreye girer, tamamlanmış 4 saatlik blokla stop izler; en fazla 168 saat.
- %0,30 toplam sabit ücret/kayma varsayımı. Funding bilinmiyor; tam net sonuç
  iddia edilmez. Kaldıraç, fon büyüklüğü, eşzamanlı portföy riski modellenmez.
- Her olaya tam 168 saat dakika yolu aranır. Eksik/açık/gerçekleşemeyen giriş
  ayrı paydalarda kalır. Stop sonrası hareket işlem başarısına katılmaz.

## Önceden belirlenen rapor

Her varyant için aday/başlayan/tam yol sayısı, pozitif kapanış oranı, ücret
varsayımı sonrası ortalama/medyan R ve yüzde getiri, başlangıç riski, tutuş
süresi, işlem içi %5/10/20/30/50 erişimi, erişip zararla kapanma sayısı, ham
erişim; eksiklerin sayısı. Gün bazında örnek sayısı da gösterilir.

Ek tanımlayıcı eşleme: aynı sembol ve UTC teyit saatinde her iki varyant da
olay ürettiyse ilk olayı eşleştir. 60 dk'ya göre kaç dakika erken, giriş fiyatı
farkı ve R farkı; eşleşmeyenler ayrıca. Bu sonradan ortak sinyale koşullu
altkümedir; tüm fırsatların etkisi veya nedensel üstünlük sayılmaz.

Kazanma oranına göre model seçilmez. Üç varyant ve bütün başarısız sonuçlar
korunur; eşik ayarı ve canlıya geçiş yapılmaz. Küçük/bağımlı geliştirme örneği
gelecek başarı oranı veya yeni güvenilirlik iddiası üretmez.
