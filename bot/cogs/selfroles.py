"""Self-assignable roles via a persistent dropdown panel.

Build a panel with ``/selfroles new`` then add roles with ``/selfroles addrole``
(each with an optional custom label/emoji/description). Members pick roles from
the dropdown; selecting assigns, deselecting removes — bounded by ``max`` roles.

The panel's select is persistent (custom_id ``selfrole:select``); on interaction
the managed role set is looked up by the panel message id, so it survives
restarts regardless of how many panels exist.
"""
from __future__ import annotations

from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import config
from core import embeds
from core.permissions import requires


def build_panel_embed(title: Optional[str], options: list, guild: discord.Guild) -> discord.Embed:
    embed = discord.Embed(
        title=title or "🎭 Выбор ролей",
        description="Выберите роли из меню ниже. Повторный выбор — снимает роль.",
        color=config.EMBED_COLOR,
    )
    if options:
        lines = []
        for o in options:
            role = guild.get_role(o["role_id"])
            label = o["label"] or (role.name if role else str(o["role_id"]))
            lines.append(f"{o['emoji'] or '•'} {label}")
        embed.add_field(name="Доступные роли", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="Доступные роли", value="*пока пусто — добавьте через `/selfroles addrole`*",
                        inline=False)
    return embed


def build_panel_view(options: list, max_roles: int) -> "SelfRoleView":
    view = SelfRoleView()
    if options:
        view.add_item(SelfRoleSelect(options, max_roles))
    return view


class SelfRoleSelect(discord.ui.Select):
    def __init__(self, options: Optional[list] = None, max_roles: int = 0) -> None:
        if options:
            select_options = [
                discord.SelectOption(
                    label=(o["label"] or str(o["role_id"]))[:100],
                    value=str(o["role_id"]),
                    emoji=o["emoji"] or None,
                    description=(o["description"] or "")[:100] or None,
                )
                for o in options
            ]
            max_values = min(max_roles, len(select_options)) if max_roles else len(select_options)
        else:
            select_options = [discord.SelectOption(label="—", value="__noop__")]
            max_values = 1
        super().__init__(
            placeholder="🎭 Выберите роли…", min_values=0, max_values=max(max_values, 1),
            options=select_options, custom_id="selfrole:select",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None or not isinstance(interaction.user, discord.Member):
            return
        db = interaction.client.db  # type: ignore[attr-defined]
        message_id = interaction.message.id
        panel = await db.get_selfrole_panel(message_id)
        options = await db.get_selfrole_options(message_id)
        if panel is None or not options:
            await interaction.response.send_message(
                embed=embeds.error("Эта панель больше не активна."), ephemeral=True
            )
            return

        managed_ids = {o["role_id"] for o in options}
        selected_ids = {int(v) for v in self.values if v != "__noop__"}
        max_roles = panel["max_roles"]
        if max_roles and len(selected_ids) > max_roles:
            await interaction.response.send_message(
                embed=embeds.error(f"Можно выбрать не больше {max_roles} ролей."), ephemeral=True
            )
            return

        member = interaction.user
        current = {r.id for r in member.roles}
        to_add, to_remove, missing = [], [], False
        for rid in managed_ids:
            role = guild.get_role(rid)
            if role is None:
                continue
            if role >= guild.me.top_role:
                missing = True
                continue
            if rid in selected_ids and rid not in current:
                to_add.append(role)
            elif rid not in selected_ids and rid in current:
                to_remove.append(role)

        try:
            if to_add:
                await member.add_roles(*to_add, reason="self-role")
            if to_remove:
                await member.remove_roles(*to_remove, reason="self-role")
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=embeds.error("У бота нет прав выдать/снять некоторые роли (проверьте иерархию)."),
                ephemeral=True,
            )
            return

        parts = []
        if to_add:
            parts.append("➕ " + ", ".join(r.mention for r in to_add))
        if to_remove:
            parts.append("➖ " + ", ".join(r.mention for r in to_remove))
        if missing:
            parts.append("⚠️ некоторые роли выше роли бота — пропущены")
        await interaction.response.send_message(
            embed=embeds.success("\n".join(parts) if parts else "Ничего не изменилось."),
            ephemeral=True,
        )


class SelfRoleView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)


class SelfRoles(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    selfroles = app_commands.Group(
        name="selfroles", description="Само-выдаваемые роли (панель с меню)", guild_only=True
    )

    async def _render(self, guild: discord.Guild, panel) -> None:
        channel = guild.get_channel(panel["channel_id"])
        if not isinstance(channel, discord.TextChannel):
            return
        try:
            message = await channel.fetch_message(panel["message_id"])
        except discord.HTTPException:
            return
        options = await self.bot.db.get_selfrole_options(panel["message_id"])
        await message.edit(
            embed=build_panel_embed(panel["title"], options, guild),
            view=build_panel_view(options, panel["max_roles"]),
        )

    @selfroles.command(name="new", description="Создать новую панель ролей в этом канале")
    @app_commands.describe(title="Заголовок панели", max="Макс. ролей на пользователя (0 = без лимита)")
    @requires("selfroles")
    async def new(
        self,
        interaction: discord.Interaction,
        title: Optional[str] = None,
        max: app_commands.Range[int, 0, 25] = 0,
    ) -> None:
        guild = interaction.guild
        channel = interaction.channel
        if guild is None or not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                embed=embeds.error("Нужен текстовый канал."), ephemeral=True
            )
            return
        embed = build_panel_embed(title, [], guild)
        msg = await channel.send(embed=embed, view=SelfRoleView())
        await self.bot.db.create_selfrole_panel_row(msg.id, guild.id, channel.id, title, max)
        await interaction.response.send_message(
            embed=embeds.success("Панель создана. Добавьте роли через `/selfroles addrole`."),
            ephemeral=True,
        )

    @selfroles.command(name="addrole", description="Добавить роль в последнюю панель этого канала")
    @app_commands.describe(role="Роль", label="Подпись (необязательно)",
                           emoji="Эмодзи", description="Описание")
    @requires("selfroles")
    async def addrole(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
        label: Optional[str] = None,
        emoji: Optional[str] = None,
        description: Optional[str] = None,
    ) -> None:
        guild = interaction.guild
        channel = interaction.channel
        assert guild and isinstance(channel, discord.TextChannel)
        panel = await self.bot.db.latest_selfrole_panel(channel.id)
        if panel is None:
            await interaction.response.send_message(
                embed=embeds.error("В этом канале нет панели. Создайте через `/selfroles new`."),
                ephemeral=True,
            )
            return
        if role.is_default() or role.managed:
            await interaction.response.send_message(
                embed=embeds.error("Эту роль нельзя выдавать самостоятельно."), ephemeral=True
            )
            return
        if role >= guild.me.top_role:
            await interaction.response.send_message(
                embed=embeds.error("Роль выше роли бота — он не сможет её выдавать."), ephemeral=True
            )
            return
        opts = await self.bot.db.get_selfrole_options(panel["message_id"])
        if len(opts) >= 25:
            await interaction.response.send_message(
                embed=embeds.error("В панели уже 25 ролей (лимит)."), ephemeral=True
            )
            return
        await self.bot.db.add_selfrole_option(panel["message_id"], role.id, label, emoji, description)
        await self._render(guild, panel)
        await interaction.response.send_message(
            embed=embeds.success(f"Роль {role.mention} добавлена в панель."), ephemeral=True
        )

    @selfroles.command(name="removerole", description="Убрать роль из последней панели канала")
    @app_commands.describe(role="Роль")
    @requires("selfroles")
    async def removerole(self, interaction: discord.Interaction, role: discord.Role) -> None:
        guild = interaction.guild
        channel = interaction.channel
        assert guild and isinstance(channel, discord.TextChannel)
        panel = await self.bot.db.latest_selfrole_panel(channel.id)
        if panel is None:
            await interaction.response.send_message(
                embed=embeds.error("В этом канале нет панели."), ephemeral=True
            )
            return
        n = await self.bot.db.remove_selfrole_option(panel["message_id"], role.id)
        if n:
            await self._render(guild, panel)
        msg = "Роль убрана." if n else "Этой роли нет в панели."
        await interaction.response.send_message(embed=embeds.success(msg), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SelfRoles(bot))
