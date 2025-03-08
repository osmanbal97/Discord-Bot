import discord
from discord.ext import commands
from discord.ui import Button, View
import yt_dlp
import spotipy
import asyncio
from spotipy.oauth2 import SpotifyClientCredentials
import os
import random
import logging
from dotenv import load_dotenv
from time import time

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")

sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
    client_id=SPOTIFY_CLIENT_ID,
    client_secret=SPOTIFY_CLIENT_SECRET
))

intents = discord.Intents.default()
intents.voice_states = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

queue = []  # Sadece şarkı isimlerini tutacak
voice_client = None
standby_task = None
STANDBY_TIMEOUT = 900
is_paused = False
is_shuffled = False
is_looping = False
MAX_FILES = 10
MUSIC_DIR = "music"

# Cache için global değişkenler
youtube_cache = {}  # {search_query: (url, timestamp)}
CACHE_TTL = 3600  # 1 saatlik cache süresi (saniye cinsinden)

def setup_music_directory():
    os.makedirs(MUSIC_DIR, exist_ok=True)

def get_youtube_url(search_query):
    if search_query in youtube_cache:
        cached_url, timestamp = youtube_cache[search_query]
        if time() - timestamp < CACHE_TTL:
            logger.info(f"Cache hit for query: {search_query}")
            return cached_url
        else:
            logger.info(f"Cache expired for query: {search_query}")
            del youtube_cache[search_query]

    ydl_opts = {
        'format': 'bestaudio/best',
        'noplaylist': True,
        'quiet': True,
        'default_search': 'ytsearch',
        'extract_flat': True,
        'limit': 1
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            result = ydl.extract_info(search_query, download=False)
            if result and 'entries' in result:
                url = result['entries'][0]['url']
            elif 'url' in result:
                url = result['url']
            else:
                return None
            
            youtube_cache[search_query] = (url, time())
            logger.info(f"Added to cache: {search_query}")
            return url
    except Exception as e:
        logger.error(f"Error getting YouTube URL: {e}")
        return None

def get_spotify_type(url):
    if "playlist" in url:
        return "playlist"
    elif "track" in url:
        return "track"
    return None

def clean_old_files():
    try:
        mp3_files = [f for f in os.listdir(MUSIC_DIR) if f.endswith('.mp3')]
        if len(mp3_files) >= MAX_FILES:
            mp3_files.sort(key=lambda x: os.path.getctime(os.path.join(MUSIC_DIR, x)))
            for file in mp3_files[:-MAX_FILES + 1]:
                os.remove(os.path.join(MUSIC_DIR, file))
    except Exception as e:
        logger.error(f"Error cleaning files: {e}")

def download_song(url, track_name):
    try:
        song_path = f"{MUSIC_DIR}/{track_name}.mp3"
        if os.path.exists(song_path):
            logger.info(f"Dosya zaten mevcut: {song_path}")
            return song_path

        clean_old_files()
        ydl_opts = {
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '256',
            }],
            'outtmpl': f'{MUSIC_DIR}/{track_name}.%(ext)s',
            'quiet': True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        return song_path
    except Exception as e:
        logger.error(f"Error downloading song: {e}")
        if track_name in youtube_cache:
            del youtube_cache[track_name]
        return None

async def standby(ctx):
    global voice_client
    await asyncio.sleep(STANDBY_TIMEOUT)
    if voice_client and voice_client.is_connected() and not voice_client.is_playing():
        await voice_client.disconnect()
        await ctx.send("15 dakika boyunca hareketsiz kaldım, bu yüzden ayrılıyorum.")

async def reset_standby(ctx):
    global standby_task
    if standby_task:
        standby_task.cancel()
    standby_task = asyncio.create_task(standby(ctx))

class MusicControls(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.red)
    async def skip(self, interaction: discord.Interaction, button: Button):
        if voice_client and voice_client.is_playing():
            voice_client.stop()
            await interaction.response.send_message("Şarkı atlandı!")
            await play_next_song(interaction)
        else:
            await interaction.response.send_message("Şu anda çalan bir şarkı yok.")

    @discord.ui.button(label="Oynat/Duraklat", style=discord.ButtonStyle.blurple)
    async def pause_resume(self, interaction: discord.Interaction, button: Button):
        global is_paused
        if voice_client and voice_client.is_playing() and not is_paused:
            voice_client.pause()
            is_paused = True
            await interaction.response.send_message("Şarkı duraklatıldı.")
        elif voice_client and is_paused:
            voice_client.resume()
            is_paused = False
            await interaction.response.send_message("Şarkı devam ediyor.")
        else:
            await interaction.response.send_message("Şu anda çalan bir şarkı yok.")

    @discord.ui.button(label="Durdur", style=discord.ButtonStyle.grey)
    async def stop(self, interaction: discord.Interaction, button: Button):
        global voice_client, queue
        if voice_client and voice_client.is_connected():
            if voice_client.is_playing():
                voice_client.stop()
            queue.clear()
            await voice_client.disconnect()
            voice_client = None
            await interaction.response.send_message("Müzik durduruldu ve bağlantı kesildi.")
        else:
            await interaction.response.send_message("Zaten bir ses kanalında değilim.")

    @discord.ui.button(label="Siradakiler", style=discord.ButtonStyle.green)
    async def queue_list(self, interaction: discord.Interaction, button: Button):
        if not queue:
            await interaction.response.send_message("Kuyruk şu anda boş.")
            return
        queue_str = "\n".join([f"{i+1}. {song}" for i, song in enumerate(queue)])
        await interaction.response.send_message(f"Şarkı Kuyruğu:\n{queue_str}")

    @discord.ui.button(label="Sifirla", style=discord.ButtonStyle.red)
    async def clear(self, interaction: discord.Interaction, button: Button):
        global queue
        queue.clear()
        await interaction.response.send_message("Kuyruk temizlendi.")

class ExtraControls(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Loop", style=discord.ButtonStyle.blurple)
    async def loop(self, interaction: discord.Interaction, button: Button):
        global is_looping
        is_looping = not is_looping
        state = "açık" if is_looping else "kapalı"
        await interaction.response.send_message(f"Döngü modu {state}.")

    @discord.ui.button(label="Shuffle", style=discord.ButtonStyle.blurple)
    async def shuffle(self, interaction: discord.Interaction, button: Button):
        global is_shuffled
        is_shuffled = not is_shuffled
        if is_shuffled and queue:
            random.shuffle(queue)
        state = "açık" if is_shuffled else "kapalı"
        await interaction.response.send_message(f"Karıştırma modu {state}.")

    @discord.ui.button(label="Durum", style=discord.ButtonStyle.green)
    async def status(self, interaction: discord.Interaction, button: Button):
        if not voice_client or not voice_client.is_connected():
            await interaction.response.send_message("Şu anda bir ses kanalına bağlı değilim.")
            return
        status_msg = [
            f"Bağlı kanal: {voice_client.channel.name}",
            f"Çalıyor: {voice_client.is_playing()}",
            f"Duraklatıldı: {is_paused}",
            f"Kuyrukta {len(queue)} şarkı var",
            f"Karıştırılmış: {is_shuffled}",
            f"Döngü: {is_looping}",
            f"Ses seviyesi: {int(voice_client.source.volume * 100) if voice_client and voice_client.source else 50}%"
        ]
        await interaction.response.send_message("\n".join(status_msg))

    @discord.ui.button(label="Ses Arttir", style=discord.ButtonStyle.grey)
    async def volume_up(self, interaction: discord.Interaction, button: Button):
        if not voice_client or not voice_client.is_playing():
            await interaction.response.send_message("Şu anda müzik çalmıyor.")
            return
        current_volume = int(voice_client.source.volume * 100)
        new_volume = min(100, current_volume + 10)
        voice_client.source.volume = new_volume / 100
        await interaction.response.send_message(f"Ses seviyesi {new_volume}% olarak ayarlandı.")

    @discord.ui.button(label="Ses Dusur", style=discord.ButtonStyle.grey)
    async def volume_down(self, interaction: discord.Interaction, button: Button):
        if not voice_client or not voice_client.is_playing():
            await interaction.response.send_message("Şu anda müzik çalmıyor.")
            return
        current_volume = int(voice_client.source.volume * 100)
        new_volume = max(0, current_volume - 10)
        voice_client.source.volume = new_volume / 100
        await interaction.response.send_message(f"Ses seviyesi {new_volume}% olarak ayarlandı.")

class SearchView(View):
    def __init__(self, search_results, ctx):
        super().__init__(timeout=60)  # 60 saniye sonra butonlar devre dışı kalır
        self.search_results = search_results
        self.ctx = ctx

        for i, result in enumerate(search_results[:5], 1):  # İlk 5 sonucu al
            title = result['title'][:50] + "..." if len(result['title']) > 50 else result['title']
            button = Button(label=f"{i}. {title}", style=discord.ButtonStyle.blurple, custom_id=str(i))
            button.callback = self.button_callback
            self.add_item(button)

    async def button_callback(self, interaction: discord.Interaction):
        if interaction.user != self.ctx.author:
            await interaction.response.send_message("Bu arama sizin değil!", ephemeral=True)
            return

        choice = int(interaction.data['custom_id']) - 1
        selected_song = self.search_results[choice]['title']
        queue.append(selected_song)
        logger.info(f"{selected_song} kuyruğa eklendi.")
        
        await interaction.response.send_message(f"{selected_song} kuyruğa eklendi.")
        if not voice_client or not voice_client.is_playing():
            await play_next_song(self.ctx)
        self.stop()  # Butonları devre dışı bırak

async def play_next_song(ctx_or_interaction):
    global voice_client, queue, is_looping
    if queue:
        next_song_name = queue.pop(0)
        youtube_url = get_youtube_url(next_song_name)
        if youtube_url:
            song_path = download_song(youtube_url, next_song_name)
            if song_path and os.path.exists(song_path):
                source = discord.FFmpegPCMAudio(song_path)
                source = discord.PCMVolumeTransformer(source, volume=0.5)
                voice_client.play(source, after=lambda e: asyncio.run_coroutine_threadsafe(play_next_song(ctx_or_interaction), bot.loop))
                if is_looping:
                    queue.append(next_song_name)
                if isinstance(ctx_or_interaction, commands.Context):
                    await ctx_or_interaction.send(f"Şimdi çalıyor: {next_song_name}", view=MusicControls())
                else:
                    await ctx_or_interaction.followup.send(f"Şimdi çalıyor: {next_song_name}", view=MusicControls())
            else:
                logger.error(f"{next_song_name} indirilemedi.")
                await play_next_song(ctx_or_interaction)
        else:
            logger.error(f"{next_song_name} bulunamadı.")
            await play_next_song(ctx_or_interaction)
    else:
        if isinstance(ctx_or_interaction, commands.Context):
            await ctx_or_interaction.send("Kuyruk boş. Yeni şarkı ekleyene kadar bekliyorum.")
        else:
            await ctx_or_interaction.followup.send("Kuyruk boş. Yeni şarkı ekleyene kadar bekliyorum.")
        await reset_standby(ctx_or_interaction.channel if isinstance(ctx_or_interaction, commands.Context) else ctx_or_interaction.channel)

@bot.command()
async def play(ctx, *, url_or_query: str):
    global voice_client
    try:
        if not ctx.author.voice or not ctx.author.voice.channel:
            await ctx.send("Önce bir ses kanalına gir.")
            return

        voice_channel = ctx.author.voice.channel
        if ctx.voice_client is None:
            voice_client = await voice_channel.connect()

        if "spotify.com" in url_or_query:
            spotify_type = get_spotify_type(url_or_query)
            
            if spotify_type == "playlist":
                playlist_id = url_or_query.split("/")[-1].split("?")[0]
                results = sp.playlist_tracks(playlist_id)
                tracks = results['items']
                
                if not tracks:
                    await ctx.send("Playlist'te şarkı bulunamadı.")
                    return

                first_song = True
                for item in tracks:
                    track = item['track']
                    track_name = f"{track['name']} - {track['artists'][0]['name']}"
                    queue.append(track_name)
                    logger.info(f"{track_name} kuyruğa eklendi.")
                    if first_song and not voice_client.is_playing():
                        first_song = False
                        await play_next_song(ctx)
            
            elif spotify_type == "track":
                track_id = url_or_query.split("/")[-1].split("?")[0]
                track = sp.track(track_id)
                track_name = f"{track['name']} - {track['artists'][0]['name']}"
                queue.append(track_name)
                logger.info(f"{track_name} kuyruğa eklendi.")
                if not voice_client.is_playing():
                    await play_next_song(ctx)
            
            else:
                await ctx.send("Geçersiz Spotify bağlantısı. Lütfen geçerli bir Spotify şarkı veya çalma listesi bağlantısı kullanın.")
                return
        else:
            if url_or_query.startswith(('http://', 'https://')):
                queue.append(url_or_query)
            else:
                queue.append(url_or_query)
            logger.info(f"{url_or_query} kuyruğa eklendi.")
            if not voice_client.is_playing():
                await play_next_song(ctx)

        await ctx.send("Müzik kontrolleri:", view=MusicControls())
        await ctx.send("Ekstra kontroller:", view=ExtraControls())
        await reset_standby(ctx)

    except Exception as e:
        logger.error(f"Error in play command: {e}")
        await ctx.send(f"Bir hata oluştu: {str(e)}")

@bot.command()
async def playnext(ctx, *, url_or_query: str):
    global voice_client
    try:
        if not ctx.author.voice or not ctx.author.voice.channel:
            await ctx.send("Önce bir ses kanalına gir.")
            return

        voice_channel = ctx.author.voice.channel
        if ctx.voice_client is None:
            voice_client = await voice_channel.connect()

        if "spotify.com" in url_or_query:
            spotify_type = get_spotify_type(url_or_query)
            
            if spotify_type == "playlist":
                await ctx.send("Bu komut sadece tekli şarkılar için kullanılabilir. Playlist için !play kullanın.")
                return
            
            elif spotify_type == "track":
                track_id = url_or_query.split("/")[-1].split("?")[0]
                track = sp.track(track_id)
                track_name = f"{track['name']} - {track['artists'][0]['name']}"
                queue.insert(0, track_name)
                logger.info(f"{track_name} kuyruğun başına eklendi.")
                if not voice_client.is_playing():
                    await play_next_song(ctx)
            
            else:
                await ctx.send("Geçersiz Spotify bağlantısı.")
                return
        else:
            if url_or_query.startswith(('http://', 'https://')):
                queue.insert(0, url_or_query)
            else:
                queue.insert(0, url_or_query)
            logger.info(f"{url_or_query} kuyruğun başına eklendi.")
            if not voice_client.is_playing():
                await play_next_song(ctx)

        await ctx.send(f"{url_or_query} kuyruğun başına eklendi.")
        await reset_standby(ctx)

    except Exception as e:
        logger.error(f"Error in playnext command: {e}")
        await ctx.send(f"Bir hata oluştu: {str(e)}")

@bot.command()
async def playnow(ctx, *, url_or_query: str):
    global voice_client
    try:
        if not ctx.author.voice or not ctx.author.voice.channel:
            await ctx.send("Önce bir ses kanalına gir.")
            return

        voice_channel = ctx.author.voice.channel
        if ctx.voice_client is None:
            voice_client = await voice_channel.connect()
        elif voice_client.is_playing():
            voice_client.stop()

        if "spotify.com" in url_or_query:
            spotify_type = get_spotify_type(url_or_query)
            
            if spotify_type == "playlist":
                await ctx.send("Bu komut sadece tekli şarkılar için kullanılabilir. Playlist için !play kullanın.")
                return
            
            elif spotify_type == "track":
                track_id = url_or_query.split("/")[-1].split("?")[0]
                track = sp.track(track_id)
                track_name = f"{track['name']} - {track['artists'][0]['name']}"
                queue.insert(0, track_name)
                logger.info(f"{track_name} hemen çalmak için kuyruğun başına eklendi.")
                await play_next_song(ctx)
            
            else:
                await ctx.send("Geçersiz Spotify bağlantısı.")
                return
        else:
            if url_or_query.startswith(('http://', 'https://')):
                queue.insert(0, url_or_query)
            else:
                queue.insert(0, url_or_query)
            logger.info(f"{url_or_query} hemen çalmak için kuyruğun başına eklendi.")
            await play_next_song(ctx)

        await ctx.send(f"Şimdi çalıyor: {url_or_query}")
        await ctx.send("Müzik kontrolleri:", view=MusicControls())
        await ctx.send("Ekstra kontroller:", view=ExtraControls())
        await reset_standby(ctx)

    except Exception as e:
        logger.error(f"Error in playnow command: {e}")
        await ctx.send(f"Bir hata oluştu: {str(e)}")

@bot.command()
async def controls(ctx):
    await ctx.send("Müzik kontrolleri:", view=MusicControls())
    await ctx.send("Ekstra kontroller:", view=ExtraControls())

@bot.command()
async def move(ctx, from_pos: int, to_pos: int):
    if not queue or from_pos < 1 or to_pos < 1 or from_pos > len(queue) or to_pos > len(queue):
        await ctx.send("Geçersiz sıra numarası.")
        return
    song = queue.pop(from_pos - 1)
    queue.insert(to_pos - 1, song)
    await ctx.send(f"{song} {to_pos}. sıraya taşındı.")

@bot.command()
async def remove(ctx, pos: int):
    if not queue or pos < 1 or pos > len(queue):
        await ctx.send("Geçersiz sıra numarası.")
        return
    song = queue.pop(pos - 1)
    await ctx.send(f"{song} kuyruktan silindi.")

@bot.command()
async def search(ctx, *, query: str):
    try:
        ydl_opts = {
            'format': 'bestaudio/best',
            'noplaylist': True,
            'quiet': True,
            'default_search': 'ytsearch5',  # İlk 5 sonucu al
            'extract_flat': True
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            result = ydl.extract_info(query, download=False)
            if not result or 'entries' not in result or not result['entries']:
                await ctx.send("Arama sonucunda şarkı bulunamadı.")
                return

            search_results = result['entries']
            view = SearchView(search_results, ctx)
            embed = discord.Embed(title=f"'{query}' için Arama Sonuçları", description="Aşağıdaki butonlardan birini seçerek şarkıyı kuyruğa ekleyebilirsiniz.", color=discord.Color.green())
            for i, entry in enumerate(search_results[:5], 1):
                embed.add_field(name=f"{i}. {entry['title']}", value=f"Süre: {entry.get('duration', 'Bilinmiyor')} saniye", inline=False)
            await ctx.send(embed=embed, view=view)

    except Exception as e:
        logger.error(f"Error in search command: {e}")
        await ctx.send(f"Bir hata oluştu: {str(e)}")

@bot.command()
async def help(ctx):
    embed = discord.Embed(title="Bot Komutları", description="Aşağıda botun tüm komutları ve kullanım örnekleri listelenmiştir.", color=discord.Color.blue())
    embed.add_field(
        name="!play <şarkı adı veya URL>",
        value="Bir şarkıyı veya Spotify çalma listesini kuyruğun sonuna ekler.\n**Örnek:**\n- `!play Imagine Dragons - Believer` (YouTube'dan şarkı ekler)\n- `!play https://open.spotify.com/track/xxx` (Spotify şarkısı ekler)\n- `!play https://open.spotify.com/playlist/xxx` (Spotify çalma listesi ekler)",
        inline=False
    )
    embed.add_field(
        name="!playnext <şarkı adı veya URL>",
        value="Bir şarkıyı kuyruğun başına ekler.\n**Örnek:**\n- `!playnext Imagine Dragons - Believer` (YouTube'dan şarkıyı başa ekler)\n- `!playnext https://open.spotify.com/track/xxx` (Spotify şarkısını başa ekler)",
        inline=False
    )
    embed.add_field(
        name="!playnow <şarkı adı veya URL>",
        value="Mevcut şarkıyı keser ve yeni şarkıyı hemen çalar.\n**Örnek:**\n- `!playnow Imagine Dragons - Believer` (YouTube'dan şarkıyı hemen çalar)\n- `!playnow https://open.spotify.com/track/xxx` (Spotify şarkısını hemen çalar)",
        inline=False
    )
    embed.add_field(
        name="!search <şarkı adı>",
        value="YouTube'da şarkı arar ve ilk 5 sonucu butonlarla listeler.\n**Örnek:**\n- `!search Imagine Dragons` (5 seçenek sunar, butona tıklayarak seçilir)",
        inline=False
    )
    embed.add_field(
        name="!controls",
        value="Müzik kontrol butonlarını gösterir (oynat/duraklat, atla, durdur, kuyruk listesi, sıfırla).\n**Örnek:**\n- `!controls` (kontrol panelini açar)",
        inline=False
    )
    embed.add_field(
        name="!move <başlangıç sırası> <hedef sıra>",
        value="Kuyruktaki bir şarkıyı başka bir sıraya taşır.\n**Örnek:**\n- `!move 3 1` (3. sıradaki şarkıyı 1. sıraya taşır)",
        inline=False
    )
    embed.add_field(
        name="!remove <sıra numarası>",
        value="Kuyruktaki belirli bir şarkıyı siler.\n**Örnek:**\n- `!remove 2` (2. sıradaki şarkıyı siler)",
        inline=False
    )
    embed.set_footer(text="Not: Botu kullanmadan önce bir ses kanalına girmelisiniz.")
    await ctx.send(embed=embed)

@bot.event
async def on_ready():
    setup_music_directory()
    logger.info(f"Bot {bot.user} olarak bağlandı!")

bot.run(DISCORD_TOKEN)