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

def setup_music_directory():
    os.makedirs(MUSIC_DIR, exist_ok=True)

def get_youtube_url(search_query):
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
                return result['entries'][0]['url']
            elif 'url' in result:
                return result['url']
    except Exception as e:
        logger.error(f"Error getting YouTube URL: {e}")
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
        return f"{MUSIC_DIR}/{track_name}.mp3"
    except Exception as e:
        logger.error(f"Error downloading song: {e}")
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
                await play_next_song(ctx_or_interaction)  # Hata varsa bir sonrakine geç
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
async def play(ctx, url_or_query: str):
    global voice_client
    try:
        if not ctx.author.voice or not ctx.author.voice.channel:
            await ctx.send("Önce bir ses kanalına gir.")
            return

        voice_channel = ctx.author.voice.channel
        if ctx.voice_client is None:
            voice_client = await voice_channel.connect()

        if "spotify.com" in url_or_query:
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
async def controls(ctx):
    await ctx.send("Müzik kontrolleri:", view=MusicControls())
    await ctx.send("Ekstra kontroller:", view=ExtraControls())

@bot.event
async def on_ready():
    setup_music_directory()
    logger.info(f"Bot {bot.user} olarak bağlandı!")

bot.run(DISCORD_TOKEN)