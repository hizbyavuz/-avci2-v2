# TA PAPER özel veri çıkışı — hazırlanan değişiklik, henüz canlı değil

26 Ekim 2026. Railway'de çalışan motor `a0ef35f7663e2a2fc4b7296a8d3d31dea883cf8d`.
Bu paket yalnız altyapı bağlantısını hazırlar. Sunucu satın alınmadı, Railway
ayarları değiştirilmedi, canlı Binance isteği veya gerçek emir üretilmedi.

## Bulgu ve sınırı

- 21:15 TSİ: motorun aynı dakika yerel ağırlığı 2, Binance IP ağırlığı 1164.
  Ortak IP'deki başka trafik güçlü olasılık; kimin ürettiği kesin değil.
- Eski yardımcı servis `sleep 45; exit 0`, restart `NEVER`; son bir saat CPU/RAM
  sıfır, ilgili saatlerde ağ/HTTP kaydı yok. GitHub'da çalışan workflow yok.
- Railway sabit çıkış IP'lerinin bile müşteriye özel olacağını garanti etmiyor:
  https://docs.railway.com/networking/static-outbound-ips
- DigitalOcean hesabında Droplet yok. Oracle önceki denemesi kapasite nedeniyle
  tamamlanmamış; erişilebilir hazır VPS doğrulanamadı.

## Somut kurulum

Motor, veritabanı, Telegram ve arşiv Railway'de kalır. Tek yeni Linux sunucusu
yalnız altı mevcut kamuya açık Binance Futures GET uç noktasını iletir.
İşlem motoru, strateji veya Telegram servisi bu sunucuda çalışmaz.

Önerilen: DigitalOcean standart Ubuntu 24.04 Droplet, `sgp1`,
`s-1vcpu-1gb` (1 vCPU, 1 GB RAM, 25 GB disk, 1 TB transfer), **6 USD/ay**
plan bedeli; vergi ve kota aşımı hariç, Railway ücretine ek. Boyut/bölge
26 Ekim 2026 API kataloğunda mevcut. Codex Universal geliştirme imajı değildir.
Otomatik yedekleme, ek disk, Load Balancer veya NAT Gateway satın alınmaz.
Sunucu silinene kadar kullanım faturalanır; durdurmak faturayı bitirmez.

Standart Droplet kendi kiralanmış public IPv4 adresinden doğrudan çıkar;
bu adres sunucu var olduğu sürece ona atanmıştır. Paylaşımlı CPU, ortak NAT
anlamına gelmez. Adres geçmişi/erişim uygunluğu garanti değildir; 403/451/418
alınırsa IP/bölge döndürülmez, istekler durdurulur ve sonuç raporlanır.

Kaynaklar:
- https://www.digitalocean.com/pricing/droplets
- https://docs.digitalocean.com/products/droplets/details/private-droplets/
- https://docs.digitalocean.com/support/how-do-i-get-a-droplets-dedicated-ip-address/

## Uygulama sırası

1. Aylık ek ücret için açık onay al. Yeni Droplet ve SSH anahtarı yalnız bağlı
   DigitalOcean aracıyla oluşturulur. Ubuntu 24.04 imajını API'den doğrula.
   Tek standart public IPv4; özel NAT/IPv6/rotasyon yok. SSH anahtarı kullan;
   parolalı SSH kapalı. Yönetim SSH'sını yönetici IP'siyle sınırla, HTTPS 443 açık.
2. Docker Engine/Compose ve OpenSSL kur. Bu dalın doğrulanmış commit'ini sunucuya
   kopyala. **Henüz motoru veya Binance sorgusunu başlatma.**
3. Railway son heartbeat/418 kaydından en güncel `cooldown` JSON'unu al.
   Son doğrulanan `until_ms=1793121240264`, `reason=BINANCE_HTTP_418`,
   `source=EXCHANGE_RETRY_TIME`: **27.10.2026 20:14:00.264 TSİ**.
   Bu belgeyi son kayıt yerine kullanma; süre uzadıysa yeni değeri taşı.
4. Güncel JSON'u sunucuda root-only `/root/cooldown-export.json` olarak sakla.
   Repo dizininden `sudo python3 prepare_ta_egress.py --address DROPLET_IPV4
   --cooldown-file /root/cooldown-export.json` çalıştır. Betik yalnız dosyaları
   hazırlar; mevcut dizini, banı, sertifikayı veya token'ı asla ezmez.
5. `sudo docker compose -f compose.ta-egress.yaml up -d --build` çalıştır.
   Health kontrolü `/healthz`, token ile ve oluşturulan sertifikaya güvenerek
   yapılır; Binance'e istek çıkarmaz. Tek worker/durable state kilidini doğrula.
   Eksik/bozuk cooldown dosyasıyla sunucu başlamamalı.
6. Railway'de bu dalın commit'ini ve aşağıdaki üç değişkeni birlikte staged yap:
   `TA_PAPER_EGRESS_URL=https://DROPLET_IPV4`, `TA_PAPER_EGRESS_TOKEN`
   (egress.env'deki gizli değer), `TA_PAPER_EGRESS_CA_PEM`
   (server.crt içeriği). `TA_PAPER_COMMIT_SHA` yeni commit olmalı.
   Token'ı veya TLS özel anahtarını chat'e, GitHub'a, loglara koyma.
7. Değişiklikleri yayımla; mevcut `/app/.long-short-state` aynen kalır.
   `request_usage.transport=PRIVATE_EGRESS`, aynı/sonraki cooldown ve
   ban bitene kadar **sıfır Binance isteği** koşulunu doğrula.
8. En erken sunucu bekleme süresinden sonra motorun doğal ilk saat isteğini
   doğrula. Sonra tam tarama, veri boşlukları, IP/yerel ağırlık farkı ve aday
   teslimini izle. İlk başarılı HTTP yanıtı kalıcı çözüm kanıtı değildir;
   en az bir günlük normal akış görülmeden günlük sorun bitti denmez.

## Güvenceler / geri alma

- TLS sertifika ve IP adı doğrulanır; yetkisiz istek Binance'e ulaşmaz.
  Sertifika 10 yıl geçerli, yalnız bu istemciye güven çapası olarak verilir;
  özel anahtar ele geçirilirse token ve sertifika birlikte yenilenmelidir.
- Host, proxy, imzalı hesap veya emir parametresi kabul edilmez. HTTP redirect
  ve otomatik doğrudan Railway'e dönüş yok. Ortam proxy'leri kullanılmaz.
- Mevcut istemci limiti ve ikinci sunucu limiti aynı korumaları uygular.
  418/429/403/451 beklemesi kalıcıdır. Sunucu yalnız tek worker açar.
- Strateji `core.py`, sinyal/risk eşikleri, data freshness, tarama kapsamı ve
  aday/plan ayrımı değişmez. Config hash:
  `f4485acc0340fb6352cde17558ada6f53459bafa486793423792c3b4e2ccdedd`.
- Geçiş başarısızsa motor veri bekler; sırf çalışsın diye doğrudan IP'ye dönmez.
  Geri alma, en yeni iki taraftaki cooldown'un daha geç olanını koruyarak ve
  trafiği kapalı tutarak yapılır. Bu bir yasak süresi kısaltma yöntemi değildir.
- Sunucu kapatılırsa state diskte saklanır. Silme işlemi ayrıca kullanıcı
  yetkisi gerektirir; yeniden oluşturmak otomatik kurtarma yöntemi değildir.

## Doğrulama

104 çevrimdışı test geçti. Gerçek yerel TLS bağlantısıyla doğru/yanlış token,
sertifika adı, ban sırasında sıfır çıkış; diğer testlerde tek worker,
zorunlu cooldown dosyası, kalıcı 418, endpoint/parametre sınırları,
Unicode coin sembolleri ve sayısal verinin korunması kontrol edildi.
Gunicorn 26.2.0 ile yerelde iki gerçek TLS açılışı ayrıca doğrulandı: restart
sonrasında aynı ban, yetkili health yanıtı ve sıfır Binance isteği. Paket
kurulumu başarılı. Bu testler Binance'e veya ücretli bir sunucuya istek üretmez.
Üretim Docker imajı ve gerçek sunucu/erişim doğrulaması henüz tamamlanmış sayılmaz.
