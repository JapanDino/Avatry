"""Compact server dashboard for bot systems."""
from __future__ import annotations

import discord
from discord.ext import commands

import config
from core.registry import CATEGORIES
from core.permissions import requires_hybrid


class Dashboard(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(
        name="dashboard",
        aliases=["dash", "панель"],
        description="Общий dashboard систем бота на сервере",
    )
    @commands.guild_only()
    @requires_hybrid("dashboard")
    async def dashboard(self, ctx: commands.Context) -> None:
        assert ctx.guild is not None
        guild = ctx.guild

        command_lines = []
        enabled_total = 0
        total_commands = 0
        for category in CATEGORIES:
            enabled = 0
            for command in category.commands:
                if await self.bot.db.is_command_enabled(guild.id, command.key, command.default_enabled):
                    enabled += 1
            enabled_total += enabled
            total_commands += len(category.commands)
            command_lines.append(f"{category.emoji} {category.label}: **{enabled}/{len(category.commands)}**")

        ticket_counts = await self.bot.db.ticket_status_counts(guild.id)
        rating = await self.bot.db.ticket_rating_summary(guild.id)
        stats = await self.bot.db.server_stats_totals(guild.id, 7)
        rep_top = await self.bot.db.reputation_top(guild.id, limit=3)
        econ = await self.bot.db.get_econ_config(guild.id)

        open_tickets = sum(ticket_counts.get(key, 0) for key in ("pending", "open", "waiting_user", "waiting_staff", "resolved"))
        avg_rating = rating["avg_rating"]
        rating_text = f"{avg_rating:.2f}/5" if avg_rating is not None else "нет оценок"
        rep_text = "\n".join(
            f"**{idx}.** <@{row['receiver_id']}> — {row['score']}"
            for idx, row in enumerate(rep_top, 1)
        ) or "пока пусто"

        embed = discord.Embed(
            title=f"🧭 Dashboard: {guild.name}",
            color=config.EMBED_COLOR,
            timestamp=discord.utils.utcnow(),
        )
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="Участники", value=str(guild.member_count or len(guild.members)), inline=True)
        embed.add_field(name="Команды", value=f"{enabled_total}/{total_commands} включено", inline=True)
        embed.add_field(
            name="Экономика",
            value="включена" if econ["enabled"] else "выключена",
            inline=True,
        )
        embed.add_field(
            name="Активность 7 дн.",
            value=(
                f"Сообщения: **{stats['messages']}**\n"
                f"Команды: **{stats['commands_used']}**\n"
                f"Участники: +{stats['members_joined']} / -{stats['members_left']}"
            ),
            inline=False,
        )
        embed.add_field(
            name="Тикеты",
            value=f"Активных: **{open_tickets}**\nЗакрытых: **{ticket_counts.get('closed', 0)}**\nОценка: **{rating_text}**",
            inline=True,
        )
        embed.add_field(name="Топ репутации", value=rep_text, inline=True)
        embed.add_field(name="Сферы команд", value="\n".join(command_lines[:12]), inline=False)
        await ctx.reply(embed=embed, mention_author=False)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Dashboard(bot))
