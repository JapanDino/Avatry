"""Small helpers for consistent embed styling across cogs."""
from __future__ import annotations

import discord

import config


def base(title: str | None = None, description: str | None = None) -> discord.Embed:
    return discord.Embed(title=title, description=description, color=config.EMBED_COLOR)


def success(description: str, title: str | None = None) -> discord.Embed:
    return discord.Embed(
        title=title, description=f"✅ {description}", color=config.SUCCESS_COLOR
    )


def error(description: str, title: str | None = None) -> discord.Embed:
    return discord.Embed(
        title=title, description=f"❌ {description}", color=config.ERROR_COLOR
    )


def warn(description: str, title: str | None = None) -> discord.Embed:
    return discord.Embed(
        title=title, description=f"⚠️ {description}", color=config.WARN_COLOR
    )
