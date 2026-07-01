"""Runtime configuration loaded from environment / .env file."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Project root = directory containing this file.
BASE_DIR = Path(__file__).resolve().parent

load_dotenv(BASE_DIR / ".env")

DISCORD_TOKEN: str = os.getenv("DISCORD_TOKEN", "").strip()

# User who always keeps full access to every command, on every guild.
# Hard-coded by product requirement (JapanDino).
SUPER_ADMIN_ID: int = 694999466029088809

# The bot's developer, shown in the /botinfo profile card.
DEVELOPER_ID: int = 694999466029088809  # JapanDino

# Application (client) id — used to build the invite link.
APPLICATION_ID: int = 1521039959996371004
INVITE_URL: str = (
    f"https://discord.com/api/oauth2/authorize?client_id={APPLICATION_ID}"
    "&permissions=1099780189206&scope=bot%20applications.commands"
)

# Bot embed accent colour (Discord blurple-ish).
EMBED_COLOR: int = 0x5865F2
ERROR_COLOR: int = 0xED4245
SUCCESS_COLOR: int = 0x57F287
WARN_COLOR: int = 0xFEE75C

_db_path = os.getenv("DATABASE_PATH", "data/bot.db").strip()
DATABASE_PATH: Path = (BASE_DIR / _db_path) if not os.path.isabs(_db_path) else Path(_db_path)


def _parse_guild_ids(raw: str) -> list[int]:
    ids: list[int] = []
    for chunk in raw.replace(";", ",").split(","):
        chunk = chunk.strip()
        if chunk.isdigit():
            ids.append(int(chunk))
    return ids


DEV_GUILD_IDS: list[int] = _parse_guild_ids(os.getenv("DEV_GUILD_IDS", ""))
