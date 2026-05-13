"""
Discord Proxy Bot — передатчик между Rust-плагином и Discord.
pip install discord.py aiohttp
"""

import asyncio
import base64
import io
import logging
import os
import aiohttp
from aiohttp import web
import discord
from discord import app_commands
from discord.ext import commands

# ─── КОНФИГ ──────────────────────────────────────────────────────────────────
BOT_TOKEN = "MTMxMjQxMzA3MDY0NzIzMDUxNQ.G36nRV.Ll-AogX8Uxj2kizg1tGs2UXM-15YxtAC14Bues"

SCREENSHOT_CHANNEL_ID  = 1496897127010537531
SCREENSHOT_MODER_CH_ID = 1496897127010537529
BAN_LOG_CHANNEL_ID     = 1496897127190761735

API_SECRET      = "xK9mP2qR7vL4nZ1w"
HTTP_PORT       = int(os.environ.get("PORT", 8765))  # Railway сам задаёт PORT
RUST_SERVER_URL = "http://178.35.149.13:8766"        # не используется (polling)
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("proxy")

# Очередь команд для плагина
command_queue = []

intents = discord.Intents.default()
intents.guilds = True

class ACoreBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        # Синхронизируем slash-команды глобально
        await self.tree.sync()
        log.info("Slash commands synced")

bot = ACoreBot()


def check_auth(request: web.Request) -> bool:
    return request.headers.get("X-Secret") == API_SECRET


# ─── Slash-команды ────────────────────────────────────────────────────────────
def is_admin():
    async def predicate(interaction: discord.Interaction) -> bool:
        return interaction.user.guild_permissions.administrator
    return app_commands.check(predicate)


@bot.tree.command(name="screenshot", description="Запросить скриншот игрока")
@app_commands.describe(steamid="Steam ID игрока")
@is_admin()
async def cmd_screenshot(interaction: discord.Interaction, steamid: str):
    await interaction.response.defer(ephemeral=True)
    msg = await send_to_rust("screen", steamid, str(interaction.channel_id))
    await interaction.followup.send(msg, ephemeral=True)


@bot.tree.command(name="hwid", description="Управление HWID банами")
@app_commands.describe(action="ban или unban", steamid="Steam ID игрока", reason="Причина бана")
@app_commands.choices(action=[
    app_commands.Choice(name="ban",   value="ban"),
    app_commands.Choice(name="unban", value="unban"),
])
@is_admin()
async def cmd_hwid(interaction: discord.Interaction, action: str, steamid: str, reason: str = "Banned By AntiCheat"):
    await interaction.response.defer(ephemeral=True)
    msg = await send_to_rust(action, steamid, str(interaction.channel_id), reason)
    await interaction.followup.send(msg, ephemeral=True)


@bot.tree.command(name="showcheck", description="Статистика проверок модератора")
@app_commands.describe(steamid="Steam ID модератора")
@is_admin()
async def cmd_showcheck(interaction: discord.Interaction, steamid: str):
    await interaction.response.defer(ephemeral=True)
    msg = await send_to_rust("showcheck", steamid, str(interaction.channel_id))
    await interaction.followup.send(msg, ephemeral=True)


async def send_to_rust(action: str, user_id: str, channel_id: str, reason: str = "") -> str:
    # Кладём команду в очередь — плагин заберёт сам через polling
    command_queue.append({
        "action":     action,
        "user_id":    user_id,
        "channel_id": channel_id,
        "reason":     reason
    })
    log.info(f"Command queued: {action} for {user_id}")
    return f"Команда {action} поставлена в очередь для игрока {user_id}"


# ─── HTTP: приём скриншота от плагина ────────────────────────────────────────
async def handle_screenshot(request: web.Request) -> web.Response:
    if not check_auth(request):
        return web.json_response({"error": "Unauthorized"}, status=401)
    try:
        data        = await request.json()
        user_id     = int(data["user_id"])
        player_name = data.get("player_name", "Unknown")
        image_b64   = data["image_base64"]
        channel_id  = int(data.get("channel_id", SCREENSHOT_CHANNEL_ID))
        is_moder    = data.get("is_moder", False)

        image_bytes = base64.b64decode(image_b64)
        file = discord.File(io.BytesIO(image_bytes), filename="screenshot.png")

        embed = discord.Embed(color=0x5865F2)
        embed.add_field(name="SteamID",    value=str(user_id), inline=True)
        embed.add_field(name="Имя игрока", value=player_name,  inline=True)
        embed.set_image(url="attachment://screenshot.png")

        link_embed = discord.Embed()
        link_embed.set_author(
            name="Кликни сюда чтобы перейти в профиль игрока",
            url=f"https://steamcommunity.com/profiles/{user_id}/",
            icon_url="https://upload.wikimedia.org/wikipedia/commons/thumb/8/83/Steam_icon_logo.svg/2048px-Steam_icon_logo.svg.png"
        )

        view = ScreenshotView(user_id, is_moder)
        channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
        await channel.send(embeds=[embed, link_embed], file=file, view=view)

        log.info(f"Screenshot sent: user={user_id} channel={channel_id}")
        return web.json_response({"ok": True})
    except Exception as e:
        log.error(f"handle_screenshot error: {e}")
        return web.json_response({"error": str(e)}, status=500)


# ─── HTTP: плагин забирает команды (polling) ─────────────────────────────────
async def handle_poll(request: web.Request) -> web.Response:
    if not check_auth(request):
        return web.json_response({"error": "Unauthorized"}, status=401)
    # Отдаём все накопленные команды и очищаем очередь
    commands = list(command_queue)
    command_queue.clear()
    return web.json_response({"commands": commands})


# ─── HTTP: приём бан-лога от плагина ─────────────────────────────────────────
async def handle_ban_log(request: web.Request) -> web.Response:
    if not check_auth(request):
        return web.json_response({"error": "Unauthorized"}, status=401)
    try:
        data    = await request.json()
        user_id = int(data["user_id"])
        reason  = data.get("reason", "")
        is_ban  = data.get("is_ban", True)

        embed = discord.Embed(
            color=0xFF0000 if is_ban else 0x00FF00,
            description="Игрок забанен" if is_ban else "Игрок разбанен"
        )
        embed.add_field(name="SteamID", value=f"`{user_id}`", inline=True)
        if is_ban and reason:
            embed.add_field(name="Причина", value=reason, inline=False)

        link_embed = discord.Embed()
        link_embed.set_author(
            name="Перейти в профиль игрока",
            url=f"https://steamcommunity.com/profiles/{user_id}/",
            icon_url="https://upload.wikimedia.org/wikipedia/commons/thumb/8/83/Steam_icon_logo.svg/2048px-Steam_icon_logo.svg.png"
        )

        channel = bot.get_channel(BAN_LOG_CHANNEL_ID) or await bot.fetch_channel(BAN_LOG_CHANNEL_ID)
        await channel.send(embeds=[embed, link_embed])

        log.info(f"Ban log: user={user_id} is_ban={is_ban}")
        return web.json_response({"ok": True})
    except Exception as e:
        log.error(f"handle_ban_log error: {e}")
        return web.json_response({"error": str(e)}, status=500)


# ─── Кнопки ──────────────────────────────────────────────────────────────────
class ScreenshotView(discord.ui.View):
    def __init__(self, user_id: int, is_moder: bool):
        super().__init__(timeout=None)
        if not is_moder:
            self.add_item(ActionButton("BAN",   discord.ButtonStyle.danger,  f"ban_{user_id}"))
            self.add_item(ActionButton("UNBAN", discord.ButtonStyle.success, f"unban_{user_id}"))
        self.add_item(ActionButton("SCREEN", discord.ButtonStyle.primary, f"screen_{user_id}"))


class ActionButton(discord.ui.Button):
    def __init__(self, label: str, style: discord.ButtonStyle, custom_id: str):
        super().__init__(label=label, style=style, custom_id=custom_id)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        if not interaction.user.guild_permissions.administrator:
            await interaction.followup.send("У вас нет прав!", ephemeral=True)
            return

        parts = self.custom_id.split("_")
        action, user_id_str = parts[0], parts[1]
        msg = await send_to_rust(action, user_id_str, str(interaction.channel_id))
        await interaction.followup.send(msg, ephemeral=True)


# ─── События ─────────────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    bot.add_view(ScreenshotView(0, False))
    bot.add_view(ScreenshotView(0, True))
    log.info(f"Bot online: {bot.user} (id={bot.user.id})")


# ─── HTTP-сервер ─────────────────────────────────────────────────────────────
async def start_http_server():
    app = web.Application(client_max_size=50 * 1024 * 1024)  # 50MB для скриншотов
    app.router.add_post("/send_screenshot", handle_screenshot)
    app.router.add_post("/send_ban_log",    handle_ban_log)
    app.router.add_get("/poll",             handle_poll)      # плагин забирает команды
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", HTTP_PORT)
    await site.start()
    log.info(f"HTTP server listening on :{HTTP_PORT}")


async def main():
    async with bot:
        await start_http_server()
        await bot.start(BOT_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
