"""The /config command — opens the interactive permission-management menu.

Only the server owner and the super admin may open it (this gate is not
configurable, by design).
"""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from core import embeds
from core.permissions import is_admin_access
from views.config_views import HomeView
from views.settings_views import SettingsHome


class Config(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="config", description="Панель настройки доступа к командам бота"
    )
    @app_commands.guild_only()
    async def config(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                embed=embeds.error("Команда доступна только на сервере."), ephemeral=True
            )
            return
        if not is_admin_access(interaction.user, guild):
            await interaction.response.send_message(
                embed=embeds.error(
                    "Открыть это меню может только администратор сервера "
                    "(или супер-администратор бота)."
                ),
                ephemeral=True,
            )
            return

        view = HomeView(self.bot, interaction.user.id)
        embed = await view.build_embed(guild)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        view.message = await interaction.original_response()

    @app_commands.command(
        name="settings", description="Визуальная настройка модулей бота (логи, тикеты, уровни и др.)"
    )
    @app_commands.guild_only()
    async def settings(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                embed=embeds.error("Команда доступна только на сервере."), ephemeral=True
            )
            return
        if not is_admin_access(interaction.user, guild):
            await interaction.response.send_message(
                embed=embeds.error(
                    "Открыть настройки может только администратор сервера "
                    "(или супер-администратор бота)."
                ),
                ephemeral=True,
            )
            return
        view = SettingsHome(self.bot, interaction.user.id, guild.id)
        embed = await view.build_embed()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        view.message = await interaction.original_response()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Config(bot))
