"""Server analytics collectors and reports."""
from __future__ import annotations

from datetime import datetime, timezone
import logging
import sqlite3

import discord
from discord import app_commands
from discord.ext import commands

import config
from core import embeds
from core.permissions import requires_hybrid

log = logging.getLogger("samurai.analytics")


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _bar(value: int, max_value: int, width: int = 10) -> str:
    if max_value <= 0:
        return "░" * width
    filled = round((value / max_value) * width)
    return "█" * filled + "░" * (width - filled)


class Analytics(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _inc(self, guild_id: int, field: str) -> None:
        try:
            await self.bot.db.increment_server_stat(guild_id, _today(), field)
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).lower():
                log.warning("Skipped analytics increment %s for guild %s: database is locked", field, guild_id)
                return
            raise

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return
        await self._inc(message.guild.id, "messages")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        await self._inc(member.guild.id, "members_joined")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        await self._inc(member.guild.id, "members_left")

    @commands.Cog.listener()
    async def on_command_completion(self, ctx: commands.Context) -> None:
        if ctx.guild is not None:
            await self._inc(ctx.guild.id, "commands_used")

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        if interaction.type is discord.InteractionType.application_command:
            await self._inc(interaction.guild.id, "commands_used")

    @commands.hybrid_group(
        name="analytics",
        aliases=["stats", "статистика"],
        description="Аналитика сервера",
        invoke_without_command=True,
    )
    @commands.guild_only()
    @requires_hybrid("analytics")
    async def analytics(self, ctx: commands.Context) -> None:
        await self._send_server_report(ctx, 7)

    @analytics.command(name="server", description="Активность сервера за период")
    @app_commands.describe(days="Период в днях: 1-30")
    @commands.guild_only()
    @requires_hybrid("analytics")
    async def server(self, ctx: commands.Context, days: int = 7) -> None:
        await self._send_server_report(ctx, days)

    async def _send_server_report(self, ctx: commands.Context, days: int = 7) -> None:
        assert ctx.guild is not None
        days = max(1, min(days, 30))
        rows = await self.bot.db.server_stats_recent(ctx.guild.id, days)
        totals = await self.bot.db.server_stats_totals(ctx.guild.id, days)

        embed = discord.Embed(
            title=f"📊 Аналитика сервера за {days} дн.",
            color=config.EMBED_COLOR,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Сообщения", value=str(totals["messages"]), inline=True)
        embed.add_field(name="Команды", value=str(totals["commands_used"]), inline=True)
        embed.add_field(
            name="Участники",
            value=f"+{totals['members_joined']} / -{totals['members_left']}",
            inline=True,
        )
        if rows:
            max_messages = max(int(row["messages"]) for row in rows)
            lines = []
            for row in reversed(rows):
                messages = int(row["messages"])
                lines.append(f"`{row['stat_date']}` {_bar(messages, max_messages)} {messages}")
            embed.add_field(name="Сообщения по дням", value="\n".join(lines), inline=False)
        await ctx.reply(embed=embed, mention_author=False)

    @analytics.command(name="tickets", description="Статистика тикетов")
    @commands.guild_only()
    @requires_hybrid("analytics")
    async def tickets(self, ctx: commands.Context) -> None:
        assert ctx.guild is not None
        counts = await self.bot.db.ticket_status_counts(ctx.guild.id)
        rating = await self.bot.db.ticket_rating_summary(ctx.guild.id)
        staff = await self.bot.db.ticket_staff_stats(ctx.guild.id, limit=5)

        total = sum(counts.values())
        lines = []
        for status in ("pending", "open", "waiting_user", "waiting_staff", "resolved", "closed"):
            if counts.get(status, 0):
                lines.append(f"{status}: **{counts[status]}**")

        avg = rating["avg_rating"]
        rating_text = "нет оценок"
        if avg is not None:
            rating_text = f"{avg:.2f}/5 по {rating['count']} оценкам"

        embed = discord.Embed(
            title="🎫 Аналитика тикетов",
            color=config.EMBED_COLOR,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Всего заявок", value=str(total), inline=True)
        embed.add_field(name="Оценка", value=rating_text, inline=True)
        embed.add_field(name="Статусы", value="\n".join(lines) or "пока пусто", inline=False)
        if staff:
            staff_lines = []
            for row in staff:
                avg_staff = row["avg_rating"]
                avg_text = f"{avg_staff:.2f}/5" if avg_staff is not None else "нет оценок"
                staff_lines.append(f"<@{row['claimed_by']}> — {row['closed_count']} закрыто, {avg_text}")
            embed.add_field(name="Персонал", value="\n".join(staff_lines), inline=False)
        await ctx.reply(embed=embed, mention_author=False)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Analytics(bot))
