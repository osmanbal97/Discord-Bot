# Kurulum Rehberi (AI asistan için)

Bu dosya, botu kendi bilgisayarında çalıştıracak kişinin AI asistanına (Claude Code, Cursor vb.) verilmek için yazıldı. Asistan: adımları sırayla uygula, **insan gereken adımlarda dur ve kullanıcıya ne yapacağını açıkça söyle.**

## Botun yapısı

- Tek bir Docker container içinde iki süreç var (`supervisord.conf`):
  - **Lavalink** (Java, `127.0.0.1:2333`): YouTube/Spotify'dan sesi çekip Discord'a stream eder. Ayarları `application.yml`.
  - **Bot** (`bot.py`, Python, discord.py + wavelink): Discord komutları. `library.py` türe göre klasörleri yönetir.
- Kaydedilen klasörler `/app/data/folders.json` içinde durur, volume olarak bağlanmalı.

## Kurallar

- `.env` dosyasını **asla commit'leme ve içindeki token'ları ekrana yazdırma.**
- YouTube girişi için **ana Google hesabını değil, yan (burner) bir hesap** kullanılmalı. Hesap banlanabilir.
- Aynı `DISCORD_TOKEN` ile **aynı anda sadece bir bot** çalışabilir. Bot başka bir yerde (ör. EC2) açıksa önce orası kapatılmalı.
- 2333 portunu dışarıya açma (`-p 2333` kullanma).

## 1. Gereksinimler

- Docker (Docker Desktop veya OrbStack) çalışır durumda olmalı: `docker info`
- Git
- **Discord'a erişim.** Discord Türkiye'de engelli. Bilgisayar Türkiye'deyse VPN açık olmalı. Kontrol:
  ```bash
  curl -s -o /dev/null -w "%{http_code}\n" --max-time 8 https://discord.com/api/v10/gateway
  ```
  `200` dönmeli. `000` dönüyorsa kullanıcıya VPN açmasını söyle ve bekle.

## 2. Zip'i aç ve `.env` oluştur

Proje zip olarak gelir. Aç ve klasöre gir:

```bash
unzip discord-bot.zip && cd discord-bot
cp .env.example .env
```

`.env`'i doldur. Değerleri **kullanıcıdan iste**, kendin uydurma:

- `DISCORD_TOKEN`: botun token'ı. Bot sahibi ayrıca gönderir (zip'te yoktur).
- `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET`: **opsiyonel**, sadece Spotify linkleri için. Kullanıcı https://developer.spotify.com/dashboard adresinde bir app oluşturup Client ID ve Secret'ı kopyalar. Boş bırakılırsa bot yine çalışır, sadece Spotify linkleri çalmaz.
- `LAVALINK_PASSWORD`: rastgele bir değer üret: `openssl rand -hex 16`
- `LAVALINK_URI`: olduğu gibi kalsın (`http://localhost:2333`).

## 3. Build ve ilk çalıştırma

```bash
docker build -t discord-bot .
docker run -d --restart unless-stopped --env-file .env \
  -v "$HOME/bot-data:/app/data" --name bot discord-bot
```

İlk açılışta Lavalink plugin'leri indirir, 20–40 saniye sürebilir.

## 4. YouTube girişi (Google device kodu) — İNSAN GEREKLİ

İlk çalıştırmada refresh token olmadığı için Lavalink loglara bir giriş kodu basar:

```bash
docker logs bot 2>&1 | grep "OAUTH INTEGRATION"
```

Şuna benzer bir satır çıkar:

```
OAUTH INTEGRATION: To give youtube-source access to your account, go to https://www.google.com/device and enter code ABC-DEF-GHIJ
```

**Asistan burada durmalı** ve kullanıcıya şunu söylemeli:

> https://www.google.com/device adresine git, **yan (burner) Google hesabınla** giriş yap ve `ABC-DEF-GHIJ` kodunu gir. Onayladıktan sonra bana haber ver.

Kod birkaç dakika içinde geçersiz olur. Loglarda `The device token has expired` görürsen `docker restart bot` ile yeni kod al.

Kullanıcı onayladıktan sonra refresh token'ı loglardan al:

```bash
docker logs bot 2>&1 | grep "Token retrieved successfully"
```

Satır şöyle biter: `Store your refresh token as this can be reused. (1//0xxxxxxxx...)`. Parantez içindeki değeri **ekrana yazdırmadan** `.env`'e ekle:

```bash
TOKEN=$(docker logs bot 2>&1 | grep "Token retrieved successfully" | tail -1 | sed -E 's/.*reused\. \((.*)\)$/\1/')
echo "PLUGINS_YOUTUBE_OAUTH_REFRESHTOKEN=$TOKEN" >> .env
```

**Container'ı yeniden oluştur.** `docker restart` `.env`'i yeniden okumaz:

```bash
docker rm -f bot
docker run -d --restart unless-stopped --env-file .env \
  -v "$HOME/bot-data:/app/data" --name bot discord-bot
```

Artık her açılışta kod sormaz. Loglarda `YouTube access token refreshed successfully` görünmeli.

## 5. Doğrulama

```bash
docker logs bot 2>&1 | grep -E "Wavelink node connected|olarak bağlandı|OAUTH|refreshed"
```

Beklenenler:
- `Wavelink node connected` → bot Lavalink'e bağlandı
- `Bot ... olarak bağlandı!` → bot Discord'a girdi
- `YouTube access token refreshed successfully` → YouTube girişi çalışıyor

Kullanıcıdan Discord'da şunları denemesini iste:
1. Bir ses kanalına girip `!play Ezhel Geceler` → şarkı çalmalı
2. İki şarkı ekleyip ilki bitince ikincisine geçtiğini görmek
3. Çalan şarkıda **➕ Kaydet** butonu → "rap klasörüne kaydedildi" demeli
4. `!folders` → klasör butonları çıkmalı, butona basınca klasör çalmalı

## Sorun giderme

| Log / belirti | Sebep | Çözüm |
|---|---|---|
| `Cannot connect to host discord.com:443` | Discord engelli | VPN aç, container'ı yeniden başlat |
| `UnknownHostException: maven.lavalink.dev` | Docker'ın interneti yok / yeni açıldı | Docker'ı yeniden başlat, `docker restart bot` |
| `Invalid status code for oauth2 token fetch: 400` ve Lavalink açılmıyor | Refresh token geçersiz (iptal edilmiş veya hesap banlanmış) | `.env`'den `PLUGINS_YOUTUBE_OAUTH_REFRESHTOKEN` satırını sil, container'ı yeniden oluştur, 4. adımı tekrarla |
| `Sign in to confirm you're not a bot` | YouTube engeli | 4. adım yapılmış mı kontrol et; ev internetinde genelde nadir |
| Şarkı çalmıyor, logda `Track exception` | O video engelli/bölge kısıtlı | Başka şarkı dene; bot otomatik sıradakine geçer |
| Kaydedilen klasörler kayboldu | Volume bağlanmamış | `-v "$HOME/bot-data:/app/data"` ile çalıştır |

## Güncelleme

Yeni zip gelince eski klasördeki `.env`'i yeni klasöre kopyala, sonra:

```bash
docker build -t discord-bot .
docker rm -f bot
docker run -d --restart unless-stopped --env-file .env \
  -v "$HOME/bot-data:/app/data" --name bot discord-bot
```

Klasörler `$HOME/bot-data` içinde olduğu için güncellemede kaybolmaz.
