"""Welcome / goodbye messages and auto-role on join.

Message templates support placeholders:
  {user} — mention · {name} — display name · {server} — guild name ·
  {count} — member count.
"""
from __future__ import annotations

import io
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from core import banners, embeds
from core.permissions import requires

DEFAULT_WELCOME = "Добро пожаловать, {user}, на **{server}**! Ты {count}-й участник 🎉"
DEFAULT_GOODBYE = "{name} покинул(а) сервер. Участников осталось: {count}."


def render(template: str, member: discord.Member) -> str:
    return (
        template.replace("{user}", member.mention)
        .replace("{name}", member.display_name)
        .replace("{server}", member.guild.name)
        .replace("{count}", str(member.guild.member_count))
    )


class Welcome(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        cfg = await self.bot.db.get_guild_config(member.guild.id)
        # Auto-role.
        if cfg["autorole_id"]:
            role = member.guild.get_role(cfg["autorole_id"])
            if role is not None and role < member.guild.me.top_role:
                try:
                    await member.add_roles(role, reason="autorole on join")
                except discord.HTTPException:
                    pass
        # Welcome message — a generated banner image (with embed fallback).
        if cfg["welcome_channel_id"]:
            channel = member.guild.get_channel(cfg["welcome_channel_id"])
            if isinstance(channel, discord.TextChannel):
                text = cfg["welcome_message"] or DEFAULT_WELCOME
                accent = member.color.to_rgb() if member.color.value else (88, 101, 242)
                file = None
                try:
                    av = await member.display_avatar.replace(size=128).read()
                    data = await banners.welcome(av, member.display_name,
                                                 member.guild.member_count, member.guild.name, accent)
                    file = discord.File(io.BytesIO(data), filename="welcome.png")
                except Exception:
                    file = None
                try:
                    if file is not None:
                        await channel.send(content=f"{member.mention}", file=file)
                    else:
                        embed = embeds.base(description=render(text, member))
                        embed.set_thumbnail(url=member.display_avatar.url)
                        await channel.send(content=member.mention, embed=embed)
                except discord.HTTPException:
                    pass

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        cfg = await self.bot.db.get_guild_config(member.guild.id)
        if cfg["goodbye_channel_id"]:
            channel = member.guild.get_channel(cfg["goodbye_channel_id"])
            if isinstance(channel, discord.TextChannel):
                text = cfg["goodbye_message"] or DEFAULT_GOODBYE
                embed = embeds.base(description=render(text, member))
                try:
                    await channel.send(embed=embed)
                except discord.HTTPException:
                    pass

    # ---- config ---------------------------------------------------------

    welcome = app_commands.Group(name="welcome", description="Приветствие новичков", guild_only=True)
    goodbye = app_commands.Group(name="goodbye", description="Прощание с ушедшими", guild_only=True)

    @welcome.command(name="set", description="Канал и (опц.) текст приветствия")
    @app_commands.describe(channel="Канал приветствий", message="Текст ({user},{name},{server},{count})")
    @requires("welcome")
    async def welcome_set(
        self, interaction: discord.Interaction, channel: discord.TextChannel,
        message: Optional[str] = None,
    ) -> None:
        fields = {"welcome_channel_id": channel.id}
        if message:
            fields["welcome_message"] = message
        await self.bot.db.update_guild_config(interaction.guild_id, **fields)
        await interaction.response.send_message(
            embed=embeds.success(f"Приветствия включены в {channel.mention}."), ephemeral=True
        )

    @welcome.command(name="test", description="Показать пример приветствия")
    @requires("welcome")
    async def welcome_test(self, interaction: discord.Interaction) -> None:
        cfg = await self.bot.db.get_guild_config(interaction.guild_id)
        text = cfg["welcome_message"] or DEFAULT_WELCOME
        await interaction.response.send_message(
            embed=embeds.base(description=render(text, interaction.user)), ephemeral=True
        )

    @welcome.command(name="off", description="Выключить приветствия")
    @requires("welcome")
    async def welcome_off(self, interaction: discord.Interaction) -> None:
        await self.bot.db.update_guild_config(interaction.guild_id, welcome_channel_id=None)
        await interaction.response.send_message(
            embed=embeds.success("Приветствия выключены."), ephemeral=True
        )

    @goodbye.command(name="set", description="Канал и (опц.) текст прощания")
    @app_commands.describe(channel="Канал прощаний", message="Текст ({name},{server},{count})")
    @requires("goodbye")
    async def goodbye_set(
        self, interaction: discord.Interaction, channel: discord.TextChannel,
        message: Optional[str] = None,
    ) -> None:
        fields = {"goodbye_channel_id": channel.id}
        if message:
            fields["goodbye_message"] = message
        await self.bot.db.update_guild_config(interaction.guild_id, **fields)
        await interaction.response.send_message(
            embed=embeds.success(f"Прощания включены в {channel.mention}."), ephemeral=True
        )

    @goodbye.command(name="off", description="Выключить прощания")
    @requires("goodbye")
    async def goodbye_off(self, interaction: discord.Interaction) -> None:
        await self.bot.db.update_guild_config(interaction.guild_id, goodbye_channel_id=None)
        await interaction.response.send_message(
            embed=embeds.success("Прощания выключены."), ephemeral=True
        )

    @app_commands.command(name="autorole", description="Авто-роль для новых участников")
    @app_commands.describe(role="Роль для новичков (не указывать — выключить)")
    @app_commands.guild_only()
    @requires("autorole")
    async def autorole(
        self, interaction: discord.Interaction, role: Optional[discord.Role] = None
    ) -> None:
        guild = interaction.guild
        assert guild
        if role is not None and role >= guild.me.top_role:
            await interaction.response.send_message(
                embed=embeds.error("Роль выше роли бота — он не сможет её выдавать."), ephemeral=True
            )
            return
        await self.bot.db.update_guild_config(guild.id, autorole_id=role.id if role else None)
        msg = f"Авто-роль: {role.mention}" if role else "Авто-роль выключена."
        await interaction.response.send_message(embed=embeds.success(msg), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Welcome(bot))
