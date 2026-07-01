"""User & server information (hybrid: works as /avatar and k.avatar)."""
from __future__ import annotations

from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import config
from core import embeds
from core.permissions import requires_hybrid


class UserInfo(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(name="avatar", aliases=["ava", "аватар"],
                             description="Показать аватар пользователя")
    @app_commands.describe(user="Чей аватар показать (по умолчанию — ваш)")
    @commands.guild_only()
    @requires_hybrid("avatar")
    async def avatar(self, ctx: commands.Context, user: Optional[discord.Member] = None) -> None:
        member = user or ctx.author
        color = member.color if member.color.value else config.EMBED_COLOR
        embed = discord.Embed(title=f"Аватар — {member.display_name}", color=color)
        global_avatar = member.avatar or member.default_avatar
        embed.set_image(url=global_avatar.url)
        links = [f"[Глобальный]({global_avatar.url})"]
        if member.guild_avatar is not None:
            links.append(f"[Серверный]({member.guild_avatar.url})")
            embed.set_thumbnail(url=member.guild_avatar.url)
        embed.description = " • ".join(links)
        await ctx.reply(embed=embed, mention_author=False)

    @commands.hybrid_command(name="banner", aliases=["баннер"],
                             description="Показать баннер профиля пользователя")
    @app_commands.describe(user="Чей баннер показать (по умолчанию — ваш)")
    @commands.guild_only()
    @requires_hybrid("banner")
    async def banner(self, ctx: commands.Context, user: Optional[discord.Member] = None) -> None:
        member = user or ctx.author
        fetched = await self.bot.fetch_user(member.id)
        if fetched.banner is None:
            await ctx.reply(embed=embeds.warn(f"У {member.mention} нет баннера профиля."),
                            mention_author=False)
            return
        embed = embeds.base(title=f"Баннер — {member.display_name}")
        embed.set_image(url=fetched.banner.url)
        embed.description = f"[Открыть оригинал]({fetched.banner.url})"
        await ctx.reply(embed=embed, mention_author=False)

    @commands.hybrid_command(name="userinfo", aliases=["ui", "кто", "юзер"],
                             description="Подробная информация о пользователе")
    @app_commands.describe(user="О ком показать информацию (по умолчанию — о вас)")
    @commands.guild_only()
    @requires_hybrid("userinfo")
    async def userinfo(self, ctx: commands.Context, user: Optional[discord.Member] = None) -> None:
        member = user or ctx.author
        assert isinstance(member, discord.Member)
        color = member.color if member.color.value else config.EMBED_COLOR
        embed = discord.Embed(color=color)
        embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Пользователь", value=f"{member} ({member.mention})", inline=False)
        embed.add_field(name="ID", value=f"`{member.id}`", inline=True)
        embed.add_field(name="Бот", value="Да" if member.bot else "Нет", inline=True)
        embed.add_field(name="Создан аккаунт",
                        value=discord.utils.format_dt(member.created_at, style="R"), inline=True)
        if member.joined_at:
            embed.add_field(name="Зашёл на сервер",
                            value=discord.utils.format_dt(member.joined_at, style="R"), inline=True)
        if member.premium_since:
            embed.add_field(name="Бустит с",
                            value=discord.utils.format_dt(member.premium_since, style="R"), inline=True)
        roles = [r.mention for r in reversed(member.roles) if r.name != "@everyone"]
        if roles:
            shown = roles[:20]
            extra = f" … и ещё {len(roles) - len(shown)}" if len(roles) > len(shown) else ""
            embed.add_field(name=f"Роли ({len(roles)})", value=" ".join(shown) + extra, inline=False)
        key_perms = self._format_key_permissions(member)
        if key_perms:
            embed.add_field(name="Ключевые права", value=key_perms, inline=False)
        if member.id == ctx.guild.owner_id:
            embed.set_footer(text="👑 Владелец сервера")
        elif member.id == config.SUPER_ADMIN_ID:
            embed.set_footer(text="⭐ Супер-администратор бота")
        await ctx.reply(embed=embed, mention_author=False)

    @staticmethod
    def _format_key_permissions(member: discord.Member) -> str:
        perms = member.guild_permissions
        if perms.administrator:
            return "Администратор"
        names = {
            "manage_guild": "Управление сервером", "manage_roles": "Управление ролями",
            "manage_channels": "Управление каналами", "ban_members": "Баны",
            "kick_members": "Кики", "moderate_members": "Тайм-ауты",
            "manage_messages": "Управление сообщениями",
        }
        active = [label for attr, label in names.items() if getattr(perms, attr)]
        return ", ".join(active) if active else ""

    @commands.hybrid_command(name="serverinfo", aliases=["si", "сервер"],
                             description="Информация о сервере")
    @commands.guild_only()
    @requires_hybrid("serverinfo")
    async def serverinfo(self, ctx: commands.Context) -> None:
        guild = ctx.guild
        assert guild is not None
        embed = embeds.base(title=guild.name)
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        if guild.banner:
            embed.set_image(url=guild.banner.url)
        owner = guild.owner or await guild.fetch_member(guild.owner_id)
        embed.add_field(name="Владелец", value=owner.mention if owner else "—", inline=True)
        embed.add_field(name="ID", value=f"`{guild.id}`", inline=True)
        embed.add_field(name="Создан",
                        value=discord.utils.format_dt(guild.created_at, style="R"), inline=True)
        embed.add_field(name="Участников", value=str(guild.member_count), inline=True)
        embed.add_field(name="Ролей", value=str(len(guild.roles)), inline=True)
        embed.add_field(name="Каналов",
                        value=f"{len(guild.text_channels)} текст. / {len(guild.voice_channels)} голос.",
                        inline=True)
        embed.add_field(name="Уровень буста",
                        value=f"{guild.premium_tier} ({guild.premium_subscription_count} бустов)",
                        inline=True)
        await ctx.reply(embed=embed, mention_author=False)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(UserInfo(bot))
