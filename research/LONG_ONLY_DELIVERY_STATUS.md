# İlk yayın kapsamı: yalnız LONG

22 Ekim 2026 UTC / 23 Ekim Türkiye

Kullanıcının "Sadece onu da yapsak iyi yani" yönlendirmesiyle ilk teslim kapsamı,
mevcut LONG kuralının araştırma/izleme bildirimi olarak tek başına çalışmasıdır.
Bu kapsam için SHORT araştırmasının başarılı olması artık önkoşul değildir.
SHORT modelleri ve başarısız sonuçları araştırma kayıtlarında korunur; yeni
SHORT bildirimi veya eski SHORT kartı güncellemesi yayımlanmaz.

## Korunan seçim ve ölçüm

`BASE_BREAKOUT_LONG`, `SWING_STRUCTURE_RESEARCH_V1_2026_10_22` ve
`d899d56bf0dea0bc564c7907e4e79ad30b81e464de9021b6ecfd6d44e4577938`
config hash'i aynen korunur. Yeni filtre/puan, hedef isabet oranı veya eşik
optimizasyonu eklenmedi. Saatlik 72 saat aralık kırılımı, aynı saatin yedi günlük
hacim tabanına göre uyanış, alış payı ve uzamama koşulları mevcut haliyle kalır.
Giriş/stop geometrisi, +2R sonrası dört saatlik takip, en fazla yedi gün,
stop-önce hesabı, eksik verinin ayrı tutulması ve günlük altı bildirim üst sınırı
değişmedi. Altı, gönderim üst sınırıdır; günlük sinyal taahhüdü değildir.

Mevcut geliştirme verisinde 399 tam LONG planının 217'si pozitifti (%54,4),
ücret/kayma varsayımı sonrası ortalama +0,846R idi. Funding bu sayıya dahil
değildir. Eşleşmiş kontrol farkının betimleyici aralığı sıfırı içerdiği için
bağımsız üstünlük veya gelecekteki kazanma olasılığı iddiası yapılmaz.
Özgün sonuçlar `exit_control_v1/FINDINGS.md` içinde korunur.

## Uygulanan yayın düzeltmesi

`SWING_RUNTIME_V3_LONG_ONLY_2026_10_22` çalışma sürümünde kart güncellemesi de
yeni bildirimle aynı yayın denetiminden geçer. Eski bir veritabanından dönen
SHORT kartı böylece Telegram'a gönderilemez. LONG kartı güncellemesi ve tekrar
göndermeme davranışı korunur. Eski manifestlere dokunulmaz; yeni çalışma sürümü
ayrı manifest kullanır. Karar ve çıkış kodları değişmedi.

İlgili 25 Swing ve yedi yayın/teşhis testi geçti (32 toplam). Yeni testler
geri yüklenen SHORT kartının sessiz kaldığını ve LONG kartının yalnız bir kez
güncellendiğini denetler. İlk karma kart testinde iki teslimin aynı zamanla
kurulması nedeniyle son kart belirsizdi; fixture gerçek sıra içerecek şekilde
düzeltildi ve iki test grubu tekrar geçti. Karar SHA256:
`c757e6f34bcd3c5549bfb02f31466f9288721cc77fc8c8697d9ba0bb80f6b5fe`;
çıkış SHA256:
`7798d1a2d6ffe66c367e517b4a7125a15e0d5faf7c2d868292fba136e3411feb`.

## Doğrulanmış operasyon durumu

Bu turdaki salt okunur Railway kontrolünde beş servisin tamamı offline ve
aktif deployment listeleri boştu. Motorun kaynak ayarı hâlâ eski MAJOR dalını
ve d74f382 commit'ini gösteriyor. 18 Ekim tarihli bekleyen kaynak kaldırma
değişikliği yerinde duruyor; uygulanmadı.

Yeni profil ve bildirim ayarları (`LS_RUNTIME_PROFILE`, `SWING_NOTIFICATIONS`)
servis değişken adlarında yok. `TELEGRAM_BOT_TOKEN` var; açık
`TELEGRAM_CHAT_ID` ayarı yok. Bu, kalıcı diskte eski alıcı kaydı bulunmadığının
kanıtı değildir; bu turda diskteki kayıt okunamadı ve alıcı doğrulanmadı.

Çalışma katmanı mevcut geçmiş/veri servisine bağımlı, o servis de kapalı.
Daha önce karşılaşılan veri erişim engelini aşmak için relay/probe başlatılmadı,
bölge/host değiştirilmedi. Canlı kaynak, saatlik mum yeterliliği ve tazelik ile
Telegram alıcısı doğrulanmadan çalışan sinyal akışı iddia edilmiyor.

**Bu turda deployment, ortam değişkeni değişikliği veya Telegram gönderimi
yapılmadı. Sinyaller henüz başlamadı.** Kalan teslim işi, izinli ve çalışır
veri kaynağı ile mevcut alıcının doğrulanması ve LONG sürümünün o bağlantıda
devreye alınmasıdır; yeni bir başarı oranına ulaşana kadar araştırma yapmak
değildir. Otomatik borsa emri yoktur.
