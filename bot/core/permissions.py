"""Access-control core.

Permission model (per guild):
  * The server owner always has full access.
  * The super admin (JapanDino) always has full access, on every guild.
  * Every other member is denied by default and must be granted access
    per-command via the ``/config`` menu — either by role or individually.
  * A command can also be globally disabled for a guild; owner / super admin
    bypass the disabled state so they can re-enable it.

Guard a slash command callback with ``@requires("command_key")``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

import config
from core.registry import CATEGORY_OF_COMMAND, COMMANDS_BY_KEY

if TYPE_CHECKING:
    from bot import SamuraiBot

DEVELOPER_ROLE_NAME = "developer"


class MissingAccess(app_commands.CheckFailure):
    """Raised when a member is not allowed to run a managed command."""

    def __init__(self, command_key: str, reason: str = "no_access"):
        self.command_key = command_key
        self.reason = reason
        super().__init__(f"Missing access to {command_key} ({reason})")


def is_privileged(user: discord.abc.User, guild: discord.Guild) -> bool:
    """Owner or super admin — bypasses all per-command configuration."""
    return user.id == config.SUPER_ADMIN_ID or user.id == guild.owner_id


def has_developer_role(user: discord.abc.User) -> bool:
    """Members with the developer role get full bot-command access."""
    if not isinstance(user, discord.Member):
        return False
    return any(role.name.casefold() == DEVELOPER_ROLE_NAME for role in user.roles)


def is_admin_access(user: discord.abc.User, guild: discord.Guild) -> bool:
    """Full access to every bot command: owner, super admin, developer role,
    or any member with the Discord ``Administrator`` permission."""
    if is_privileged(user, guild):
        return True
    if has_developer_role(user):
        return True
    return isinstance(user, discord.Member) and user.guild_permissions.administrator


async def member_can_use(
    bot: "SamuraiBot", member: discord.Member, command_key: str
) -> bool:
    guild = member.guild
    if is_admin_access(member, guild):
        return True

    meta = COMMANDS_BY_KEY.get(command_key)
    default_enabled = meta.default_enabled if meta else True
    if not await bot.db.is_command_enabled(guild.id, command_key, default_enabled):
        return False

    member_role_ids = {r.id for r in member.roles}

    # Per-command grants (user or role), set via /config.
    role_ids, user_ids = await bot.db.get_permission_targets(guild.id, command_key)
    if member.id in user_ids:
        return True
    if any(rid in member_role_ids for rid in role_ids):
        return True

    # Category access roles (e.g. moderation roles), set via /settings.
    category = CATEGORY_OF_COMMAND.get(command_key)
    if category is not None:
        cat_role_ids = await bot.db.get_category_roles(guild.id, category.key)
        if any(rid in member_role_ids for rid in cat_role_ids):
            return True

    return False


def requires(command_key: str):
    """app_commands check factory guarding a command by its registry key."""

    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            raise MissingAccess(command_key, reason="guild_only")
        bot: "SamuraiBot" = interaction.client  # type: ignore[assignment]
        if await member_can_use(bot, interaction.user, command_key):
            return True
        # Distinguish "disabled" from "not granted" for a clearer message.
        meta = COMMANDS_BY_KEY.get(command_key)
        default_enabled = meta.default_enabled if meta else True
        enabled = await bot.db.is_command_enabled(
            interaction.guild.id, command_key, default_enabled
        )
        raise MissingAccess(command_key, reason="disabled" if not enabled else "no_access")

    return app_commands.check(predicate)


def requires_hybrid(command_key: str):
    """commands.check guarding a *hybrid* command by its registry key.

    Works for both prefix (``k.``) and slash invocations because hybrid
    commands run ``commands.check`` predicates in both contexts.
    """

    async def predicate(ctx: commands.Context) -> bool:
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            raise commands.NoPrivateMessage()
        if await member_can_use(ctx.bot, ctx.author, command_key):  # type: ignore[arg-type]
            return True
        raise commands.CheckFailure("Нет доступа к этой команде.")

    return commands.check(predicate)
