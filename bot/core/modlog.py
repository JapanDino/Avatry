"""Shared helpers for moderation logging and target validation."""
from __future__ import annotations

import re
from typing import Optional

import discord

import config

_DURATION_RE = re.compile(r"(?P<value>\d+)\s*(?P<unit>[smhdw])", re.IGNORECASE)
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
MAX_TIMEOUT_SECONDS = 28 * 86400  # Discord hard limit


def parse_duration(text: str) -> Optional[int]:
    """Parse a human duration like '30m', '2h', '1d 12h' into seconds.

    Returns None if nothing parseable is found.
    """
    total = 0
    found = False
    for match in _DURATION_RE.finditer(text):
        found = True
        total += int(match.group("value")) * _UNIT_SECONDS[match.group("unit").lower()]
    return total if found else None


def hierarchy_block(
    guild: discord.Guild,
    actor: discord.Member,
    target: discord.Member,
) -> Optional[str]:
    """Return a human-readable reason the action is not allowed, or None if OK."""
    if target.id == actor.id:
        return "Нельзя применить это действие к самому себе."
    if target.id == guild.owner_id:
        return "Нельзя применить это действие к владельцу сервера."
    if target.id == config.SUPER_ADMIN_ID:
        return "Этот пользователь защищён от модерации."
    me = guild.me
    if me is not None and target.top_role >= me.top_role:
        return "Роль пользователя выше или равна роли бота — не могу выполнить действие."
    # Owner / super admin actor bypasses role-height comparison against target.
    if actor.id in (guild.owner_id, config.SUPER_ADMIN_ID):
        return None
    if target.top_role >= actor.top_role:
        return "Роль пользователя выше или равна вашей — действие запрещено."
    return None


async def apply_action(
    guild: discord.Guild,
    member: discord.Member,
    action: str,
    *,
    duration_seconds: Optional[int] = None,
    reason: str = "",
) -> str:
    """Apply a moderation action to a member. Returns a human label of what
    happened. Raises discord.Forbidden if the bot lacks permission."""
    import datetime as _dt

    if action == "timeout":
        seconds = min(duration_seconds or 3600, MAX_TIMEOUT_SECONDS)
        until = discord.utils.utcnow() + _dt.timedelta(seconds=seconds)
        await member.timeout(until, reason=reason)
        return f"тайм-аут до {discord.utils.format_dt(until, style='R')}"
    if action == "kick":
        await member.kick(reason=reason)
        return "кик"
    if action == "ban":
        await member.ban(reason=reason, delete_message_seconds=0)
        return "бан"
    return action


async def send_modlog(
    bot: discord.Client,
    guild_id: int,
    embed: discord.Embed,
) -> None:
    """Send an embed to the configured moderation log channel, if any."""
    db = getattr(bot, "db", None)
    if db is None:
        return
    cfg = await db.get_guild_config(guild_id)
    channel_id = cfg["log_channel_id"]
    if not channel_id:
        return
    channel = bot.get_channel(channel_id)
    if isinstance(channel, discord.TextChannel):
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            pass


def action_embed(
    title: str,
    target: discord.abc.User,
    moderator: discord.abc.User,
    reason: str,
    extra: Optional[dict[str, str]] = None,
) -> discord.Embed:
    embed = discord.Embed(title=title, color=config.EMBED_COLOR)
    embed.add_field(name="Пользователь", value=f"{target} (`{target.id}`)", inline=False)
    embed.add_field(name="Модератор", value=f"{moderator} (`{moderator.id}`)", inline=False)
    embed.add_field(name="Причина", value=reason or "не указана", inline=False)
    for name, value in (extra or {}).items():
        embed.add_field(name=name, value=value, inline=True)
    embed.timestamp = discord.utils.utcnow()
    return embed
