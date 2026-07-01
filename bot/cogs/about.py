"""Bot profile card (/botinfo) and a live, rotating presence."""
from __future__ import annotations

import platform
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
from core import embeds
from core import version


def _humanize_uptime(delta_seconds: float) -> str:
    seconds = int(delta_seconds)
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    parts = []
    if days:
        parts.append(f"{days} д")
    if hours:
        parts.append(f"{hours} ч")
    if minutes:
        parts.append(f"{minutes} мин")
    if not parts:
        parts.append(f"{seconds} с")
    return " ".join(parts)


class About(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._presence_index = 0
        self.rotate_presence.start()

    async def cog_unload(self) -> None:
        self.rotate_presence.cancel()

    # ---- live presence --------------------------------------------------

    @tasks.loop(seconds=30)
    async def rotate_presence(self) -> None:
        members = sum(g.member_count or 0 for g in self.bot.guilds)
        statuses = [
            (discord.ActivityType.watching, "Moderation | Tickets | Events"),
            (discord.ActivityType.watching, f"{len(self.bot.guilds)} сервер(ов) • /help"),
            (discord.ActivityType.listening, f"{members} участников"),
            (discord.ActivityType.watching, "giveaways, economy, analytics"),
            (discord.ActivityType.playing, "with server systems"),
            (discord.ActivityType.listening, "commands • /help"),
        ]
        kind, name = statuses[self._presence_index % len(statuses)]
        self._presence_index += 1
        try:
            await self.bot.change_presence(activity=discord.Activity(type=kind, name=name))
        except discord.HTTPException:
            pass

    @rotate_presence.before_loop
    async def _before(self) -> None:
        await self.bot.wait_until_ready()

    # ---- profile card ---------------------------------------------------

    @commands.hybrid_command(name="botinfo", aliases=["bot", "бот"],
                             description="Профиль бота: разработчик, аптайм и статистика")
    @commands.guild_only()
    async def botinfo(self, ctx: commands.Context) -> None:
        bot = self.bot
        me = bot.user
        members = sum(g.member_count or 0 for g in bot.guilds)
        uptime = (discord.utils.utcnow() - bot.start_time).total_seconds()  # type: ignore[attr-defined]
        ping = round(bot.latency * 1000)

        embed = discord.Embed(
            title=f"{me.display_name} — профиль" if me else "Профиль бота",
            description=(
                "Многофункциональный бот: модерация, тикеты, роли, уровни, "
                "анти-фишинг и звёздная доска.\n"
                "Справка — `/help`, настройка — `/settings` и `/config`."
            ),
            color=config.EMBED_COLOR,
        )
        if me is not None:
            embed.set_thumbnail(url=me.display_avatar.url)

        embed.add_field(name="👨‍💻 Разработчик", value=f"<@{config.DEVELOPER_ID}>", inline=True)
        embed.add_field(name="⏱️ Аптайм", value=_humanize_uptime(uptime), inline=True)
        embed.add_field(name="📡 Пинг", value=f"{ping} мс", inline=True)
        embed.add_field(name="🏠 Серверов", value=str(len(bot.guilds)), inline=True)
        embed.add_field(name="👥 Участников", value=str(members), inline=True)
        embed.add_field(name="⚙️ Команд", value=str(getattr(bot, "app_command_count", 0)), inline=True)
        embed.add_field(
            name="🧩 Технологии",
            value=f"discord.py {discord.__version__} · Python {platform.python_version()}",
            inline=False,
        )
        embed.set_footer(text="K2-SO • сделано с ❤️ от JapanDino")

        view = discord.ui.View()
        view.add_item(discord.ui.Button(
            label="Пригласить бота", emoji="➕",
            style=discord.ButtonStyle.link, url=config.INVITE_URL,
        ))
        view.add_item(discord.ui.Button(
            label="Разработчик", emoji="👨‍💻",
            style=discord.ButtonStyle.link,
            url=f"https://discord.com/users/{config.DEVELOPER_ID}",
        ))
        await ctx.reply(embed=embed, view=view, mention_author=False)

    @commands.hybrid_command(name="buildinfo", description="Показать версию кода, запущенную на хостинге")
    @commands.guild_only()
    async def buildinfo(self, ctx: commands.Context) -> None:
        banner_dir = Path(__file__).resolve().parent.parent / "assets" / "banners"
        banner_files = sorted(p.name for p in banner_dir.glob("*.png")) if banner_dir.exists() else []
        embed = embeds.base(
            title="Build info",
            description=(
                f"Marker: `{version.BUILD_MARKER}`\n"
                f"Database: `{config.DATABASE_PATH}`\n"
                f"CWD: `{Path.cwd()}`\n"
                f"Banner dir: `{banner_dir}`\n"
                f"Banners: `{len(banner_files)}`"
            ),
        )
        if banner_files:
            embed.add_field(name="Banner files", value="\n".join(f"`{name}`" for name in banner_files), inline=False)
        await ctx.reply(embed=embed, mention_author=False)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(About(bot))
