"""Interactive ``/config`` menu — owner / super-admin permission management.

Navigation:  Home (categories)  →  Category (commands)  →  Command (access).
Each screen is an ephemeral message edited in place. Only the invoking user can
interact; views time out after a few minutes.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import discord

import config
from core import embeds
from core.registry import (
    CATEGORIES,
    CATEGORY_BY_KEY,
    COMMANDS_BY_KEY,
    Category,
)

if TYPE_CHECKING:
    from bot import SamuraiBot

TIMEOUT = 180.0


class BaseConfigView(discord.ui.View):
    """Common author-lock + timeout behaviour for every config screen."""

    def __init__(self, bot: "SamuraiBot", author_id: int):
        super().__init__(timeout=TIMEOUT)
        self.bot = bot
        self.author_id = author_id
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                embed=embeds.error("Это меню открыто другим пользователем."),
                ephemeral=True,
            )
            return False
        return True

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True  # type: ignore[attr-defined]
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


# --------------------------------------------------------------------------
# Home screen — pick a category
# --------------------------------------------------------------------------


class HomeView(BaseConfigView):
    def __init__(self, bot: "SamuraiBot", author_id: int):
        super().__init__(bot, author_id)
        self.add_item(CategorySelect())
        self.add_item(LogChannelButton())
        self.add_item(CloseButton())

    async def build_embed(self, guild: discord.Guild) -> discord.Embed:
        cfg = await self.bot.db.get_guild_config(guild.id)
        log_channel = guild.get_channel(cfg["log_channel_id"]) if cfg["log_channel_id"] else None
        embed = embeds.base(
            title="⚙️ Панель настроек K2-SO",
            description=(
                "Здесь настраивается **доступ к каждой команде**.\n"
                "Выберите категорию из списка ниже.\n\n"
                "👑 Владелец, 🛡️ администраторы Discord и ⭐ супер-админ "
                "всегда имеют полный доступ."
            ),
        )
        lines = []
        for cat in CATEGORIES:
            enabled = 0
            for cmd in cat.commands:
                if await self.bot.db.is_command_enabled(guild.id, cmd.key, cmd.default_enabled):
                    enabled += 1
            lines.append(f"{cat.emoji} **{cat.label}** — {enabled}/{len(cat.commands)} вкл.")
        embed.add_field(name="Категории", value="\n".join(lines), inline=False)
        embed.add_field(
            name="Журнал модерации",
            value=log_channel.mention if log_channel else "не задан",
            inline=False,
        )
        return embed


class CategorySelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label=cat.label, value=cat.key, emoji=cat.emoji,
                description=f"{len(cat.commands)} команд",
            )
            for cat in CATEGORIES
        ]
        super().__init__(placeholder="📂 Выберите категорию…", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        view: HomeView = self.view  # type: ignore[assignment]
        category = CATEGORY_BY_KEY[self.values[0]]
        new_view = CategoryView(view.bot, view.author_id, category.key)
        new_view.message = view.message
        embed = await new_view.build_embed(interaction.guild)  # type: ignore[arg-type]
        await interaction.response.edit_message(embed=embed, view=new_view)


class LogChannelButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Журнал модерации", emoji="📋", style=discord.ButtonStyle.secondary
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view: HomeView = self.view  # type: ignore[assignment]
        new_view = LogChannelView(view.bot, view.author_id)
        new_view.message = view.message
        embed = embeds.base(
            title="📋 Журнал модерации",
            description=(
                "Выберите канал, куда бот будет писать действия модерации "
                "(баны, муты, тикеты, анти-фишинг). Или сбросьте его."
            ),
        )
        await interaction.response.edit_message(embed=embed, view=new_view)


class CloseButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Закрыть", emoji="✖️", style=discord.ButtonStyle.danger, row=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(
            embed=embeds.base(description="Меню закрыто."), view=None
        )
        view: BaseConfigView = self.view  # type: ignore[assignment]
        view.stop()


# --------------------------------------------------------------------------
# Log-channel screen
# --------------------------------------------------------------------------


class LogChannelView(BaseConfigView):
    def __init__(self, bot: "SamuraiBot", author_id: int):
        super().__init__(bot, author_id)
        self.add_item(LogChannelSelect())
        self.add_item(ResetLogChannelButton())
        self.add_item(BackHomeButton(row=1))


class LogChannelSelect(discord.ui.ChannelSelect):
    def __init__(self):
        super().__init__(
            placeholder="Выберите канал для логов…",
            channel_types=[discord.ChannelType.text],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view: LogChannelView = self.view  # type: ignore[assignment]
        channel = self.values[0]
        await view.bot.db.update_guild_config(interaction.guild_id, log_channel_id=channel.id)
        await _go_home(interaction, view)


class ResetLogChannelButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Сбросить", emoji="🧹", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction) -> None:
        view: LogChannelView = self.view  # type: ignore[assignment]
        await view.bot.db.update_guild_config(interaction.guild_id, log_channel_id=None)
        await _go_home(interaction, view)


# --------------------------------------------------------------------------
# Category screen — pick a command
# --------------------------------------------------------------------------


class CategoryView(BaseConfigView):
    def __init__(self, bot: "SamuraiBot", author_id: int, category_key: str):
        super().__init__(bot, author_id)
        self.category: Category = CATEGORY_BY_KEY[category_key]
        self.add_item(CommandSelect(self.category))
        self.add_item(CategoryEnableButton(True, row=1))
        self.add_item(CategoryEnableButton(False, row=1))
        self.add_item(BackHomeButton(row=2))

    async def build_embed(self, guild: discord.Guild) -> discord.Embed:
        embed = embeds.base(
            title=f"{self.category.emoji} {self.category.label}",
            description="Выберите команду, чтобы настроить доступ к ней.",
        )
        for cmd in self.category.commands:
            enabled = await self.bot.db.is_command_enabled(
                guild.id, cmd.key, cmd.default_enabled
            )
            roles, users = await self.bot.db.get_permission_targets(guild.id, cmd.key)
            state = "🟢" if enabled else "🔴"
            grants = f"{len(roles)} ролей, {len(users)} польз." if (roles or users) else "только владелец"
            embed.add_field(
                name=f"{state} {cmd.label}",
                value=f"{cmd.description}\n*Доступ: {grants}*",
                inline=False,
            )
        return embed

    async def refresh(self, interaction: discord.Interaction) -> None:
        embed = await self.build_embed(interaction.guild)  # type: ignore[arg-type]
        await interaction.response.edit_message(embed=embed, view=self)


class CategoryEnableButton(discord.ui.Button):
    def __init__(self, enabled: bool, row: int = 0):
        super().__init__(
            label="Включить сферу" if enabled else "Выключить сферу",
            emoji="🟢" if enabled else "🔴",
            style=discord.ButtonStyle.success if enabled else discord.ButtonStyle.danger,
            row=row,
        )
        self.enabled = enabled

    async def callback(self, interaction: discord.Interaction) -> None:
        view: CategoryView = self.view  # type: ignore[assignment]
        for cmd in view.category.commands:
            await view.bot.db.set_command_enabled(interaction.guild_id, cmd.key, self.enabled)
        await view.refresh(interaction)


class CommandSelect(discord.ui.Select):
    def __init__(self, category: Category):
        options = [
            discord.SelectOption(
                label=cmd.label, value=cmd.key, description=cmd.description[:100]
            )
            for cmd in category.commands
        ]
        super().__init__(placeholder="🔧 Выберите команду…", options=options)
        self.category = category

    async def callback(self, interaction: discord.Interaction) -> None:
        view: CategoryView = self.view  # type: ignore[assignment]
        new_view = CommandConfigView(view.bot, view.author_id, self.values[0], self.category.key)
        new_view.message = view.message
        embed = await new_view.build_embed(interaction.guild)  # type: ignore[arg-type]
        await interaction.response.edit_message(embed=embed, view=new_view)


# --------------------------------------------------------------------------
# Command screen — toggle + grant access
# --------------------------------------------------------------------------


class CommandConfigView(BaseConfigView):
    def __init__(self, bot: "SamuraiBot", author_id: int, command_key: str, category_key: str):
        super().__init__(bot, author_id)
        self.command_key = command_key
        self.category_key = category_key
        self.add_item(GrantRoleSelect())
        self.add_item(GrantUserSelect())
        self.add_item(ToggleEnabledButton(row=2))
        self.add_item(ClearAccessButton(row=2))
        self.add_item(BackToCategoryButton(row=2))

    async def build_embed(self, guild: discord.Guild) -> discord.Embed:
        cmd = COMMANDS_BY_KEY[self.command_key]
        enabled = await self.bot.db.is_command_enabled(
            guild.id, cmd.key, cmd.default_enabled
        )
        role_ids, user_ids = await self.bot.db.get_permission_targets(guild.id, cmd.key)
        roles = [guild.get_role(r) for r in role_ids]
        role_txt = " ".join(r.mention for r in roles if r) or "—"
        user_txt = " ".join(f"<@{u}>" for u in user_ids) or "—"

        embed = embeds.base(title=f"🔧 {cmd.label}", description=cmd.description)
        embed.add_field(
            name="Статус",
            value="🟢 включена" if enabled else "🔴 выключена",
            inline=False,
        )
        embed.add_field(name="Роли с доступом", value=role_txt, inline=False)
        embed.add_field(name="Пользователи с доступом", value=user_txt, inline=False)
        embed.set_footer(
            text="Выбор в списках полностью заменяет текущий набор. "
            "Владелец и супер-админ имеют доступ всегда."
        )
        return embed

    async def refresh(self, interaction: discord.Interaction) -> None:
        # Update toggle button label to match current state.
        enabled = await self.bot.db.is_command_enabled(
            interaction.guild_id, self.command_key, COMMANDS_BY_KEY[self.command_key].default_enabled
        )
        for child in self.children:
            if isinstance(child, ToggleEnabledButton):
                child.apply_state(enabled)
        embed = await self.build_embed(interaction.guild)  # type: ignore[arg-type]
        await interaction.response.edit_message(embed=embed, view=self)


class GrantRoleSelect(discord.ui.RoleSelect):
    def __init__(self):
        super().__init__(placeholder="🎭 Роли с доступом…", min_values=0, max_values=25)

    async def callback(self, interaction: discord.Interaction) -> None:
        view: CommandConfigView = self.view  # type: ignore[assignment]
        await view.bot.db.set_permission_targets(
            interaction.guild_id, view.command_key, "role", [r.id for r in self.values]
        )
        await view.refresh(interaction)


class GrantUserSelect(discord.ui.UserSelect):
    def __init__(self):
        super().__init__(placeholder="👤 Пользователи с доступом…", min_values=0, max_values=25)

    async def callback(self, interaction: discord.Interaction) -> None:
        view: CommandConfigView = self.view  # type: ignore[assignment]
        await view.bot.db.set_permission_targets(
            interaction.guild_id, view.command_key, "user", [u.id for u in self.values]
        )
        await view.refresh(interaction)


class ToggleEnabledButton(discord.ui.Button):
    def __init__(self, row: int = 0):
        super().__init__(label="Вкл/Выкл", style=discord.ButtonStyle.primary, row=row)

    def apply_state(self, enabled: bool) -> None:
        self.label = "Выключить" if enabled else "Включить"
        self.style = discord.ButtonStyle.danger if enabled else discord.ButtonStyle.success

    async def callback(self, interaction: discord.Interaction) -> None:
        view: CommandConfigView = self.view  # type: ignore[assignment]
        cmd = COMMANDS_BY_KEY[view.command_key]
        current = await view.bot.db.is_command_enabled(
            interaction.guild_id, cmd.key, cmd.default_enabled
        )
        await view.bot.db.set_command_enabled(interaction.guild_id, cmd.key, not current)
        await view.refresh(interaction)


class ClearAccessButton(discord.ui.Button):
    def __init__(self, row: int = 0):
        super().__init__(label="Очистить доступ", emoji="🧹", style=discord.ButtonStyle.secondary, row=row)

    async def callback(self, interaction: discord.Interaction) -> None:
        view: CommandConfigView = self.view  # type: ignore[assignment]
        await view.bot.db.set_permission_targets(interaction.guild_id, view.command_key, "role", [])
        await view.bot.db.set_permission_targets(interaction.guild_id, view.command_key, "user", [])
        await view.refresh(interaction)


class BackToCategoryButton(discord.ui.Button):
    def __init__(self, row: int = 0):
        super().__init__(label="Назад", emoji="◀️", style=discord.ButtonStyle.secondary, row=row)

    async def callback(self, interaction: discord.Interaction) -> None:
        view: CommandConfigView = self.view  # type: ignore[assignment]
        new_view = CategoryView(view.bot, view.author_id, view.category_key)
        new_view.message = view.message
        embed = await new_view.build_embed(interaction.guild)  # type: ignore[arg-type]
        await interaction.response.edit_message(embed=embed, view=new_view)


# --------------------------------------------------------------------------
# Shared navigation helpers
# --------------------------------------------------------------------------


class BackHomeButton(discord.ui.Button):
    def __init__(self, row: int = 0):
        super().__init__(label="Назад", emoji="◀️", style=discord.ButtonStyle.secondary, row=row)

    async def callback(self, interaction: discord.Interaction) -> None:
        view: BaseConfigView = self.view  # type: ignore[assignment]
        await _go_home(interaction, view)


async def _go_home(interaction: discord.Interaction, view: BaseConfigView) -> None:
    new_view = HomeView(view.bot, view.author_id)
    new_view.message = view.message
    embed = await new_view.build_embed(interaction.guild)  # type: ignore[arg-type]
    await interaction.response.edit_message(embed=embed, view=new_view)
