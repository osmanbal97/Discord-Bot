import asyncio
import logging
import os
from typing import cast

import aiohttp
import discord
import wavelink
from aiohttp import web
from discord.ext import commands
from discord.ui import Button, View
from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
LAVALINK_URI = os.getenv("LAVALINK_URI", "http://localhost:2333")
LAVALINK_PASSWORD = os.getenv("LAVALINK_PASSWORD", "youshallnotpass")

intents = discord.Intents.default()
intents.voice_states = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
bot.remove_command("help")

STANDBY_TIMEOUT = 900
standby_tasks: dict[int, asyncio.Task] = {}
current_messages: dict[int, discord.Message] = {}
is_looping: dict[int, bool] = {}
is_shuffled: dict[int, bool] = {}


def get_player(ctx_or_interaction) -> wavelink.Player | None:
    if isinstance(ctx_or_interaction, commands.Context):
        guild = ctx_or_interaction.guild
    else:
        guild = ctx_or_interaction.guild
    if guild and guild.voice_client:
        return cast(wavelink.Player, guild.voice_client)
    return None


def format_duration(ms: int) -> str:
    seconds = ms // 1000
    minutes = seconds // 60
    seconds = seconds % 60
    return f"{minutes}:{seconds:02d}"


async def standby(guild_id: int):
    await asyncio.sleep(STANDBY_TIMEOUT)
    guild = bot.get_guild(guild_id)
    if guild and guild.voice_client:
        player = cast(wavelink.Player, guild.voice_client)
        if not player.playing:
            channel = player.home if hasattr(player, "home") else None
            await player.disconnect()
            if channel:
                await channel.send(
                    "15 dakika boyunca hareketsiz kaldım, bu yüzden ayrılıyorum."
                )


async def reset_standby(guild_id: int):
    if guild_id in standby_tasks:
        standby_tasks[guild_id].cancel()
    standby_tasks[guild_id] = asyncio.create_task(standby(guild_id))


class MusicControls(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.red)
    async def skip(self, interaction: discord.Interaction, button: Button):
        player = get_player(interaction)
        if player and player.playing:
            await player.skip(force=True)
            await interaction.response.send_message("Şarkı atlandı!", delete_after=2)
        else:
            await interaction.response.send_message(
                "Şu anda çalan bir şarkı yok.", delete_after=5
            )

    @discord.ui.button(label="Oynat/Duraklat", style=discord.ButtonStyle.blurple)
    async def pause_resume(self, interaction: discord.Interaction, button: Button):
        player = get_player(interaction)
        if player and player.playing:
            await player.pause(not player.paused)
            guild_id = interaction.guild.id
            if guild_id in current_messages:
                try:
                    msg = current_messages[guild_id]
                    embed = msg.embeds[0]
                    status = "⏸️ Paused" if player.paused else "▶️ Playing"
                    embed.set_field_at(0, name="Status", value=status, inline=True)
                    await msg.edit(embed=embed)
                except Exception:
                    pass
            state = "duraklatıldı" if player.paused else "devam ediyor"
            await interaction.response.send_message(f"Şarkı {state}.", delete_after=5)
        else:
            await interaction.response.send_message(
                "Şu anda çalan bir şarkı yok.", delete_after=5
            )

    @discord.ui.button(label="Durdur", style=discord.ButtonStyle.grey)
    async def stop(self, interaction: discord.Interaction, button: Button):
        player = get_player(interaction)
        if player and player.connected:
            player.queue.clear()
            await player.disconnect()
            guild_id = interaction.guild.id
            if guild_id in current_messages:
                try:
                    await current_messages[guild_id].delete()
                except Exception:
                    pass
                del current_messages[guild_id]
            await interaction.response.send_message(
                "Müzik durduruldu ve bağlantı kesildi."
            )
        else:
            await interaction.response.send_message(
                "Zaten bir ses kanalında değilim.", delete_after=5
            )

    @discord.ui.button(label="Siradakiler", style=discord.ButtonStyle.green)
    async def queue_list(self, interaction: discord.Interaction, button: Button):
        player = get_player(interaction)
        if not player or player.queue.is_empty:
            await interaction.response.send_message(
                "Kuyruk şu anda boş.", delete_after=5
            )
            return
        queue_str = "\n".join(
            [f"{i + 1}. {track.title}" for i, track in enumerate(player.queue)]
        )
        await interaction.response.send_message(
            f"Şarkı Kuyruğu:\n{queue_str}", delete_after=10
        )

    @discord.ui.button(label="Sirayi Temizle", style=discord.ButtonStyle.red)
    async def clear(self, interaction: discord.Interaction, button: Button):
        player = get_player(interaction)
        if player:
            player.queue.clear()
        await interaction.response.send_message("Kuyruk temizlendi.", delete_after=5)


class ExtraControls(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Loop", style=discord.ButtonStyle.blurple)
    async def loop(self, interaction: discord.Interaction, button: Button):
        guild_id = interaction.guild.id
        player = get_player(interaction)
        if player:
            current = is_looping.get(guild_id, False)
            is_looping[guild_id] = not current
            if is_looping[guild_id]:
                player.queue.mode = wavelink.QueueMode.loop
            else:
                player.queue.mode = wavelink.QueueMode.normal
        state = "açık" if is_looping.get(guild_id, False) else "kapalı"
        await interaction.response.send_message(f"Döngü modu {state}.", delete_after=5)

    @discord.ui.button(label="Shuffle", style=discord.ButtonStyle.blurple)
    async def shuffle(self, interaction: discord.Interaction, button: Button):
        guild_id = interaction.guild.id
        player = get_player(interaction)
        current = is_shuffled.get(guild_id, False)
        is_shuffled[guild_id] = not current
        if is_shuffled[guild_id] and player and not player.queue.is_empty:
            player.queue.shuffle()
        state = "açık" if is_shuffled[guild_id] else "kapalı"
        await interaction.response.send_message(
            f"Karıştırma modu {state}.", delete_after=5
        )

    @discord.ui.button(label="Durum", style=discord.ButtonStyle.green)
    async def status(self, interaction: discord.Interaction, button: Button):
        player = get_player(interaction)
        if not player or not player.connected:
            await interaction.response.send_message(
                "Şu anda bir ses kanalına bağlı değilim.", delete_after=5
            )
            return
        guild_id = interaction.guild.id
        status_msg = [
            f"Bağlı kanal: {player.channel.name}",
            f"Çalıyor: {player.playing}",
            f"Duraklatıldı: {player.paused}",
            f"Kuyrukta {player.queue.count} şarkı var",
            f"Karıştırılmış: {is_shuffled.get(guild_id, False)}",
            f"Döngü: {is_looping.get(guild_id, False)}",
            f"Ses seviyesi: {player.volume}%",
        ]
        await interaction.response.send_message("\n".join(status_msg), delete_after=10)

    @discord.ui.button(label="Ses Arttir", style=discord.ButtonStyle.grey)
    async def volume_up(self, interaction: discord.Interaction, button: Button):
        player = get_player(interaction)
        if not player or not player.playing:
            await interaction.response.send_message(
                "Şu anda müzik çalmıyor.", delete_after=5
            )
            return
        new_volume = min(100, player.volume + 10)
        await player.set_volume(new_volume)
        await interaction.response.send_message(
            f"Ses seviyesi {new_volume}% olarak ayarlandı.", delete_after=5
        )

    @discord.ui.button(label="Ses Dusur", style=discord.ButtonStyle.grey)
    async def volume_down(self, interaction: discord.Interaction, button: Button):
        player = get_player(interaction)
        if not player or not player.playing:
            await interaction.response.send_message(
                "Şu anda müzik çalmıyor.", delete_after=5
            )
            return
        new_volume = max(0, player.volume - 10)
        await player.set_volume(new_volume)
        await interaction.response.send_message(
            f"Ses seviyesi {new_volume}% olarak ayarlandı.", delete_after=5
        )


class SearchView(View):
    def __init__(self, search_results: list[wavelink.Playable], ctx):
        super().__init__(timeout=60)
        self.search_results = search_results
        self.ctx = ctx

        for i, track in enumerate(search_results[:5], 1):
            title = track.title[:50] + "..." if len(track.title) > 50 else track.title
            button = Button(
                label=f"{i}. {title}",
                style=discord.ButtonStyle.blurple,
                custom_id=str(i),
            )
            button.callback = self.button_callback
            self.add_item(button)

    async def button_callback(self, interaction: discord.Interaction):
        if interaction.user != self.ctx.author:
            await interaction.response.send_message(
                "Bu arama sizin değil!", ephemeral=True
            )
            return

        choice = int(interaction.data["custom_id"]) - 1
        selected_track = self.search_results[choice]

        player = get_player(interaction)
        if player:
            await player.queue.put_wait(selected_track)
            await interaction.response.send_message(
                f"{selected_track.title} kuyruğa eklendi."
            )
            if not player.playing:
                await player.play(player.queue.get(), volume=30)
        self.stop()


async def send_now_playing(player: wavelink.Player, track: wavelink.Playable):
    guild_id = player.guild.id

    if guild_id in current_messages:
        try:
            await current_messages[guild_id].delete()
        except Exception:
            pass

    embed = discord.Embed(
        title="Now Playing",
        description=track.title,
        color=discord.Color.blue(),
    )
    embed.add_field(name="Status", value="▶️ Playing", inline=True)
    embed.add_field(
        name="Queue", value=f"{player.queue.count} songs remaining", inline=True
    )
    if track.length:
        embed.add_field(
            name="Duration", value=format_duration(track.length), inline=True
        )
    if track.artwork:
        embed.set_thumbnail(url=track.artwork)
    if track.author:
        embed.add_field(name="Artist", value=track.author, inline=True)
    embed.set_footer(text="Use controls below to manage playback")

    if hasattr(player, "home") and player.home:
        current_messages[guild_id] = await player.home.send(
            embed=embed, view=MusicControls()
        )


@bot.event
async def on_wavelink_node_ready(payload: wavelink.NodeReadyEventPayload):
    logger.info(
        f"Wavelink node connected: {payload.node!r} | Resumed: {payload.resumed}"
    )


@bot.event
async def on_wavelink_track_start(payload: wavelink.TrackStartEventPayload):
    player = payload.player
    if player:
        await send_now_playing(player, payload.track)
        await reset_standby(player.guild.id)


@bot.event
async def on_wavelink_track_end(payload: wavelink.TrackEndEventPayload):
    player = payload.player
    if not player:
        return

    if player.queue.is_empty:
        guild_id = player.guild.id
        if hasattr(player, "home") and player.home:
            embed = discord.Embed(
                title="Queue Empty",
                description="No more songs in queue",
                color=discord.Color.red(),
            )
            if guild_id in current_messages:
                try:
                    await current_messages[guild_id].delete()
                except Exception:
                    pass
            current_messages[guild_id] = await player.home.send(embed=embed)
        await reset_standby(guild_id)


async def resolve_spotify(url_or_query: str) -> list[wavelink.Playable]:
    """Search via wavelink which handles Spotify through LavaSrc plugin."""
    tracks: wavelink.Search = await wavelink.Playable.search(url_or_query)
    if isinstance(tracks, wavelink.Playlist):
        return list(tracks.tracks)
    return list(tracks) if tracks else []


@bot.command()
async def play(ctx, *, url_or_query: str):
    try:
        if not ctx.author.voice or not ctx.author.voice.channel:
            await ctx.send("Önce bir ses kanalına gir.")
            return

        player: wavelink.Player
        if not ctx.voice_client:
            player = await ctx.author.voice.channel.connect(cls=wavelink.Player)
        else:
            player = cast(wavelink.Player, ctx.voice_client)

        if not hasattr(player, "home"):
            player.home = ctx.channel

        tracks: wavelink.Search = await wavelink.Playable.search(url_or_query)
        if not tracks:
            await ctx.send("Şarkı bulunamadı.")
            return

        if isinstance(tracks, wavelink.Playlist):
            added = await player.queue.put_wait(tracks)
            await ctx.send(
                f"**{tracks.name}** playlistinden {added} şarkı kuyruğa eklendi."
            )
        else:
            track = tracks[0]
            await player.queue.put_wait(track)
            await ctx.send(f"**{track.title}** kuyruğa eklendi.")

        if not player.playing:
            await player.play(player.queue.get(), volume=30)

        await ctx.send("Extra controls:", view=ExtraControls())
        await reset_standby(ctx.guild.id)

    except Exception as e:
        logger.error(f"Error in play command: {e}")
        await ctx.send(f"Bir hata oluştu: {str(e)}")


@bot.command()
async def playnext(ctx, *, url_or_query: str):
    try:
        if not ctx.author.voice or not ctx.author.voice.channel:
            await ctx.send("Önce bir ses kanalına gir.")
            return

        player: wavelink.Player
        if not ctx.voice_client:
            player = await ctx.author.voice.channel.connect(cls=wavelink.Player)
        else:
            player = cast(wavelink.Player, ctx.voice_client)

        if not hasattr(player, "home"):
            player.home = ctx.channel

        tracks: wavelink.Search = await wavelink.Playable.search(url_or_query)
        if not tracks:
            await ctx.send("Şarkı bulunamadı.")
            return

        if isinstance(tracks, wavelink.Playlist):
            await ctx.send("Bu komut sadece tekli şarkılar için kullanılabilir.")
            return

        track = tracks[0]
        player.queue.put_at(0, track)
        await ctx.send(f"**{track.title}** kuyruğun başına eklendi.")

        if not player.playing:
            await player.play(player.queue.get(), volume=30)

        await reset_standby(ctx.guild.id)

    except Exception as e:
        logger.error(f"Error in playnext command: {e}")
        await ctx.send(f"Bir hata oluştu: {str(e)}")


@bot.command()
async def playnow(ctx, *, url_or_query: str):
    try:
        if not ctx.author.voice or not ctx.author.voice.channel:
            await ctx.send("Önce bir ses kanalına gir.")
            return

        player: wavelink.Player
        if not ctx.voice_client:
            player = await ctx.author.voice.channel.connect(cls=wavelink.Player)
        else:
            player = cast(wavelink.Player, ctx.voice_client)

        if not hasattr(player, "home"):
            player.home = ctx.channel

        tracks: wavelink.Search = await wavelink.Playable.search(url_or_query)
        if not tracks:
            await ctx.send("Şarkı bulunamadı.")
            return

        if isinstance(tracks, wavelink.Playlist):
            await ctx.send("Bu komut sadece tekli şarkılar için kullanılabilir.")
            return

        track = tracks[0]
        await player.play(track, volume=30)
        await ctx.send(f"**{track.title}** şimdi çalınıyor.")
        await ctx.send("Extra controls:", view=ExtraControls())
        await reset_standby(ctx.guild.id)

    except Exception as e:
        logger.error(f"Error in playnow command: {e}")
        await ctx.send(f"Bir hata oluştu: {str(e)}")


@bot.command()
async def controls(ctx):
    await ctx.send("Müzik kontrolleri:", view=MusicControls())
    await ctx.send("Ekstra kontroller:", view=ExtraControls())


@bot.command()
async def move(ctx, from_pos: int, to_pos: int):
    player = get_player(ctx)
    if not player or player.queue.is_empty:
        await ctx.send("Kuyruk boş.")
        return
    if (
        from_pos < 1
        or to_pos < 1
        or from_pos > player.queue.count
        or to_pos > player.queue.count
    ):
        await ctx.send("Geçersiz sıra numarası.")
        return
    track = player.queue.peek(from_pos - 1)
    player.queue.delete(from_pos - 1)
    player.queue.put_at(to_pos - 1, track)
    await ctx.send(f"{track.title} {to_pos}. sıraya taşındı.")


@bot.command()
async def remove(ctx, pos: int):
    player = get_player(ctx)
    if not player or player.queue.is_empty:
        await ctx.send("Kuyruk boş.")
        return
    if pos < 1 or pos > player.queue.count:
        await ctx.send("Geçersiz sıra numarası.")
        return
    track = player.queue.peek(pos - 1)
    player.queue.delete(pos - 1)
    await ctx.send(f"{track.title} kuyruktan silindi.")


@bot.command()
async def search(ctx, *, query: str):
    try:
        tracks: wavelink.Search = await wavelink.Playable.search(query)
        if not tracks or isinstance(tracks, wavelink.Playlist):
            await ctx.send("Arama sonucunda şarkı bulunamadı.")
            return

        results = list(tracks[:5])
        view = SearchView(results, ctx)
        embed = discord.Embed(
            title=f"'{query}' için Arama Sonuçları",
            description="Aşağıdaki butonlardan birini seçerek şarkıyı kuyruğa ekleyebilirsiniz.",
            color=discord.Color.green(),
        )
        for i, track in enumerate(results, 1):
            duration = format_duration(track.length) if track.length else "Bilinmiyor"
            embed.add_field(
                name=f"{i}. {track.title}",
                value=f"Süre: {duration} | {track.author}",
                inline=False,
            )
        await ctx.send(embed=embed, view=view)

    except Exception as e:
        logger.error(f"Error in search command: {e}")
        await ctx.send(f"Bir hata oluştu: {str(e)}")


@bot.command()
async def help(ctx):
    embed = discord.Embed(
        title="Bot Komutları",
        description="Aşağıda botun tüm komutları ve kullanım örnekleri listelenmiştir.",
        color=discord.Color.blue(),
    )
    embed.add_field(
        name="!play <şarkı adı veya URL>",
        value="Bir şarkıyı veya Spotify çalma listesini kuyruğun sonuna ekler.\n**Örnek:**\n- `!play NTO Beyond Control`\n- `!play https://open.spotify.com/track/xxx`\n- `!play https://open.spotify.com/playlist/xxx`",
        inline=False,
    )
    embed.add_field(
        name="!playnext <şarkı adı veya URL>",
        value="Bir şarkıyı kuyruğun başına ekler.\n**Örnek:**\n- `!playnext NTO Beyond Control`\n- `!playnext https://open.spotify.com/track/xxx`",
        inline=False,
    )
    embed.add_field(
        name="!playnow <şarkı adı veya URL>",
        value="Mevcut şarkıyı keser ve yeni şarkıyı hemen çalar.\n**Örnek:**\n- `!playnow NTO Beyond Control`\n- `!playnow https://open.spotify.com/track/xxx`",
        inline=False,
    )
    embed.add_field(
        name="!search <şarkı adı>",
        value="Şarkı arar ve ilk 5 sonucu butonlarla listeler.\n**Örnek:**\n- `!search NTO Beyond Control`",
        inline=False,
    )
    embed.add_field(
        name="!controls",
        value="Müzik kontrol butonlarını gösterir.\n**Örnek:**\n- `!controls`",
        inline=False,
    )
    embed.add_field(
        name="!move <başlangıç sırası> <hedef sıra>",
        value="Kuyruktaki bir şarkıyı başka bir sıraya taşır.\n**Örnek:**\n- `!move 3 1`",
        inline=False,
    )
    embed.add_field(
        name="!remove <sıra numarası>",
        value="Kuyruktaki belirli bir şarkıyı siler.\n**Örnek:**\n- `!remove 2`",
        inline=False,
    )
    embed.set_footer(text="Not: Botu kullanmadan önce bir ses kanalına girmelisiniz.")
    await ctx.send(embed=embed)


@bot.event
async def on_ready():
    logger.info(f"Bot {bot.user} olarak bağlandı!")


async def setup_hook():
    nodes = [
        wavelink.Node(
            uri=LAVALINK_URI,
            password=LAVALINK_PASSWORD,
        )
    ]
    await wavelink.Pool.connect(nodes=nodes, client=bot, cache_capacity=100)


bot.setup_hook = setup_hook


async def health_handler(request):
    return web.Response(text="OK")


async def start_health_server():
    app = web.Application()
    app.router.add_get("/", health_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", 8000)))
    await site.start()
    logger.info("Health check server started on port 8000")


async def self_ping():
    await asyncio.sleep(30)
    port = int(os.getenv("PORT", 8000))
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(f"http://localhost:{port}/") as resp:
                    logger.info(f"Self-ping: {resp.status}")
            except Exception as e:
                logger.error(f"Self-ping failed: {e}")
            await asyncio.sleep(300)


async def main():
    await start_health_server()
    asyncio.create_task(self_ping())
    await bot.start(DISCORD_TOKEN)


asyncio.run(main())
