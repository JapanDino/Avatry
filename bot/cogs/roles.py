"""Role management (hybrid: /role add and k.role add)."""
from __future__ import annotations

from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import config
from core import embeds, modlog
from core.permissions import requires_hybrid


class Roles(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_group(name="role", description="Управление ролями участников", guild_only=True)
    async def role(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.reply(embed=embeds.warn("Используй `role add` или `role remove`."),
                            mention_author=False)

    def _assign_block(self, guild: discord.Guild, actor: discord.Member,
                      target_role: discord.Role) -> Optional[str]:
        if target_role.is_default():
            return "Нельзя выдавать роль @everyone."
        if target_role.managed:
            return "Эта роль управляется интеграцией и не выдаётся вручную."
        me = guild.me
        if me is None or target_role >= me.top_role:
            return "Эта роль выше роли бота — не могу ею управлять."
        if actor.id in (guild.owner_id, config.SUPER_ADMIN_ID):
            return None
        if target_role >= actor.top_role:
            return "Эта роль выше или равна вашей высшей роли."
        return None

    @role.command(name="add", description="Выдать роль пользователю")
    @app_commands.describe(user="Кому выдать роль", target_role="Какую роль выдать")
    @requires_hybrid("role_add")
    async def add(self, ctx: commands.Context, user: discord.Member,
                  target_role: discord.Role) -> None:
        guild = ctx.guild
        assert guild and isinstance(ctx.author, discord.Member)
        block = self._assign_block(guild, ctx.author, target_role)
        if block:
            await ctx.reply(embed=embeds.error(block), mention_author=False)
            return
        if target_role in user.roles:
            await ctx.reply(embed=embeds.warn(f"У {user.mention} уже есть роль {target_role.mention}."),
                            mention_author=False)
            return
        try:
            await user.add_roles(target_role, reason=f"role add by {ctx.author}")
        except discord.Forbidden:
            await ctx.reply(embed=embeds.error("Недостаточно прав, чтобы выдать эту роль."),
                            mention_author=False)
            return
        await ctx.reply(embed=embeds.success(f"{user.mention} получил роль {target_role.mention}."),
                        mention_author=False)
        await modlog.send_modlog(self.bot, guild.id,
                                 modlog.action_embed("🎭 Выдача роли", user, ctx.author, target_role.name))

    @role.command(name="remove", description="Снять роль с пользователя")
    @app_commands.describe(user="С кого снять роль", target_role="Какую роль снять")
    @requires_hybrid("role_remove")
    async def remove(self, ctx: commands.Context, user: discord.Member,
                     target_role: discord.Role) -> None:
        guild = ctx.guild
        assert guild and isinstance(ctx.author, discord.Member)
        block = self._assign_block(guild, ctx.author, target_role)
        if block:
            await ctx.reply(embed=embeds.error(block), mention_author=False)
            return
        if target_role not in user.roles:
            await ctx.reply(embed=embeds.warn(f"У {user.mention} нет роли {target_role.mention}."),
                            mention_author=False)
            return
        try:
            await user.remove_roles(target_role, reason=f"role remove by {ctx.author}")
        except discord.Forbidden:
            await ctx.reply(embed=embeds.error("Недостаточно прав, чтобы снять эту роль."),
                            mention_author=False)
            return
        await ctx.reply(embed=embeds.success(f"С {user.mention} снята роль {target_role.mention}."),
                        mention_author=False)
        await modlog.send_modlog(self.bot, guild.id,
                                 modlog.action_embed("🎭 Снятие роли", user, ctx.author, target_role.name))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Roles(bot))
