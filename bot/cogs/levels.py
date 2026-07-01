"""Activity levels / XP.

Members earn XP for chatting (rate-limited per minute). Levels use a rising
curve; reaching a level can grant a reward role. Includes /rank and
/leaderboard plus a /levels config group.
"""
from __future__ import annotations

import io
import random
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from core import embeds, rankcard
from core.permissions import requires

XP_COOLDOWN = 60          # seconds between XP-earning messages
XP_MIN, XP_MAX = 15, 25   # XP awarded per eligible message


def xp_needed(level: int) -> int:
    """XP required to advance from ``level`` to ``level + 1``."""
    return 5 * (level ** 2) + 50 * level + 100


def progress(total_xp: int) -> tuple[int, int, int]:
    """Return (level, xp_into_level, xp_for_next) for a total XP amount."""
    level, remaining = 0, total_xp
    need = xp_needed(0)
    while remaining >= need:
        remaining -= need
        level += 1
        need = xp_needed(level)
    return level, remaining, need


def _bar(current: int, total: int, width: int = 12) -> str:
    filled = int(width * current / total) if total else 0
    return "█" * filled + "░" * (width - filled)


def _parse_hex_color(value: Optional[str]) -> Optional[tuple[int, int, int]]:
    if not value:
        return None
    raw = value.strip().lstrip("#")
    if len(raw) != 6:
        return None
    try:
        return tuple(int(raw[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return None


def _normalize_hex(value: str) -> Optional[str]:
    color = _parse_hex_color(value)
    if color is None:
        return None
    return "#{:02X}{:02X}{:02X}".format(*color)


def _discord_color(rgb: tuple[int, int, int]) -> discord.Colour:
    return discord.Colour((rgb[0] << 16) + (rgb[1] << 8) + rgb[2])


class Levels(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        if not isinstance(message.author, discord.Member):
            return
        cfg = await self.bot.db.get_guild_config(message.guild.id)
        if not cfg["levels_enabled"]:
            return

        row = await self.bot.db.get_level_row(message.guild.id, message.author.id)
        now = int(time.time())
        if now - row["last_msg_ts"] < XP_COOLDOWN:
            return

        old_level = row["level"]
        new_total = row["xp"] + random.randint(XP_MIN, XP_MAX)
        new_level, _, _ = progress(new_total)
        await self.bot.db.update_level_row(
            message.guild.id, message.author.id, new_total, new_level, now
        )
        if new_level > old_level:
            # Level-up is silent (no chat message); only grant reward roles.
            await self._grant_level_rewards(message.author, new_level)

    async def _grant_level_rewards(self, member: discord.Member, level: int) -> None:
        for reward in await self.bot.db.get_level_rewards(member.guild.id):
            if reward["level"] == level:
                role = member.guild.get_role(reward["role_id"])
                if role and role < member.guild.me.top_role:
                    try:
                        await member.add_roles(role, reason=f"level {level} reward")
                    except discord.HTTPException:
                        pass

    # ---- public commands ------------------------------------------------

    @commands.hybrid_command(name="rank", aliases=["ранг", "уровень"],
                             description="Показать ваш уровень и опыт")
    @app_commands.describe(user="Чей ранг (по умолчанию — ваш)")
    @commands.guild_only()
    async def rank(self, ctx: commands.Context, user: Optional[discord.Member] = None) -> None:
        guild = ctx.guild
        member = user or ctx.author
        row = await self.bot.db.get_level_row(guild.id, member.id)
        level, into, need = progress(row["xp"])
        rank_pos = await self.bot.db.get_rank(guild.id, member.id)
        prefs = await self.bot.db.get_rank_prefs(member.id)
        pref_accent = _parse_hex_color(prefs["accent"]) if prefs is not None else None
        pref_bg = _parse_hex_color(prefs["bg"]) if prefs is not None else None
        accent = pref_accent or (member.color.to_rgb() if member.color.value else (88, 101, 242))

        # Try a generated rank-card image; fall back to an embed on any failure.
        try:
            avatar_bytes = await member.display_avatar.replace(size=128).read()
            data = await rankcard.render(avatar_bytes, member.display_name, level, rank_pos,
                                         into, need, row["xp"], accent, pref_bg)
            file = discord.File(io.BytesIO(data), filename="rank.png")
            await ctx.reply(file=file, mention_author=False)
            return
        except Exception:
            pass

        embed = discord.Embed(color=_discord_color(accent))
        embed.set_author(name=f"Ранг · {member.display_name}", icon_url=member.display_avatar.url)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Уровень", value=f"**{level}**", inline=True)
        embed.add_field(name="Место", value=f"#{rank_pos}", inline=True)
        embed.add_field(name="Всего XP", value=str(row["xp"]), inline=True)
        embed.add_field(name=f"Прогресс — {into}/{need} XP", value=f"`{_bar(into, need)}`", inline=False)
        await ctx.reply(embed=embed, mention_author=False)

    async def _leaderboard_payload(self, guild: discord.Guild, kind: str):
        if kind == "money":
            rows = await self.bot.db.econ_leaderboard(guild.id, 10)
            cfg = await self.bot.db.get_econ_config(guild.id)
            cur = cfg["currency_name"]
            data = [(r["user_id"], r["shop_wallet"] + r["safe_balance"]) for r in rows]
            title, subtitle = "Топ по балансу", f"валюта: {cur}"
            valfmt = lambda v: f"{v:,}".replace(",", " ") + f" {cur}"
        else:
            rows = await self.bot.db.leaderboard(guild.id, 10)
            data = [(r["user_id"], r["xp"]) for r in rows]
            title, subtitle = "Топ по опыту", "за всё время"
            valfmt = lambda v: f"{v:,}".replace(",", " ") + " XP"
        if not rows:
            return {"embed": embeds.base(title=f"🏆 {title}", description="Пока топ пуст.")}

        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for index, (uid, value) in enumerate(data):
            place = medals[index] if index < 3 else f"**{index + 1}.**"
            member = guild.get_member(uid)
            name = member.mention if member is not None else f"<@{uid}>"
            lines.append(f"{place} {name} — {valfmt(value)}")
        embed = embeds.base(title=f"🏆 {title}", description="\n".join(lines))
        embed.set_footer(text=subtitle)
        return {"embed": embed}

    @commands.hybrid_command(name="leaderboard", aliases=["lvltop", "топ", "топуровней"],
                             description="Топ участников с выбором критерия")
    @commands.guild_only()
    async def leaderboard(self, ctx: commands.Context) -> None:
        assert ctx.guild is not None
        payload = await self._leaderboard_payload(ctx.guild, "xp")
        await ctx.reply(**payload, view=LeaderboardView(self), mention_author=False)

    # ---- config ---------------------------------------------------------

    levels = app_commands.Group(name="levels", description="Настройка системы уровней", guild_only=True)
    reward = app_commands.Group(name="reward", parent=levels, description="Награды-роли за уровни")
    rankcard_config = app_commands.Group(name="rankcard", description="Кастомизация rank-карточки", guild_only=True)

    @rankcard_config.command(name="colors", description="Настроить цвета своей rank-карточки")
    @app_commands.describe(
        accent="Акцентный цвет в формате #5865F2",
        background="Фон карточки в формате #1A1C21",
    )
    async def rankcard_colors(
        self,
        interaction: discord.Interaction,
        accent: Optional[str] = None,
        background: Optional[str] = None,
    ) -> None:
        if accent is None and background is None:
            await interaction.response.send_message(
                embed=embeds.warn("Укажите хотя бы один цвет: `accent` или `background`."),
                ephemeral=True,
            )
            return
        normalized_accent = _normalize_hex(accent) if accent is not None else None
        normalized_bg = _normalize_hex(background) if background is not None else None
        if accent is not None and normalized_accent is None:
            await interaction.response.send_message(
                embed=embeds.error("Акцент должен быть hex-цветом, например `#5865F2`."),
                ephemeral=True,
            )
            return
        if background is not None and normalized_bg is None:
            await interaction.response.send_message(
                embed=embeds.error("Фон должен быть hex-цветом, например `#1A1C21`."),
                ephemeral=True,
            )
            return
        await self.bot.db.set_rank_prefs(
            interaction.user.id, accent=normalized_accent, bg=normalized_bg
        )
        await interaction.response.send_message(
            embed=embeds.success("Цвета rank-карточки обновлены. Проверьте через `/rankcard preview`."),
            ephemeral=True,
        )

    @rankcard_config.command(name="reset", description="Сбросить кастомизацию rank-карточки")
    async def rankcard_reset(self, interaction: discord.Interaction) -> None:
        await self.bot.db.reset_rank_prefs(interaction.user.id)
        await interaction.response.send_message(
            embed=embeds.success(f"{interaction.user.mention} сбросил кастомизацию rank-карточки."),
        )

    @rankcard_config.command(name="preview", description="Предпросмотр своей rank-карточки")
    async def rankcard_preview(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.user, discord.Member) or interaction.guild is None:
            return
        await interaction.response.defer(ephemeral=True)
        member = interaction.user
        row = await self.bot.db.get_level_row(interaction.guild.id, member.id)
        level, into, need = progress(row["xp"])
        rank_pos = await self.bot.db.get_rank(interaction.guild.id, member.id)
        prefs = await self.bot.db.get_rank_prefs(member.id)
        pref_accent = _parse_hex_color(prefs["accent"]) if prefs is not None else None
        pref_bg = _parse_hex_color(prefs["bg"]) if prefs is not None else None
        accent = pref_accent or (member.color.to_rgb() if member.color.value else (88, 101, 242))
        try:
            avatar_bytes = await member.display_avatar.replace(size=128).read()
            data = await rankcard.render(
                avatar_bytes, member.display_name, level, rank_pos,
                into, need, row["xp"], accent, pref_bg,
            )
            await interaction.followup.send(
                file=discord.File(io.BytesIO(data), filename="rank-preview.png"),
                ephemeral=True,
            )
        except Exception:
            await interaction.followup.send(
                embed=embeds.error("Не удалось собрать preview rank-карточки."),
                ephemeral=True,
            )

    @levels.command(name="toggle", description="Включить/выключить систему уровней")
    @app_commands.describe(enabled="Включить?")
    @requires("levels")
    async def levels_toggle(self, interaction: discord.Interaction, enabled: bool) -> None:
        await self.bot.db.update_guild_config(interaction.guild_id, levels_enabled=int(enabled))
        await interaction.response.send_message(
            embed=embeds.success(f"Система уровней {'включена' if enabled else 'выключена'}."),
            ephemeral=True,
        )

    @levels.command(name="channel", description="Канал для сообщений о повышении уровня")
    @app_commands.describe(channel="Канал (не указывать — писать в текущем канале)")
    @requires("levels")
    async def levels_channel(
        self, interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None
    ) -> None:
        await self.bot.db.update_guild_config(
            interaction.guild_id, levelup_channel_id=channel.id if channel else None
        )
        msg = f"Сообщения о level-up: {channel.mention}" if channel else "Level-up в текущем канале."
        await interaction.response.send_message(embed=embeds.success(msg), ephemeral=True)

    @reward.command(name="add", description="Назначить роль-награду за уровень")
    @app_commands.describe(level="Уровень", role="Роль-награда")
    @requires("levels")
    async def reward_add(
        self, interaction: discord.Interaction,
        level: app_commands.Range[int, 1, 1000], role: discord.Role,
    ) -> None:
        guild = interaction.guild
        assert guild
        if role >= guild.me.top_role:
            await interaction.response.send_message(
                embed=embeds.error("Роль выше роли бота."), ephemeral=True
            )
            return
        await self.bot.db.set_level_reward(guild.id, level, role.id)
        await interaction.response.send_message(
            embed=embeds.success(f"За {level} уровень → {role.mention}."), ephemeral=True
        )

    @reward.command(name="remove", description="Убрать награду за уровень")
    @app_commands.describe(level="Уровень")
    @requires("levels")
    async def reward_remove(
        self, interaction: discord.Interaction, level: app_commands.Range[int, 1, 1000]
    ) -> None:
        n = await self.bot.db.remove_level_reward(interaction.guild_id, level)
        msg = "Награда убрана." if n else "Для этого уровня награды нет."
        await interaction.response.send_message(embed=embeds.success(msg), ephemeral=True)

    @reward.command(name="list", description="Показать награды за уровни")
    @requires("levels")
    async def reward_list(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        assert guild
        rewards = await self.bot.db.get_level_rewards(guild.id)
        if not rewards:
            await interaction.response.send_message(
                embed=embeds.base(description="Награды не настроены."), ephemeral=True
            )
            return
        lines = []
        for r in rewards:
            role = guild.get_role(r["role_id"])
            lines.append(f"Уровень **{r['level']}** → {role.mention if role else 'удалённая роль'}")
        await interaction.response.send_message(
            embed=embeds.base(title="🎖️ Награды за уровни", description="\n".join(lines)),
            ephemeral=True,
        )


class LeaderboardKindSelect(discord.ui.Select):
    def __init__(self) -> None:
        super().__init__(
            placeholder="Выберите критерий топа...",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label="Опыт", value="xp", emoji="⭐", description="Топ участников по XP"),
                discord.SelectOption(label="Баланс", value="money", emoji="💰", description="Топ по админ-валюте"),
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or self.view is None or interaction.message is None:
            return
        view: LeaderboardView = self.view  # type: ignore[assignment]
        payload = await view.cog._leaderboard_payload(interaction.guild, self.values[0])
        for option in self.options:
            option.default = option.value == self.values[0]
        await interaction.response.defer()
        await interaction.message.edit(
            embed=payload.get("embed"),
            attachments=[payload["file"]] if "file" in payload else [],
            view=view,
        )


class LeaderboardView(discord.ui.View):
    def __init__(self, cog: Levels) -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.add_item(LeaderboardKindSelect())


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Levels(bot))
