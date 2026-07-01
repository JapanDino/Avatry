"""Direct-message notifications to the super admin (JapanDino).

Used for error reports, crashes and noteworthy lifecycle events. Includes a
small per-key cooldown so an error loop can't flood the DM channel.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Optional

import discord

import config

if TYPE_CHECKING:
    from bot import SamuraiBot

log = logging.getLogger("samurai.notify")

_LEVEL_COLORS = {
    "info": config.EMBED_COLOR,
    "success": config.SUCCESS_COLOR,
    "warning": config.WARN_COLOR,
    "error": config.ERROR_COLOR,
}
_LEVEL_EMOJI = {"info": "ℹ️", "success": "✅", "warning": "⚠️", "error": "🛑"}


class OwnerNotifier:
    def __init__(self, bot: "SamuraiBot", cooldown: float = 20.0):
        self.bot = bot
        self.cooldown = cooldown
        self._last_sent: dict[str, float] = {}

    async def send(
        self,
        title: str,
        description: str = "",
        *,
        level: str = "error",
        fields: Optional[dict[str, str]] = None,
        key: Optional[str] = None,
    ) -> None:
        """DM the super admin. Repeated events with the same ``key`` are
        throttled to one message per ``cooldown`` seconds."""
        throttle_key = key or title
        now = time.monotonic()
        last = self._last_sent.get(throttle_key, 0.0)
        if now - last < self.cooldown:
            return
        self._last_sent[throttle_key] = now

        user = self.bot.get_user(config.SUPER_ADMIN_ID)
        if user is None:
            try:
                user = await self.bot.fetch_user(config.SUPER_ADMIN_ID)
            except discord.HTTPException:
                log.warning("Could not resolve super admin %s", config.SUPER_ADMIN_ID)
                return

        emoji = _LEVEL_EMOJI.get(level, "•")
        embed = discord.Embed(
            title=f"{emoji} {title}"[:256],
            description=description[:4096] if description else None,
            color=_LEVEL_COLORS.get(level, config.EMBED_COLOR),
            timestamp=discord.utils.utcnow(),
        )
        for name, value in (fields or {}).items():
            embed.add_field(name=name[:256], value=(value or "—")[:1024], inline=False)
        embed.set_footer(text="K2-SO • авто-уведомление")

        try:
            await user.send(embed=embed)
        except discord.Forbidden:
            log.warning("Super admin has DMs closed — notification dropped: %s", title)
        except discord.HTTPException as exc:
            log.warning("Failed to DM super admin: %s", exc)

    async def report_exception(
        self,
        title: str,
        exc: BaseException,
        *,
        context: Optional[dict[str, str]] = None,
        key: Optional[str] = None,
    ) -> None:
        """Format and DM a traceback for an exception."""
        import traceback

        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        # Keep the tail of the traceback (most relevant) within Discord limits.
        tb_block = tb[-1500:]
        await self.send(
            title,
            description=f"```py\n{tb_block}\n```",
            level="error",
            fields=context,
            key=key or f"exc:{type(exc).__name__}:{title}",
        )
