"""Reputation commands: +rep / /rep, profile and leaderboard."""
from __future__ import annotations

import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import config
from core import embeds
from core.permissions import requires_hybrid

REP_COOLDOWN = 12 * 60 * 60


def _format_remaining(seconds: int) -> str:
    hours, rem = divmod(max(0, seconds), 3600)
    minutes, _ = divmod(rem, 60)
    if hours:
        return f"{hours} ч {minutes} мин"
    return f"{minutes} мин"


class Reputation(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(
        name="rep",
        aliases=["реп"],
        description="Поставить репутацию участнику",
    )
    @app_commands.describe(user="Кому поставить репутацию", reason="Причина или короткий комментарий")
    @commands.guild_only()
    @requires_hybrid("reputation")
    async def rep(
        self,
        ctx: commands.Context,
        user: discord.Member,
        *,
        reason: Optional[str] = None,
    ) -> None:
        assert ctx.guild is not None
        author = ctx.author
        if not isinstance(author, discord.Member):
            return
        if user.bot:
            await ctx.reply(embed=embeds.error("Ботам репутацию ставить нельзя."), mention_author=False)
            return
        if user.id == author.id:
            await ctx.reply(embed=embeds.error("Себе репутацию поставить нельзя."), mention_author=False)
            return

        latest = await self.bot.db.latest_reputation_from(ctx.guild.id, author.id)
        now = int(time.time())
        if latest is not None and now - int(latest["created_at"]) < REP_COOLDOWN:
            left = REP_COOLDOWN - (now - int(latest["created_at"]))
            await ctx.reply(
                embed=embeds.warn(f"Репутацию можно ставить раз в 12 часов. Осталось: **{_format_remaining(left)}**."),
                mention_author=False,
            )
            return

        await self.bot.db.add_reputation(ctx.guild.id, author.id, user.id, reason)
        score = await self.bot.db.reputation_score(ctx.guild.id, user.id)
        text = f"{author.mention} повысил репутацию {user.mention}. Теперь у участника **{score}** rep."
        if reason:
            text += f"\nПричина: {reason[:300]}"
        await ctx.reply(embed=embeds.success(text), mention_author=False)

    @commands.hybrid_command(
        name="repprofile",
        aliases=["repinfo", "репутация"],
        description="Показать репутацию участника",
    )
    @app_commands.describe(user="Чью репутацию показать")
    @commands.guild_only()
    @requires_hybrid("reputation_profile")
    async def repprofile(
        self, ctx: commands.Context, user: Optional[discord.Member] = None
    ) -> None:
        assert ctx.guild is not None
        target = user or ctx.author
        score = await self.bot.db.reputation_score(ctx.guild.id, target.id)
        given = await self.bot.db.reputation_given_count(ctx.guild.id, target.id)
        recent = await self.bot.db.reputation_recent(ctx.guild.id, target.id, limit=5)

        embed = discord.Embed(title=f"⭐ Репутация: {target.display_name}", color=config.EMBED_COLOR)
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="Получено", value=str(score), inline=True)
        embed.add_field(name="Выдано", value=str(given), inline=True)
        if recent:
            lines = []
            for row in recent:
                reason = f" — {row['reason'][:80]}" if row["reason"] else ""
                lines.append(f"<@{row['giver_id']}> <t:{row['created_at']}:R>{reason}")
            embed.add_field(name="Последние отзывы", value="\n".join(lines), inline=False)
        await ctx.reply(embed=embed, mention_author=False)

    @commands.hybrid_command(
        name="reptop",
        aliases=["топреп"],
        description="Топ участников по репутации",
    )
    @commands.guild_only()
    @requires_hybrid("reputation_top")
    async def reptop(self, ctx: commands.Context) -> None:
        assert ctx.guild is not None
        rows = await self.bot.db.reputation_top(ctx.guild.id, limit=10)
        if not rows:
            await ctx.reply(embed=embeds.warn("Репутации пока нет."), mention_author=False)
            return
        lines = [
            f"**{idx}.** <@{row['receiver_id']}> — **{row['score']}**"
            for idx, row in enumerate(rows, 1)
        ]
        await ctx.reply(
            embed=embeds.base(title="⭐ Топ репутации", description="\n".join(lines)),
            mention_author=False,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Reputation(bot))
