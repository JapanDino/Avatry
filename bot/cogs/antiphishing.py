"""Anti-phishing & suspicious-activity protection.

Scans messages for phishing links (known scam domains + brand look-alikes such
as "dlscord-nitro.com") and, when enabled, takes a configurable action against
the sender. Staff and privileged users are never scanned to avoid false hits.
"""
from __future__ import annotations

import re
from datetime import timedelta
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from core import embeds, modlog
from core.permissions import is_privileged, requires

# Domains that are legitimate brand targets often impersonated by scammers.
OFFICIAL_DOMAINS = {
    "discord.com", "discord.gg", "discord.gift", "discordapp.com", "discordapp.net",
    "discord.media", "discord.new", "discord.dev", "discordstatus.com",
    "steamcommunity.com", "steampowered.com", "store.steampowered.com",
}

# Curated set of frequently-seen scam domains (extend as needed).
KNOWN_PHISHING_DOMAINS = {
    "discord-nitro.com", "discordnitro.com", "discord-gift.com", "discordgift.site",
    "dlscord.com", "dlscord.gift", "discrod.com", "discordc.gift", "discorde.gift",
    "steamcommunity-gift.com", "steamcommunity.ru.com", "discord-airdrop.com",
    "discordapp.gift", "discord-nitro.info", "nitro-discord.com", "free-nitro.com",
}

# Brand tokens that, when present in a non-official domain, signal a look-alike.
BRAND_TOKENS = ("discord", "steam", "nitro")

SUSPICIOUS_PHRASES = (
    "free nitro", "free discord nitro", "nitro for free", "бесплатный нитро",
    "халява нитро", "steam gift", "claim your gift", "airdrop", "free gift",
)

URL_RE = re.compile(
    r"(?:https?://)?(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/[^\s]*)?", re.IGNORECASE
)
HOST_RE = re.compile(r"(?:https?://)?(?:www\.)?([a-z0-9.-]+\.[a-z]{2,})", re.IGNORECASE)

ACTION_CHOICES = [
    app_commands.Choice(name="Удалить сообщение", value="delete"),
    app_commands.Choice(name="Удалить + предупреждение", value="warn"),
    app_commands.Choice(name="Удалить + тайм-аут (1ч)", value="timeout"),
    app_commands.Choice(name="Удалить + кик", value="kick"),
    app_commands.Choice(name="Удалить + бан", value="ban"),
]
ACTION_LABELS = {c.value: c.name for c in ACTION_CHOICES}


def _registrable(host: str) -> str:
    """Return the last two labels of a hostname (best-effort registrable domain)."""
    parts = host.lower().strip(".").split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host.lower()


def detect_phishing(content: str) -> Optional[str]:
    """Return a short reason string if the message looks like phishing, else None."""
    lowered = content.lower()
    hosts: list[str] = []
    for match in URL_RE.finditer(content):
        host_match = HOST_RE.match(match.group(0))
        if host_match:
            hosts.append(host_match.group(1).lower())

    for host in hosts:
        reg = _registrable(host)
        if host in KNOWN_PHISHING_DOMAINS or reg in KNOWN_PHISHING_DOMAINS:
            return f"известный фишинговый домен: `{host}`"
        if reg in OFFICIAL_DOMAINS or host in OFFICIAL_DOMAINS:
            continue  # genuine brand domain
        if any(token in host for token in BRAND_TOKENS):
            return f"домен-подделка под известный бренд: `{host}`"

    has_link = bool(hosts)
    if has_link and any(phrase in lowered for phrase in SUSPICIOUS_PHRASES):
        return "ссылка вместе с признаками скам-раздачи (free nitro / gift)"
    return None


class AntiPhishing(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    antiphish = app_commands.Group(
        name="antiphish", description="Защита от фишинговых ссылок", guild_only=True
    )

    # ---- message scanning ----------------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        if not isinstance(message.author, discord.Member):
            return
        # Never scan staff / privileged members.
        if (
            is_privileged(message.author, message.guild)
            or message.author.guild_permissions.manage_messages
        ):
            return

        cfg = await self.bot.db.get_guild_config(message.guild.id)
        if not cfg["antiphish_enabled"]:
            return

        reason = detect_phishing(message.content)
        if reason is None:
            return
        await self._handle_phishing(message, cfg["antiphish_action"], reason)

    async def _handle_phishing(
        self, message: discord.Message, action: str, reason: str
    ) -> None:
        guild = message.guild
        member = message.author
        assert guild and isinstance(member, discord.Member)

        try:
            await message.delete()
        except discord.HTTPException:
            pass

        applied = "удалено сообщение"
        full_reason = f"Anti-phishing: {reason}"
        try:
            if action == "timeout":
                await member.timeout(timedelta(hours=1), reason=full_reason)
                applied += " + тайм-аут 1ч"
            elif action == "kick":
                await member.kick(reason=full_reason)
                applied += " + кик"
            elif action == "ban":
                await member.ban(reason=full_reason, delete_message_seconds=86400)
                applied += " + бан"
            elif action == "warn":
                await self.bot.db.add_case(
                    guild.id, member.id, self.bot.user.id, "warn", full_reason
                )
                applied += " + предупреждение"
        except discord.Forbidden:
            applied += " (доп. действие не удалось: нет прав)"

        try:
            await message.channel.send(
                embed=embeds.warn(
                    f"{member.mention}, обнаружена подозрительная ссылка и она была удалена."
                ),
                delete_after=8,
            )
        except discord.HTTPException:
            pass

        log_embed = modlog.action_embed(
            "🎣 Анти-фишинг",
            member,
            self.bot.user,
            reason,
            extra={"Действие": applied, "Канал": message.channel.mention},
        )
        # Prefer the dedicated anti-phish log channel, fall back to the modlog.
        cfg = await self.bot.db.get_guild_config(guild.id)
        target_id = cfg["antiphish_log_id"] or cfg["log_channel_id"]
        if target_id:
            channel = guild.get_channel(target_id)
            if isinstance(channel, discord.TextChannel):
                try:
                    await channel.send(embed=log_embed)
                except discord.HTTPException:
                    pass

    # ---- configuration commands ----------------------------------------

    @antiphish.command(name="status", description="Показать настройки анти-фишинга")
    @requires("antiphish")
    async def status(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        assert guild
        cfg = await self.bot.db.get_guild_config(guild.id)
        log_id = cfg["antiphish_log_id"] or cfg["log_channel_id"]
        log_channel = guild.get_channel(log_id) if log_id else None
        embed = embeds.base(title="🎣 Анти-фишинг")
        embed.add_field(
            name="Статус",
            value="🟢 включён" if cfg["antiphish_enabled"] else "🔴 выключен",
            inline=True,
        )
        embed.add_field(
            name="Действие",
            value=ACTION_LABELS.get(cfg["antiphish_action"], cfg["antiphish_action"]),
            inline=True,
        )
        embed.add_field(
            name="Канал логов",
            value=log_channel.mention if log_channel else "не задан",
            inline=True,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @antiphish.command(name="toggle", description="Включить/выключить анти-фишинг")
    @app_commands.describe(enabled="Включить защиту?")
    @requires("antiphish")
    async def toggle(self, interaction: discord.Interaction, enabled: bool) -> None:
        guild = interaction.guild
        assert guild
        await self.bot.db.update_guild_config(guild.id, antiphish_enabled=int(enabled))
        await interaction.response.send_message(
            embed=embeds.success(
                f"Анти-фишинг {'включён' if enabled else 'выключен'}."
            ),
            ephemeral=True,
        )

    @antiphish.command(name="action", description="Выбрать действие при обнаружении фишинга")
    @app_commands.describe(action="Что делать с нарушителем")
    @app_commands.choices(action=ACTION_CHOICES)
    @requires("antiphish")
    async def action(
        self, interaction: discord.Interaction, action: app_commands.Choice[str]
    ) -> None:
        guild = interaction.guild
        assert guild
        await self.bot.db.update_guild_config(guild.id, antiphish_action=action.value)
        await interaction.response.send_message(
            embed=embeds.success(f"Действие установлено: **{action.name}**."),
            ephemeral=True,
        )

    @antiphish.command(name="logchannel", description="Канал для логов анти-фишинга")
    @app_commands.describe(channel="Канал (не указывать — сбросить)")
    @requires("antiphish")
    async def logchannel(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
    ) -> None:
        guild = interaction.guild
        assert guild
        await self.bot.db.update_guild_config(
            guild.id, antiphish_log_id=channel.id if channel else None
        )
        await interaction.response.send_message(
            embed=embeds.success(
                f"Канал логов: {channel.mention}" if channel else "Канал логов сброшен."
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AntiPhishing(bot))
