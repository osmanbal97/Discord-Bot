import discord
from discord.ext import commands
from discord.ui import Button, View
import yt_dlp
import spotipy
import asyncio
from spotipy.oauth2 import SpotifyClientCredentials
import os
from dotenv import load_dotenv

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

queue = []
voice_client = None
standby_task = None
STANDBY_TIMEOUT = 900  # 15 dakika (saniye cinsinden)
is_paused = False  # Pause kontrolü

@bot.event
async def on_ready():
    print(f'{bot.user} olarak giriş yapıldı.')

def get_youtube_url(search_query):
    ydl_opts = {
        'format': 'bestaudio/best',
        'noplaylist': True,
        'quiet': True,
        'default_search': 'ytsearch',
        'extract_flat': True,
        'limit': 1
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        result = ydl.extract_info(search_query, download=False)
        if result and 'entries' in result:
            return result['entries'][0]['url']
        elif 'url' in result:
            return result['url']
    return None

def download_song(url):
    ydl_opts = {
        'format': 'bestaudio/best',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '256',
        }],
        'outtmpl': 'song.%(ext)s',
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

async def standby(ctx):
    global voice_client
    await asyncio.sleep(STANDBY_TIMEOUT)
    if voice_client and voice_client.is_connected() and not voice_client.is_playing():
        await voice_client.disconnect()
        await ctx.send("15 dakika boyunca hareketsiz kaldım, bu yüzden siktirip gidiyom.")

async def reset_standby(ctx):
    global standby_task
    if standby_task:
        standby_task.cancel()
    standby_task = asyncio.create_task(standby(ctx))

async def skip_song(interaction: discord.Interaction):
    global voice_client
    if voice_client and voice_client.is_playing():
        voice_client.stop()
        await interaction.response.send_message("Şarkı atlandı!")
        await play_next_song(interaction)
    else:
        await interaction.response.send_message("Şu anda çalan bir şarkı yok.")

async def toggle_pause(interaction: discord.Interaction):
    global voice_client, is_paused

    if voice_client and voice_client.is_playing() and not is_paused:
        voice_client.pause()
        is_paused = True
        await interaction.response.edit_message(content="Şarkı duraklatıldı.", view=create_music_controls())
    elif voice_client and is_paused:
        voice_client.resume()
        is_paused = False
        await interaction.response.edit_message(content="Şarkı devam ediyor.", view=create_music_controls())
    else:
        await interaction.response.send_message("Şu anda çalan bir şarkı yok veya zaten duraklatılmış.")

def create_music_controls():
    skip_button = Button(label="Skip", style=discord.ButtonStyle.red)
    skip_button.callback = skip_song

    pause_button_label = "Resume" if is_paused else "Pause"
    pause_button = Button(label=pause_button_label, style=discord.ButtonStyle.blurple)
    pause_button.callback = toggle_pause

    view = View()
    view.add_item(skip_button)
    view.add_item(pause_button)

    return view

async def play_next_song(ctx_or_interaction):
    global voice_client
    if queue:
        next_song = queue.pop(0)
        voice_client.play(discord.FFmpegPCMAudio(next_song), after=lambda e: asyncio.run_coroutine_threadsafe(play_next_song(ctx_or_interaction), bot.loop))
        if isinstance(ctx_or_interaction, commands.Context):
            await ctx_or_interaction.send(f"Şimdi çalıyor: {next_song}", view=create_music_controls())
        else:
            await ctx_or_interaction.followup.send(f"Şimdi çalıyor: {next_song}", view=create_music_controls())
    else:
        if isinstance(ctx_or_interaction, commands.Context):
            await ctx_or_interaction.send("Kuyruk boş. Yeni şarkı ekleyene kadar bekliyorum.")
        else:
            await ctx_or_interaction.followup.send("Kuyruk boş. Yeni şarkı ekleyene kadar bekliyorum.")
        await reset_standby(ctx_or_interaction.channel)

@bot.command()
async def play(ctx, url_or_query: str):
    global voice_client

    if ctx.author.voice is None or ctx.author.voice.channel is None:
        await ctx.send("Önce bir ses kanalına gir.")
        return

    voice_channel = ctx.author.voice.channel

    if ctx.voice_client is None:
        voice_client = await voice_channel.connect()

    if "spotify.com" in url_or_query:
        track_id = url_or_query.split("/")[-1].split("?")[0]
        track = sp.track(track_id)
        query = f"{track['name']} {track['artists'][0]['name']}"
        youtube_url = get_youtube_url(query)
        if youtube_url is None:
            await ctx.send("Şarkıyı YouTube'da bulamadım.")
            return
        await ctx.send(f"Spotify şarkısı bulundu: {query}\nYouTube'dan oynatılıyor: {youtube_url}")
        download_song(youtube_url)
    elif "youtube.com" in url_or_query or "youtu.be" in url_or_query:
        download_song(url_or_query)
    else:
        youtube_url = get_youtube_url(url_or_query)
        if youtube_url is None:
            await ctx.send("Aranan şarkı bulunamadı.")
            return
        download_song(youtube_url)

    if os.path.exists("song.mp3"):
        song_path = "song.mp3"
        queue.append(song_path)

        if not voice_client.is_playing():
            await play_next_song(ctx)
        else:
            await ctx.send(f"Şarkı kuyruğa eklendi: {url_or_query}")
    else:
        await ctx.send("Müzik dosyası bulunamadı.")

    await reset_standby(ctx)

@bot.command()
async def skip(ctx):
    global voice_client
    if voice_client and voice_client.is_playing():
        voice_client.stop()
        await ctx.send("Şarkı atlandı!")
        await play_next_song(ctx)
    else:
        await ctx.send("Şu anda çalan bir şarkı yok.")

@bot.command()
async def leave(ctx):
    global voice_client, standby_task
    if voice_client:
        await voice_client.disconnect()
        voice_client = None
        if standby_task:
            standby_task.cancel()
        await ctx.send("Sesli kanaldan ayrıldım.")
    else:
        await ctx.send("Zaten sesli kanalda değilim.")

bot.run(DISCORD_TOKEN)
