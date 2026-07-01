"""The /help command — interactive command reference. Open to everyone."""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from core import embeds
from views.help_views import HelpView


class Help(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="help", description="Список команд бота и справка по ним")
    @app_commands.guild_only()
    async def help(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                embed=embeds.error("Команда доступна только на сервере."), ephemeral=True
            )
            return
        view = HelpView(self.bot, interaction.user)
        embed = await view.home_embed()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        view.message = await interaction.original_response()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Help(bot))
