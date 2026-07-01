"""Interactive ``/settings`` dashboard.

A single ephemeral message that lets the owner / super admin configure every
server module by clicking — channels, toggles, roles and text — instead of
remembering a dozen slash commands. Navigation: Home → section → edit in place.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import discord

from core import embeds
from core.registry import CATEGORIES, CATEGORY_BY_KEY

if TYPE_CHECKING:
    from bot import SamuraiBot

TIMEOUT = 240.0
TEXT = [discord.ChannelType.text, discord.ChannelType.news]


def _ch(guild: discord.Guild, cid: Optional[int]) -> str:
    ch = guild.get_channel(cid) if cid else None
    return ch.mention if ch else "не задан"


def _role(guild: discord.Guild, rid: Optional[int]) -> str:
    r = guild.get_role(rid) if rid else None
    return r.mention if r else "не задана"


def _onoff(value) -> str:
    return "🟢 включено" if value else "🔴 выключено"


# --------------------------------------------------------------------------
# Generic, reusable components
# --------------------------------------------------------------------------


class GuildChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, field: str, placeholder: str):
        super().__init__(placeholder=placeholder, channel_types=TEXT, min_values=1, max_values=1)
        self.field = field

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.view.save_guild(interaction, **{self.field: self.values[0].id})  # type: ignore[attr-defined]


class TicketChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, field: str, placeholder: str, category: bool = False):
        types = [discord.ChannelType.category] if category else TEXT
        super().__init__(placeholder=placeholder, channel_types=types, min_values=1, max_values=1)
        self.field = field

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.view.save_ticket(interaction, **{self.field: self.values[0].id})  # type: ignore[attr-defined]


class GuildRoleSelect(discord.ui.RoleSelect):
    def __init__(self, field: str, placeholder: str, ticket: bool = False):
        super().__init__(placeholder=placeholder, min_values=1, max_values=1)
        self.field = field
        self.ticket = ticket

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.ticket:
            await self.view.save_ticket(interaction, **{self.field: self.values[0].id})  # type: ignore[attr-defined]
        else:
            await self.view.save_guild(interaction, **{self.field: self.values[0].id})  # type: ignore[attr-defined]


class ResetButton(discord.ui.Button):
    def __init__(self, label: str, fields: dict, ticket: bool = False, row: int = 4):
        super().__init__(label=label, emoji="🧹", style=discord.ButtonStyle.secondary, row=row)
        self.fields = fields
        self.ticket = ticket

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.ticket:
            await self.view.save_ticket(interaction, **self.fields)  # type: ignore[attr-defined]
        else:
            await self.view.save_guild(interaction, **self.fields)  # type: ignore[attr-defined]


class ToggleButton(discord.ui.Button):
    def __init__(self, field: str, current: bool, on="Включить", off="Выключить", row: int = 4):
        super().__init__(
            label=off if current else on,
            style=discord.ButtonStyle.danger if current else discord.ButtonStyle.success,
            row=row,
        )
        self.field = field
        self.current = current

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.view.save_guild(interaction, **{self.field: 0 if self.current else 1})  # type: ignore[attr-defined]


class BackButton(discord.ui.Button):
    def __init__(self, row: int = 4):
        super().__init__(label="Назад", emoji="◀️", style=discord.ButtonStyle.secondary, row=row)

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.view.go_home(interaction)  # type: ignore[attr-defined]


# --------------------------------------------------------------------------
# Base classes
# --------------------------------------------------------------------------


class SettingsBase(discord.ui.View):
    def __init__(self, bot: "SamuraiBot", author_id: int, guild_id: int):
        super().__init__(timeout=TIMEOUT)
        self.bot = bot
        self.author_id = author_id
        self.guild_id = guild_id
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                embed=embeds.error("Это меню открыто другим пользователем."), ephemeral=True
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

    async def go_home(self, interaction: discord.Interaction) -> None:
        view = SettingsHome(self.bot, self.author_id, self.guild_id)
        view.message = self.message
        await interaction.response.edit_message(embed=await view.build_embed(), view=view)


class SettingsSection(SettingsBase):
    async def build_embed(self) -> discord.Embed:  # pragma: no cover - overridden
        raise NotImplementedError

    async def async_init(self) -> None:
        """Add components that depend on current DB state. Overridden per section."""

    async def refresh(self, interaction: discord.Interaction) -> None:
        view = type(self)(self.bot, self.author_id, self.guild_id)
        view.message = self.message
        await view.async_init()
        await interaction.response.edit_message(embed=await view.build_embed(), view=view)

    async def save_guild(self, interaction: discord.Interaction, **fields) -> None:
        await self.bot.db.update_guild_config(self.guild_id, **fields)
        await self.refresh(interaction)

    async def save_ticket(self, interaction: discord.Interaction, **fields) -> None:
        await self.bot.db.update_ticket_config(self.guild_id, **fields)
        await self.refresh(interaction)


# --------------------------------------------------------------------------
# Home
# --------------------------------------------------------------------------


class SettingsHome(SettingsBase):
    def __init__(self, bot: "SamuraiBot", author_id: int, guild_id: int):
        super().__init__(bot, author_id, guild_id)
        self.add_item(SectionSelect())
        self.add_item(self._CloseButton())

    async def build_embed(self) -> discord.Embed:
        guild = self.bot.get_guild(self.guild_id)
        assert guild is not None
        cfg = await self.bot.db.get_guild_config(guild.id)
        tcfg = await self.bot.db.get_ticket_config(guild.id)
        themes = await self.bot.db.get_ticket_themes(guild.id)
        embed = embeds.base(
            title="🛠️ Настройки сервера",
            description="Выберите раздел в меню ниже, чтобы настроить модуль кликами.\n"
                        "Права доступа к командам — в отдельном меню `/config`.",
        )
        embed.add_field(name="📋 Логи модерации", value=_ch(guild, cfg["log_channel_id"]), inline=True)
        embed.add_field(name="🎣 Анти-фишинг", value=_onoff(cfg["antiphish_enabled"]), inline=True)
        embed.add_field(name="👋 Приветствия", value=_ch(guild, cfg["welcome_channel_id"]), inline=True)
        embed.add_field(name="📈 Уровни", value=_onoff(cfg["levels_enabled"]), inline=True)
        embed.add_field(name="⭐ Звёздная доска", value=_ch(guild, cfg["starboard_channel_id"]), inline=True)
        embed.add_field(name="🎫 Тикеты",
                        value=f"{len(themes)} тем · кулдаун {tcfg['cooldown_seconds']}с", inline=True)
        mod_roles = await self.bot.db.get_category_roles(guild.id, "moderation")
        embed.add_field(
            name="🔐 Роли модерации",
            value=" ".join(f"<@&{r}>" for r in mod_roles) if mod_roles else "не заданы",
            inline=False,
        )
        embed.set_footer(text="Меню активно 4 минуты")
        return embed

    class _CloseButton(discord.ui.Button):
        def __init__(self):
            super().__init__(label="Закрыть", emoji="✖️", style=discord.ButtonStyle.danger, row=1)

        async def callback(self, interaction: discord.Interaction) -> None:
            await interaction.response.edit_message(
                embed=embeds.base(description="Меню настроек закрыто."), view=None
            )
            self.view.stop()  # type: ignore[union-attr]


class SectionSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label=label, value=key, emoji=emoji, description=desc)
            for key, emoji, label, desc in _SECTION_META
        ]
        super().__init__(placeholder="📂 Выберите раздел настроек…", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        home: SettingsHome = self.view  # type: ignore[assignment]
        cls = _SECTION_CLASSES[self.values[0]]
        view = cls(home.bot, home.author_id, home.guild_id)
        view.message = home.message
        await view.async_init()
        await interaction.response.edit_message(embed=await view.build_embed(), view=view)


# --------------------------------------------------------------------------
# Sections
# --------------------------------------------------------------------------


class LoggingSection(SettingsSection):
    async def async_init(self) -> None:
        self.add_item(GuildChannelSelect("log_channel_id", "Выберите канал для логов модерации…"))
        self.add_item(ResetButton("Очистить", {"log_channel_id": None}))
        self.add_item(BackButton())

    async def build_embed(self) -> discord.Embed:
        guild = self.bot.get_guild(self.guild_id)
        assert guild
        cfg = await self.bot.db.get_guild_config(guild.id)
        embed = embeds.base(
            title="📋 Логи модерации",
            description="Канал, куда бот пишет действия модерации, кейсы, транскрипты "
                        "тикетов и оценки.",
        )
        embed.add_field(name="Текущий канал", value=_ch(guild, cfg["log_channel_id"]), inline=False)
        return embed


class AntiPhishSection(SettingsSection):
    async def async_init(self) -> None:
        cfg = await self.bot.db.get_guild_config(self.guild_id)
        self.add_item(AntiPhishActionSelect(cfg["antiphish_action"]))
        self.add_item(GuildChannelSelect("antiphish_log_id", "Канал для логов анти-фишинга…"))
        self.add_item(ToggleButton("antiphish_enabled", bool(cfg["antiphish_enabled"])))
        self.add_item(BackButton())

    async def build_embed(self) -> discord.Embed:
        guild = self.bot.get_guild(self.guild_id)
        assert guild
        cfg = await self.bot.db.get_guild_config(guild.id)
        actions = {"delete": "Удалить сообщение", "warn": "Удалить + предупреждение",
                   "timeout": "Удалить + тайм-аут 1ч", "kick": "Удалить + кик", "ban": "Удалить + бан"}
        embed = embeds.base(
            title="🎣 Анти-фишинг",
            description="Бот удаляет фишинговые ссылки и подделки под бренды. "
                        "Персонал и доверенные не проверяются.",
        )
        embed.add_field(name="Статус", value=_onoff(cfg["antiphish_enabled"]), inline=True)
        embed.add_field(name="Действие", value=actions.get(cfg["antiphish_action"], "—"), inline=True)
        log_id = cfg["antiphish_log_id"] or cfg["log_channel_id"]
        embed.add_field(name="Канал логов", value=_ch(guild, log_id), inline=True)
        return embed


class AntiPhishActionSelect(discord.ui.Select):
    def __init__(self, current: str):
        opts = [
            ("delete", "Только удалить сообщение", "🗑️"),
            ("warn", "Удалить и выдать предупреждение", "⚠️"),
            ("timeout", "Удалить и тайм-аут на 1 час", "🔇"),
            ("kick", "Удалить и кикнуть", "👢"),
            ("ban", "Удалить и забанить", "🔨"),
        ]
        super().__init__(
            placeholder="Что делать с нарушителем…",
            options=[discord.SelectOption(label=l, value=v, emoji=e, default=(v == current))
                     for v, l, e in opts],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.view.save_guild(interaction, antiphish_action=self.values[0])  # type: ignore[attr-defined]


class WelcomeSection(SettingsSection):
    async def async_init(self) -> None:
        self.add_item(GuildChannelSelect("welcome_channel_id", "Канал приветствий…"))
        self.add_item(GuildChannelSelect("goodbye_channel_id", "Канал прощаний…"))
        self.add_item(GuildRoleSelect("autorole_id", "Авто-роль для новичков…"))
        self.add_item(self._EditWelcome())
        self.add_item(self._EditGoodbye())
        self.add_item(BackButton(row=4))

    async def build_embed(self) -> discord.Embed:
        guild = self.bot.get_guild(self.guild_id)
        assert guild
        cfg = await self.bot.db.get_guild_config(guild.id)
        from cogs.welcome import DEFAULT_GOODBYE, DEFAULT_WELCOME
        embed = embeds.base(
            title="👋 Приветствия, прощания и авто-роль",
            description="Плейсхолдеры в тексте: `{user}` упоминание · `{name}` имя · "
                        "`{server}` сервер · `{count}` число участников.",
        )
        embed.add_field(name="Канал приветствий", value=_ch(guild, cfg["welcome_channel_id"]), inline=True)
        embed.add_field(name="Канал прощаний", value=_ch(guild, cfg["goodbye_channel_id"]), inline=True)
        embed.add_field(name="Авто-роль", value=_role(guild, cfg["autorole_id"]), inline=True)
        embed.add_field(name="Текст приветствия",
                        value=f"```{(cfg['welcome_message'] or DEFAULT_WELCOME)[:200]}```", inline=False)
        embed.add_field(name="Текст прощания",
                        value=f"```{(cfg['goodbye_message'] or DEFAULT_GOODBYE)[:200]}```", inline=False)
        return embed

    class _EditWelcome(discord.ui.Button):
        def __init__(self):
            super().__init__(label="Текст приветствия", emoji="✏️",
                             style=discord.ButtonStyle.primary, row=3)

        async def callback(self, interaction: discord.Interaction) -> None:
            await interaction.response.send_modal(
                TextFieldModal(self.view, "welcome_message", "Текст приветствия"))  # type: ignore[arg-type]

    class _EditGoodbye(discord.ui.Button):
        def __init__(self):
            super().__init__(label="Текст прощания", emoji="✏️",
                             style=discord.ButtonStyle.primary, row=3)

        async def callback(self, interaction: discord.Interaction) -> None:
            await interaction.response.send_modal(
                TextFieldModal(self.view, "goodbye_message", "Текст прощания"))  # type: ignore[arg-type]


class LevelsSection(SettingsSection):
    async def async_init(self) -> None:
        cfg = await self.bot.db.get_guild_config(self.guild_id)
        self.add_item(GuildChannelSelect("levelup_channel_id", "Канал для уведомлений о новом уровне…"))
        self.add_item(ToggleButton("levels_enabled", bool(cfg["levels_enabled"])))
        self.add_item(ResetButton("Писать в текущем канале", {"levelup_channel_id": None}, row=4))
        self.add_item(BackButton())

    async def build_embed(self) -> discord.Embed:
        guild = self.bot.get_guild(self.guild_id)
        assert guild
        cfg = await self.bot.db.get_guild_config(guild.id)
        rewards = await self.bot.db.get_level_rewards(guild.id)
        embed = embeds.base(
            title="📈 Уровни и опыт",
            description="Участники получают опыт за сообщения. Награды-роли за уровни — "
                        "командой `/levels reward add`.",
        )
        embed.add_field(name="Статус", value=_onoff(cfg["levels_enabled"]), inline=True)
        embed.add_field(name="Канал уведомлений",
                        value=_ch(guild, cfg["levelup_channel_id"]) if cfg["levelup_channel_id"]
                        else "текущий канал", inline=True)
        embed.add_field(name="Наград настроено", value=str(len(rewards)), inline=True)
        return embed


class StarboardSection(SettingsSection):
    async def async_init(self) -> None:
        self.add_item(GuildChannelSelect("starboard_channel_id", "Канал звёздной доски…"))
        self.add_item(self._EditEmoji())
        self.add_item(self._EditThreshold())
        self.add_item(ResetButton("Выключить", {"starboard_channel_id": None}, row=4))
        self.add_item(BackButton())

    async def build_embed(self) -> discord.Embed:
        guild = self.bot.get_guild(self.guild_id)
        assert guild
        cfg = await self.bot.db.get_guild_config(guild.id)
        embed = embeds.base(
            title="⭐ Звёздная доска",
            description="Сообщения, набравшие достаточно реакций, попадают в отдельный канал.",
        )
        embed.add_field(name="Канал", value=_ch(guild, cfg["starboard_channel_id"]), inline=True)
        embed.add_field(name="Эмодзи", value=cfg["starboard_emoji"], inline=True)
        embed.add_field(name="Порог реакций", value=str(cfg["starboard_threshold"]), inline=True)
        return embed

    class _EditEmoji(discord.ui.Button):
        def __init__(self):
            super().__init__(label="Эмодзи", emoji="😀", style=discord.ButtonStyle.primary, row=2)

        async def callback(self, interaction: discord.Interaction) -> None:
            await interaction.response.send_modal(EmojiModal(self.view))  # type: ignore[arg-type]

    class _EditThreshold(discord.ui.Button):
        def __init__(self):
            super().__init__(label="Порог", emoji="🔢", style=discord.ButtonStyle.primary, row=2)

        async def callback(self, interaction: discord.Interaction) -> None:
            await interaction.response.send_modal(ThresholdModal(self.view))  # type: ignore[arg-type]


class TicketsSection(SettingsSection):
    async def async_init(self) -> None:
        self.add_item(TicketChannelSelect("category_id", "Категория для тикетов…", category=True))
        self.add_item(GuildRoleSelect("support_role_id", "Роль поддержки по умолчанию…", ticket=True))
        self.add_item(self._EditCooldown())
        self.add_item(BackButton(row=4))

    async def build_embed(self) -> discord.Embed:
        guild = self.bot.get_guild(self.guild_id)
        assert guild
        tcfg = await self.bot.db.get_ticket_config(guild.id)
        themes = await self.bot.db.get_ticket_themes(guild.id)
        cat = guild.get_channel(tcfg["category_id"]) if tcfg["category_id"] else None
        embed = embeds.base(
            title="🎫 Тикеты",
            description="Базовые настройки тикетов. Темы (с собственными категориями и "
                        "ролями) — `/ticket theme add`, затем `/ticket panel`.",
        )
        embed.add_field(name="Категория", value=cat.mention if cat else "не задана", inline=True)
        embed.add_field(name="Роль поддержки", value=_role(guild, tcfg["support_role_id"]), inline=True)
        embed.add_field(name="Кулдаун", value=f"{tcfg['cooldown_seconds']} с", inline=True)
        if themes:
            embed.add_field(
                name=f"Темы ({len(themes)})",
                value=", ".join(f"{t['emoji'] or '•'} {t['label']}" for t in themes)[:1000],
                inline=False,
            )
        return embed

    class _EditCooldown(discord.ui.Button):
        def __init__(self):
            super().__init__(label="Кулдаун", emoji="⏱️", style=discord.ButtonStyle.primary, row=3)

        async def callback(self, interaction: discord.Interaction) -> None:
            await interaction.response.send_modal(CooldownModal(self.view))  # type: ignore[arg-type]


class AccessRolesSection(SettingsSection):
    """Assign roles that may use an entire command category (e.g. moderation)."""

    def __init__(self, bot: "SamuraiBot", author_id: int, guild_id: int):
        super().__init__(bot, author_id, guild_id)
        self.category_key = "moderation"

    async def async_init(self) -> None:
        self.add_item(AccessCategorySelect(self.category_key))
        self.add_item(AccessRoleSelect())
        self.add_item(AccessClearButton())
        self.add_item(BackButton())

    async def refresh(self, interaction: discord.Interaction) -> None:
        view = AccessRolesSection(self.bot, self.author_id, self.guild_id)
        view.category_key = self.category_key
        view.message = self.message
        await view.async_init()
        await interaction.response.edit_message(embed=await view.build_embed(), view=view)

    async def build_embed(self) -> discord.Embed:
        guild = self.bot.get_guild(self.guild_id)
        assert guild
        category = CATEGORY_BY_KEY[self.category_key]
        role_ids = await self.bot.db.get_category_roles(guild.id, self.category_key)
        roles = " ".join(f"<@&{rid}>" for rid in role_ids) or "—"
        embed = embeds.base(
            title="🔐 Роли доступа к модулям",
            description=(
                "Выберите модуль, затем отметьте роли — они смогут пользоваться **всеми "
                "командами этого модуля**, без выдачи по одной.\n\n"
                "👑 Владелец, 🛡️ администраторы Discord и ⭐ супер-админ имеют полный "
                "доступ всегда. Тонкая настройка отдельных команд — в `/config`."
            ),
        )
        embed.add_field(name="Выбранный модуль", value=f"{category.emoji} {category.label}", inline=False)
        embed.add_field(name="Роли с доступом", value=roles, inline=False)
        # Overview of all configured modules.
        allm = await self.bot.db.all_category_roles(guild.id)
        if allm:
            lines = []
            for cat in CATEGORIES:
                rids = allm.get(cat.key)
                if rids:
                    lines.append(f"{cat.emoji} {cat.label}: " + " ".join(f"<@&{r}>" for r in rids))
            if lines:
                embed.add_field(name="Все настроенные модули", value="\n".join(lines)[:1000], inline=False)
        return embed


class AccessCategorySelect(discord.ui.Select):
    def __init__(self, current: str):
        options = [
            discord.SelectOption(label=cat.label, value=cat.key, emoji=cat.emoji,
                                 default=(cat.key == current))
            for cat in CATEGORIES
        ]
        super().__init__(placeholder="📂 Выберите модуль…", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        view: AccessRolesSection = self.view  # type: ignore[assignment]
        view.category_key = self.values[0]
        await view.refresh(interaction)


class AccessRoleSelect(discord.ui.RoleSelect):
    def __init__(self):
        super().__init__(placeholder="🎭 Роли с доступом к модулю…", min_values=0, max_values=25)

    async def callback(self, interaction: discord.Interaction) -> None:
        view: AccessRolesSection = self.view  # type: ignore[assignment]
        await view.bot.db.set_category_roles(
            view.guild_id, view.category_key, [r.id for r in self.values]
        )
        await view.refresh(interaction)


class AccessClearButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Очистить модуль", emoji="🧹",
                         style=discord.ButtonStyle.secondary, row=4)

    async def callback(self, interaction: discord.Interaction) -> None:
        view: AccessRolesSection = self.view  # type: ignore[assignment]
        await view.bot.db.set_category_roles(view.guild_id, view.category_key, [])
        await view.refresh(interaction)


# --------------------------------------------------------------------------
# Modals
# --------------------------------------------------------------------------


class TextFieldModal(discord.ui.Modal):
    def __init__(self, section: SettingsSection, field: str, title: str):
        super().__init__(title=title)
        self.section = section
        self.field = field
        self.text = discord.ui.TextInput(
            label="Текст (пусто = стандартный)",
            style=discord.TextStyle.paragraph, required=False, max_length=1000,
        )
        self.add_item(self.text)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        value = str(self.text.value).strip() or None
        await self.section.bot.db.update_guild_config(self.section.guild_id, **{self.field: value})
        await _rerender(interaction, self.section)


class EmojiModal(discord.ui.Modal):
    def __init__(self, section: SettingsSection):
        super().__init__(title="Эмодзи доски")
        self.section = section
        self.value = discord.ui.TextInput(label="Эмодзи реакции", default="⭐", max_length=64)
        self.add_item(self.value)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.section.bot.db.update_guild_config(
            self.section.guild_id, starboard_emoji=str(self.value.value).strip() or "⭐")
        await _rerender(interaction, self.section)


class ThresholdModal(discord.ui.Modal):
    def __init__(self, section: SettingsSection):
        super().__init__(title="Порог звёздной доски")
        self.section = section
        self.value = discord.ui.TextInput(label="Сколько реакций нужно (1–100)", max_length=3)
        self.add_item(self.value)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = str(self.value.value).strip()
        if not raw.isdigit() or not (1 <= int(raw) <= 100):
            await interaction.response.send_message(
                embed=embeds.error("Введите число от 1 до 100."), ephemeral=True)
            return
        await self.section.bot.db.update_guild_config(
            self.section.guild_id, starboard_threshold=int(raw))
        await _rerender(interaction, self.section)


class CooldownModal(discord.ui.Modal):
    def __init__(self, section: SettingsSection):
        super().__init__(title="Кулдаун тикетов")
        self.section = section
        self.value = discord.ui.TextInput(
            label="Секунд между тикетами (0 = выкл)", max_length=6, default="0")
        self.add_item(self.value)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = str(self.value.value).strip()
        if not raw.isdigit() or int(raw) > 86400:
            await interaction.response.send_message(
                embed=embeds.error("Введите число от 0 до 86400."), ephemeral=True)
            return
        await self.section.bot.db.update_ticket_config(
            self.section.guild_id, cooldown_seconds=int(raw))
        await _rerender(interaction, self.section)


async def _rerender(interaction: discord.Interaction, section: SettingsSection) -> None:
    view = type(section)(section.bot, section.author_id, section.guild_id)
    view.message = section.message
    await view.async_init()
    await interaction.response.edit_message(embed=await view.build_embed(), view=view)


# --------------------------------------------------------------------------
# Section registry (after classes are defined)
# --------------------------------------------------------------------------

_SECTION_META = [
    ("access", "🔐", "Роли доступа (модерация и др.)", "Кто может пользоваться модулями"),
    ("logs", "📋", "Логи модерации", "Канал для логов действий"),
    ("antiphish", "🎣", "Анти-фишинг", "Защита от фишинговых ссылок"),
    ("welcome", "👋", "Приветствия и авто-роль", "Вход/выход и роль новичку"),
    ("levels", "📈", "Уровни", "Опыт за активность и награды"),
    ("starboard", "⭐", "Звёздная доска", "Лучшие сообщения по реакциям"),
    ("tickets", "🎫", "Тикеты", "Категория, поддержка, кулдаун"),
]

_SECTION_CLASSES: dict[str, type[SettingsSection]] = {
    "access": AccessRolesSection,
    "logs": LoggingSection,
    "antiphish": AntiPhishSection,
    "welcome": WelcomeSection,
    "levels": LevelsSection,
    "starboard": StarboardSection,
    "tickets": TicketsSection,
}
