"""Starboard — reposts highly-reacted messages to a dedicated channel."""
from __future__ import annotations

from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import config
from core import embeds
from core.permissions import requires


class Starboard(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        await self._handle(payload)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        await self._handle(payload)

    async def _handle(self, payload: discord.RawReactionActionEvent) -> None:
        if payload.guild_id is None:
            return
        cfg = await self.bot.db.get_guild_config(payload.guild_id)
        board_id = cfg["starboard_channel_id"]
        if not board_id or str(payload.emoji) != cfg["starboard_emoji"]:
            return
        if payload.channel_id == board_id:
            return  # don't star the starboard itself

        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        source = guild.get_channel(payload.channel_id)
        board = guild.get_channel(board_id)
        if not isinstance(source, discord.TextChannel) or not isinstance(board, discord.TextChannel):
            return
        try:
            message = await source.fetch_message(payload.message_id)
        except discord.HTTPException:
            return

        count = 0
        for reaction in message.reactions:
            if str(reaction.emoji) == cfg["starboard_emoji"]:
                count = reaction.count
                break

        threshold = cfg["starboard_threshold"]
        existing = await self.bot.db.get_starboard_post(payload.guild_id, message.id)

        if count < threshold:
            # Below threshold: remove board post if it exists.
            if existing and existing["board_message_id"]:
                try:
                    old = await board.fetch_message(existing["board_message_id"])
                    await old.delete()
                except discord.HTTPException:
                    pass
                await self.bot.db.upsert_starboard_post(payload.guild_id, message.id, None)
            return

        embed = self._build_embed(message, cfg["starboard_emoji"], count)
        if existing and existing["board_message_id"]:
            try:
                board_msg = await board.fetch_message(existing["board_message_id"])
                await board_msg.edit(content=self._header(cfg["starboard_emoji"], count), embed=embed)
                return
            except discord.NotFound:
                pass  # recreate below
        board_msg = await board.send(content=self._header(cfg["starboard_emoji"], count), embed=embed)
        await self.bot.db.upsert_starboard_post(payload.guild_id, message.id, board_msg.id)

    @staticmethod
    def _header(emoji: str, count: int) -> str:
        return f"{emoji} **{count}**"

    @staticmethod
    def _build_embed(message: discord.Message, emoji: str, count: int) -> discord.Embed:
        embed = discord.Embed(
            description=message.content or "*(без текста)*",
            color=config.WARN_COLOR,
            timestamp=message.created_at,
        )
        embed.set_author(name=message.author.display_name, icon_url=message.author.display_avatar.url)
        embed.add_field(name="Источник", value=f"[Перейти к сообщению]({message.jump_url})", inline=False)
        for att in message.attachments:
            if att.content_type and att.content_type.startswith("image/"):
                embed.set_image(url=att.url)
                break
        embed.set_footer(text=f"{emoji} {count} · #{getattr(message.channel, 'name', '?')}")
        return embed

    # ---- config ---------------------------------------------------------

    starboard = app_commands.Group(name="starboard", description="Звёздная доска", guild_only=True)

    @starboard.command(name="channel", description="Канал звёздной доски")
    @app_commands.describe(channel="Канал (не указывать — выключить)")
    @requires("starboard")
    async def sb_channel(
        self, interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None
    ) -> None:
        await self.bot.db.update_guild_config(
            interaction.guild_id, starboard_channel_id=channel.id if channel else None
        )
        msg = f"Звёздная доска: {channel.mention}" if channel else "Звёздная доска выключена."
        await interaction.response.send_message(embed=embeds.success(msg), ephemeral=True)

    @starboard.command(name="emoji", description="Эмодзи-реакция для доски")
    @app_commands.describe(emoji="Эмодзи (по умолчанию ⭐)")
    @requires("starboard")
    async def sb_emoji(self, interaction: discord.Interaction, emoji: str) -> None:
        await self.bot.db.update_guild_config(interaction.guild_id, starboard_emoji=emoji.strip())
        await interaction.response.send_message(
            embed=embeds.success(f"Эмодзи доски: {emoji}"), ephemeral=True
        )

    @starboard.command(name="threshold", description="Сколько реакций нужно для попадания на доску")
    @app_commands.describe(count="Порог реакций")
    @requires("starboard")
    async def sb_threshold(
        self, interaction: discord.Interaction, count: app_commands.Range[int, 1, 100]
    ) -> None:
        await self.bot.db.update_guild_config(interaction.guild_id, starboard_threshold=count)
        await interaction.response.send_message(
            embed=embeds.success(f"Порог доски: **{count}** реакций."), ephemeral=True
        )

    @starboard.command(name="status", description="Текущие настройки доски")
    @requires("starboard")
    async def sb_status(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        assert guild
        cfg = await self.bot.db.get_guild_config(guild.id)
        ch = guild.get_channel(cfg["starboard_channel_id"]) if cfg["starboard_channel_id"] else None
        embed = embeds.base(title="⭐ Звёздная доска")
        embed.add_field(name="Канал", value=ch.mention if ch else "выключена", inline=True)
        embed.add_field(name="Эмодзи", value=cfg["starboard_emoji"], inline=True)
        embed.add_field(name="Порог", value=str(cfg["starboard_threshold"]), inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Starboard(bot))
