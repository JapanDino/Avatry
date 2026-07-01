"""Owner-facing health and guild inventory reports."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, TYPE_CHECKING

import discord

import config

if TYPE_CHECKING:
    from bot import SamuraiBot


CRITICAL_PERMISSIONS: tuple[tuple[str, str], ...] = (
    ("administrator", "Administrator"),
    ("manage_guild", "Manage Server"),
    ("manage_roles", "Manage Roles"),
    ("manage_channels", "Manage Channels"),
    ("manage_messages", "Manage Messages"),
    ("kick_members", "Kick Members"),
    ("ban_members", "Ban Members"),
    ("moderate_members", "Timeout Members"),
    ("view_audit_log", "View Audit Log"),
    ("create_instant_invite", "Create Instant Invite"),
    ("send_messages", "Send Messages"),
    ("embed_links", "Embed Links"),
    ("attach_files", "Attach Files"),
    ("read_message_history", "Read Message History"),
)


def _utc_ts(dt: object) -> str:
    if dt is None:
        return "unknown"
    if isinstance(dt, datetime):
        return f"<t:{int(dt.timestamp())}:R>"
    return str(dt)


def _clip(value: str, limit: int = 1024) -> str:
    value = value.strip() or "-"
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "..."


def _chunk(lines: list[str], limit: int = 10) -> Iterable[list[str]]:
    for index in range(0, len(lines), limit):
        yield lines[index : index + limit]


def _file_size(path: Path) -> str:
    try:
        size = path.stat().st_size
    except OSError:
        return "unknown"
    units = ("B", "KB", "MB", "GB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def _uptime(bot: "SamuraiBot") -> str:
    now = discord.utils.utcnow()
    delta = now - bot.start_time
    seconds = max(0, int(delta.total_seconds()))
    compact = str(timedelta(seconds=seconds))
    return compact


def _bot_member(guild: discord.Guild) -> discord.Member | None:
    return guild.me


def _permission_report(guild: discord.Guild) -> tuple[list[str], list[str]]:
    member = _bot_member(guild)
    if member is None:
        return [], ["Bot member is not cached"]
    perms = member.guild_permissions
    granted: list[str] = []
    missing: list[str] = []
    for attr, label in CRITICAL_PERMISSIONS:
        if getattr(perms, attr, False):
            granted.append(label)
        else:
            missing.append(label)
    return granted, missing


def _invite_capability(guild: discord.Guild) -> str:
    member = _bot_member(guild)
    if member is None:
        return "unknown"
    channels: list[str] = []
    for channel in guild.text_channels:
        perms = channel.permissions_for(member)
        if perms.view_channel and perms.create_instant_invite:
            channels.append(channel.mention)
        if len(channels) >= 5:
            break
    if channels:
        more = ""
        total = sum(
            1
            for channel in guild.text_channels
            if channel.permissions_for(member).view_channel
            and channel.permissions_for(member).create_instant_invite
        )
        if total > len(channels):
            more = f" (+{total - len(channels)} more)"
        return ", ".join(channels) + more
    return "no channel with Create Instant Invite"


async def _activity_line(bot: "SamuraiBot", guild_id: int) -> str:
    try:
        totals = await bot.db.server_stats_totals(guild_id, 7)
    except Exception:  # noqa: BLE001 - reporting must not break bot startup
        return "activity: unavailable"
    return (
        f"7d: {int(totals['messages'])} msg, "
        f"{int(totals['commands_used'])} cmd, "
        f"+{int(totals['members_joined'])}/-{int(totals['members_left'])}"
    )


async def guild_summary_line(bot: "SamuraiBot", guild: discord.Guild) -> str:
    granted, missing = _permission_report(guild)
    risk = "ok" if not missing or "Administrator" in granted else f"missing {len(missing)} perms"
    activity = await _activity_line(bot, guild.id)
    owner = f"<@{guild.owner_id}>" if guild.owner_id else "unknown"
    return (
        f"**{guild.name}** (`{guild.id}`) - {guild.member_count or 0} members, "
        f"owner {owner}, {risk}; {activity}"
    )


async def build_guild_detail_embed(
    bot: "SamuraiBot", guild: discord.Guild, *, title: str | None = None
) -> discord.Embed:
    granted, missing = _permission_report(guild)
    activity = await _activity_line(bot, guild.id)
    member = _bot_member(guild)
    embed = discord.Embed(
        title=title or f"Guild detail: {guild.name}",
        color=config.EMBED_COLOR,
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Guild", value=f"{guild.name}\n`{guild.id}`", inline=True)
    embed.add_field(name="Owner", value=f"<@{guild.owner_id}>\n`{guild.owner_id}`", inline=True)
    embed.add_field(name="Members", value=str(guild.member_count or 0), inline=True)
    embed.add_field(
        name="Channels",
        value=(
            f"text {len(guild.text_channels)}, voice {len(guild.voice_channels)}, "
            f"categories {len(guild.categories)}"
        ),
        inline=True,
    )
    embed.add_field(name="Roles", value=str(len(guild.roles)), inline=True)
    embed.add_field(name="Joined", value=_utc_ts(getattr(member, "joined_at", None)), inline=True)
    embed.add_field(name="Activity", value=activity, inline=False)
    embed.add_field(
        name="Bot top role",
        value=getattr(getattr(member, "top_role", None), "mention", "unknown"),
        inline=True,
    )
    embed.add_field(
        name="Invite capability",
        value=_clip(_invite_capability(guild)),
        inline=False,
    )
    embed.add_field(
        name="Granted key perms",
        value=_clip(", ".join(granted) if granted else "none"),
        inline=False,
    )
    embed.add_field(
        name="Missing key perms",
        value=_clip(", ".join(missing) if missing else "none"),
        inline=False,
    )
    if guild.vanity_url_code:
        embed.add_field(
            name="Vanity URL",
            value=f"https://discord.gg/{guild.vanity_url_code}",
            inline=False,
        )
    return embed


async def build_overview_embeds(bot: "SamuraiBot", *, title: str = "Owner report") -> list[discord.Embed]:
    guilds = sorted(bot.guilds, key=lambda guild: guild.member_count or 0, reverse=True)
    total_members = sum(guild.member_count or 0 for guild in guilds)
    total_channels = sum(len(guild.channels) for guild in guilds)
    overview = discord.Embed(
        title=title,
        color=config.EMBED_COLOR,
        timestamp=discord.utils.utcnow(),
    )
    overview.add_field(name="Bot", value=f"{bot.user} (`{bot.user.id}`)" if bot.user else "unknown", inline=False)
    overview.add_field(name="Uptime", value=_uptime(bot), inline=True)
    overview.add_field(name="Latency", value=f"{bot.latency * 1000:.0f} ms", inline=True)
    overview.add_field(name="Guilds", value=str(len(guilds)), inline=True)
    overview.add_field(name="Cached members", value=str(total_members), inline=True)
    overview.add_field(name="Channels", value=str(total_channels), inline=True)
    overview.add_field(name="Commands", value=str(len(bot.tree.get_commands())), inline=True)
    overview.add_field(name="Database", value=f"{config.DATABASE_PATH}\n{_file_size(config.DATABASE_PATH)}", inline=False)

    lines = [await guild_summary_line(bot, guild) for guild in guilds]
    embeds = [overview]
    if not lines:
        empty = discord.Embed(
            title="Guild inventory",
            description="Bot is not connected to any guilds.",
            color=config.WARN_COLOR,
            timestamp=discord.utils.utcnow(),
        )
        embeds.append(empty)
        return embeds
    for page_no, page in enumerate(_chunk(lines, 8), start=1):
        embed = discord.Embed(
            title=f"Guild inventory {page_no}",
            description=_clip("\n".join(page), 4096),
            color=config.EMBED_COLOR,
            timestamp=discord.utils.utcnow(),
        )
        embeds.append(embed)
    return embeds


async def resolve_owner(bot: "SamuraiBot") -> discord.User | None:
    user = bot.get_user(config.SUPER_ADMIN_ID)
    if user is not None:
        return user
    try:
        return await bot.fetch_user(config.SUPER_ADMIN_ID)
    except discord.HTTPException:
        return None


async def send_owner_embeds(bot: "SamuraiBot", embeds: list[discord.Embed]) -> bool:
    owner = await resolve_owner(bot)
    if owner is None:
        return False
    try:
        for embed in embeds:
            await owner.send(embed=embed)
    except discord.HTTPException:
        return False
    return True
