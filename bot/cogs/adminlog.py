"""Administration logging form.

A panel with a "Logs" button opens a fixed form (snapshot from the spec); the
filled form is posted to a configured logging channel. Used to log staff
removals/changes. Questions are fixed per the design.
"""
from __future__ import annotations

from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import config
from core import embeds
from core.permissions import requires

# (label, placeholder, paragraph, required) — strictly per the form design.
LOG_QUESTIONS = [
    ("Никнейм | Тег", "Никнейм | Тег", False, True),
    ("Должность которую покинул", "Helper/Moderator", False, True),
    ("Причина снятия", "Ваш ответ", False, True),
    ("Нахождение в ЧС | доказательства", "да/нет + ссылки/описание доказательств", True, True),
    ("Описание", "Повышения/актив/выговора/ветки.", True, True),
]


class AdminLogModal(discord.ui.Modal, title="Логирование Администрации"):
    def __init__(self) -> None:
        super().__init__()
        self._labels: list[str] = []
        for label, placeholder, paragraph, required in LOG_QUESTIONS:
            self._labels.append(label)
            self.add_item(discord.ui.TextInput(
                label=label[:45], placeholder=placeholder[:100], required=required,
                style=discord.TextStyle.paragraph if paragraph else discord.TextStyle.short,
                max_length=1000))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        bot = interaction.client
        guild = interaction.guild
        if guild is None:
            return
        cfg = await bot.db.get_guild_config(guild.id)  # type: ignore[attr-defined]
        channel = guild.get_channel(cfg["adminlog_channel_id"]) if cfg["adminlog_channel_id"] else None
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                embed=embeds.error("Канал логирования не настроен. Админу: `/adminlog channel`."),
                ephemeral=True)
            return

        embed = discord.Embed(title="S A M U R A I", color=config.EMBED_COLOR,
                              timestamp=discord.utils.utcnow())
        embed.description = f"Залогировал: {interaction.user.mention}"
        for label, item in zip(self._labels, self.children):
            value = str(item.value).strip() or "—"
            embed.add_field(name=label, value=value[:1024], inline=False)
        embed.set_footer(text=f"ID: {interaction.user.id}")
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            await interaction.response.send_message(
                embed=embeds.error("Не удалось отправить лог (права бота в канале)."), ephemeral=True)
            return
        await interaction.response.send_message(
            embed=embeds.success(f"Запись добавлена в {channel.mention}."), ephemeral=True)


class AdminLogPanelView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Logs", style=discord.ButtonStyle.secondary, emoji="📝",
                       custom_id="alog:open")
    async def open(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(AdminLogModal())


class AdminLog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    adminlog = app_commands.Group(name="adminlog", description="Логирование администрации",
                                  guild_only=True)

    @adminlog.command(name="channel", description="Канал, куда отправляются записи логирования")
    @app_commands.describe(channel="Канал для логов администрации")
    @requires("adminlog")
    async def channel_cmd(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        await self.bot.db.update_guild_config(interaction.guild_id, adminlog_channel_id=channel.id)
        await interaction.response.send_message(
            embed=embeds.success(f"Канал логирования: {channel.mention}"), ephemeral=True)

    @adminlog.command(name="panel", description="Опубликовать панель логирования администрации")
    @app_commands.describe(channel="Канал для панели (по умолчанию — текущий)")
    @requires("adminlog")
    async def panel(self, interaction: discord.Interaction,
                    channel: Optional[discord.TextChannel] = None) -> None:
        guild = interaction.guild
        assert guild
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message(embed=embeds.error("Нужен текстовый канал."),
                                                    ephemeral=True)
            return
        embed = discord.Embed(title="S A M U R A I", color=config.EMBED_COLOR)
        embed.add_field(name="Логирование Администрации", value="​", inline=False)
        embed.add_field(
            name="​",
            value=(f"🛠️ Заполняем строго по форме\n"
                   f"</> При каких-либо ошибках обращаться к <@{config.DEVELOPER_ID}>\n"
                   "━━━━━━━━━━━━━━━━━━━━━━━━━━━"),
            inline=False)
        try:
            await target.send(embed=embed, view=AdminLogPanelView())
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=embeds.error(f"Нет прав писать в {target.mention}."), ephemeral=True)
            return
        await interaction.response.send_message(
            embed=embeds.success(f"Панель логирования опубликована в {target.mention}."), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminLog(bot))
