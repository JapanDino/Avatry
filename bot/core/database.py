"""Async SQLite storage layer (aiosqlite).

A single connection is opened at startup and shared across cogs. SQLite handles
the modest write volume of a single bot process comfortably; WAL mode keeps
reads non-blocking.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Iterable, Optional
import asyncio
import sqlite3

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS guild_config (
    guild_id            INTEGER PRIMARY KEY,
    log_channel_id      INTEGER,
    antiphish_enabled   INTEGER NOT NULL DEFAULT 0,
    antiphish_action    TEXT    NOT NULL DEFAULT 'delete',  -- delete | warn | timeout | kick | ban
    antiphish_log_id    INTEGER
);

CREATE TABLE IF NOT EXISTS command_settings (
    guild_id    INTEGER NOT NULL,
    command_key TEXT    NOT NULL,
    enabled     INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (guild_id, command_key)
);

-- One row per (command, role|user) that is granted access.
CREATE TABLE IF NOT EXISTS command_permissions (
    guild_id    INTEGER NOT NULL,
    command_key TEXT    NOT NULL,
    target_type TEXT    NOT NULL,  -- 'role' | 'user'
    target_id   INTEGER NOT NULL,
    PRIMARY KEY (guild_id, command_key, target_type, target_id)
);

CREATE TABLE IF NOT EXISTS warnings (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id      INTEGER NOT NULL,
    user_id       INTEGER NOT NULL,
    moderator_id  INTEGER NOT NULL,
    reason        TEXT,
    created_at    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_warnings_user ON warnings (guild_id, user_id);

CREATE TABLE IF NOT EXISTS ticket_config (
    guild_id        INTEGER PRIMARY KEY,
    category_id     INTEGER,
    support_role_id INTEGER,
    panel_channel_id INTEGER,
    ticket_counter  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS tickets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    INTEGER NOT NULL,
    channel_id  INTEGER NOT NULL,
    owner_id    INTEGER NOT NULL,
    number      INTEGER NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'open',  -- open | closed
    created_at  INTEGER NOT NULL,
    closed_at   INTEGER,
    closed_by   INTEGER,
    claimed_by  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_tickets_channel ON tickets (channel_id);

-- Unified moderation history (warns, mutes, kicks, bans, tempbans, notes...).
CREATE TABLE IF NOT EXISTS mod_cases (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id      INTEGER NOT NULL,
    case_number   INTEGER NOT NULL,
    user_id       INTEGER NOT NULL,
    moderator_id  INTEGER NOT NULL,
    action        TEXT    NOT NULL,  -- warn|mute|unmute|kick|ban|unban|tempban|note
    reason        TEXT,
    created_at    INTEGER NOT NULL,
    expires_at    INTEGER,           -- for warn expiry / tempban auto-undo
    active        INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_cases_user ON mod_cases (guild_id, user_id);
CREATE INDEX IF NOT EXISTS idx_cases_number ON mod_cases (guild_id, case_number);

-- Escalation ladder: at N active warns, apply an action.
CREATE TABLE IF NOT EXISTS warn_punishments (
    guild_id         INTEGER NOT NULL,
    threshold        INTEGER NOT NULL,
    action           TEXT    NOT NULL,  -- timeout | kick | ban
    duration_seconds INTEGER,           -- for timeout
    PRIMARY KEY (guild_id, threshold)
);

-- Ticket themes (topics) shown on the panel.
CREATE TABLE IF NOT EXISTS ticket_themes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    theme_key       TEXT    NOT NULL,
    label           TEXT    NOT NULL,
    emoji           TEXT,
    description     TEXT,
    category_id     INTEGER,
    support_role_id INTEGER,
    intro           TEXT,
    position        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_themes_guild ON ticket_themes (guild_id);

-- Self-assignable role panels (button/select roles).
CREATE TABLE IF NOT EXISTS selfrole_panels (
    message_id  INTEGER PRIMARY KEY,
    guild_id    INTEGER NOT NULL,
    channel_id  INTEGER NOT NULL,
    title       TEXT,
    max_roles   INTEGER NOT NULL DEFAULT 0  -- 0 = unlimited
);
CREATE TABLE IF NOT EXISTS selfrole_options (
    message_id  INTEGER NOT NULL,
    role_id     INTEGER NOT NULL,
    label       TEXT,
    emoji       TEXT,
    description TEXT,
    position    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (message_id, role_id)
);

-- XP / levels.
CREATE TABLE IF NOT EXISTS user_levels (
    guild_id    INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    xp          INTEGER NOT NULL DEFAULT 0,
    level       INTEGER NOT NULL DEFAULT 0,
    last_msg_ts INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_levels_guild ON user_levels (guild_id, xp DESC);

CREATE TABLE IF NOT EXISTS level_rewards (
    guild_id INTEGER NOT NULL,
    level    INTEGER NOT NULL,
    role_id  INTEGER NOT NULL,
    PRIMARY KEY (guild_id, level)
);

-- Starboard tracking (original message -> reposted message).
CREATE TABLE IF NOT EXISTS starboard_posts (
    guild_id            INTEGER NOT NULL,
    original_message_id INTEGER NOT NULL,
    board_message_id    INTEGER,
    PRIMARY KEY (guild_id, original_message_id)
);

-- Ticket system v3: claim/reviewer roles, form questions, panel info options,
-- and submitted requests awaiting a moderator to claim them.
CREATE TABLE IF NOT EXISTS ticket_roles (
    guild_id INTEGER NOT NULL,
    kind     TEXT    NOT NULL,  -- 'claim' (see requests & take) | 'reviewer' (in every ticket)
    role_id  INTEGER NOT NULL,
    PRIMARY KEY (guild_id, kind, role_id)
);

CREATE TABLE IF NOT EXISTS ticket_questions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    INTEGER NOT NULL,
    position    INTEGER NOT NULL DEFAULT 0,
    label       TEXT    NOT NULL,
    placeholder TEXT,
    required    INTEGER NOT NULL DEFAULT 1,
    paragraph   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_tq_guild ON ticket_questions (guild_id, position);

CREATE TABLE IF NOT EXISTS ticket_panel_info (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    INTEGER NOT NULL,
    position    INTEGER NOT NULL DEFAULT 0,
    label       TEXT    NOT NULL,
    description TEXT,
    answer      TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tpi_guild ON ticket_panel_info (guild_id, position);

CREATE TABLE IF NOT EXISTS ticket_requests (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id           INTEGER NOT NULL,
    number             INTEGER NOT NULL,
    requester_id       INTEGER NOT NULL,
    answers            TEXT    NOT NULL,  -- JSON [[label, value], ...]
    status             TEXT    NOT NULL DEFAULT 'pending',  -- pending|claimed|closed
    request_message_id INTEGER,
    channel_id         INTEGER,
    claimed_by         INTEGER,
    created_at         INTEGER NOT NULL,
    claimed_at         INTEGER,
    closed_at          INTEGER
);
CREATE INDEX IF NOT EXISTS idx_treq_channel ON ticket_requests (channel_id);

CREATE TABLE IF NOT EXISTS ticket_status_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id  INTEGER NOT NULL,
    guild_id    INTEGER NOT NULL,
    status      TEXT    NOT NULL,
    actor_id    INTEGER,
    note        TEXT,
    created_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tstatus_request ON ticket_status_events (request_id, created_at DESC);

CREATE TABLE IF NOT EXISTS reputation_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    INTEGER NOT NULL,
    giver_id    INTEGER NOT NULL,
    receiver_id INTEGER NOT NULL,
    reason      TEXT,
    created_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rep_receiver ON reputation_events (guild_id, receiver_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_rep_giver ON reputation_events (guild_id, giver_id, created_at DESC);

CREATE TABLE IF NOT EXISTS server_daily_stats (
    guild_id       INTEGER NOT NULL,
    stat_date      TEXT    NOT NULL,
    messages       INTEGER NOT NULL DEFAULT 0,
    commands_used  INTEGER NOT NULL DEFAULT 0,
    members_joined INTEGER NOT NULL DEFAULT 0,
    members_left   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, stat_date)
);

CREATE TABLE IF NOT EXISTS events (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id            INTEGER NOT NULL,
    author_id           INTEGER NOT NULL,
    title               TEXT    NOT NULL,
    genre               TEXT    NOT NULL,
    duration            TEXT    NOT NULL,
    description         TEXT    NOT NULL,
    image_url           TEXT,
    voice_channel_id    INTEGER NOT NULL,
    draft_channel_id    INTEGER,
    approval_message_id INTEGER,
    publish_channel_id  INTEGER,
    public_message_id   INTEGER,
    status              TEXT    NOT NULL DEFAULT 'draft',
    created_at          INTEGER NOT NULL,
    published_at        INTEGER,
    ended_at            INTEGER,
    reminded_at         INTEGER,
    feedback_sent_at    INTEGER,
    stats_sent_at       INTEGER
);
CREATE INDEX IF NOT EXISTS idx_events_status ON events (guild_id, status, published_at);
CREATE INDEX IF NOT EXISTS idx_events_author ON events (guild_id, author_id, created_at DESC);

CREATE TABLE IF NOT EXISTS event_attendance (
    event_id   INTEGER NOT NULL,
    guild_id   INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    first_seen INTEGER NOT NULL,
    last_seen  INTEGER NOT NULL,
    PRIMARY KEY (event_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_event_attendance_event ON event_attendance (event_id);

CREATE TABLE IF NOT EXISTS event_feedback (
    event_id     INTEGER NOT NULL,
    guild_id     INTEGER NOT NULL,
    user_id      INTEGER NOT NULL,
    event_rating INTEGER NOT NULL,
    host_rating  INTEGER NOT NULL,
    comment      TEXT,
    created_at   INTEGER NOT NULL,
    PRIMARY KEY (event_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_event_feedback_event ON event_feedback (event_id);

CREATE TABLE IF NOT EXISTS event_host_stat_resets (
    guild_id INTEGER NOT NULL,
    host_id  INTEGER NOT NULL,
    reset_at INTEGER NOT NULL,
    actor_id INTEGER,
    reason   TEXT,
    PRIMARY KEY (guild_id, host_id)
);

-- Admin/staff application system (recruitment).
CREATE TABLE IF NOT EXISTS app_config (
    guild_id          INTEGER PRIMARY KEY,
    review_channel_id INTEGER,
    panel_channel_id  INTEGER,
    panel_message_id  INTEGER,
    panel_title       TEXT,
    panel_text        TEXT,
    panel_image       TEXT
);

CREATE TABLE IF NOT EXISTS app_positions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    INTEGER NOT NULL,
    key         TEXT    NOT NULL,
    label       TEXT    NOT NULL,
    description TEXT,
    role_id     INTEGER,
    emoji       TEXT,
    position    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_apos_guild ON app_positions (guild_id, position);

CREATE TABLE IF NOT EXISTS app_questions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    INTEGER NOT NULL,
    position_id INTEGER NOT NULL,
    label       TEXT    NOT NULL,
    placeholder TEXT,
    required    INTEGER NOT NULL DEFAULT 1,
    paragraph   INTEGER NOT NULL DEFAULT 0,
    position    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_aq_pos ON app_questions (position_id, position);

CREATE TABLE IF NOT EXISTS applications (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id      INTEGER NOT NULL,
    position_id   INTEGER NOT NULL,
    position_label TEXT   NOT NULL,
    applicant_id  INTEGER NOT NULL,
    answers       TEXT    NOT NULL,
    status        TEXT    NOT NULL DEFAULT 'pending',  -- pending|approved|denied
    message_id    INTEGER,
    reviewer_id   INTEGER,
    reason        TEXT,
    created_at    INTEGER NOT NULL,
    decided_at    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_app_user ON applications (guild_id, applicant_id);

CREATE TABLE IF NOT EXISTS app_roles (
    guild_id INTEGER NOT NULL,
    role_id  INTEGER NOT NULL,
    PRIMARY KEY (guild_id, role_id)
);

-- Per-user rank-card customization (global across guilds).
CREATE TABLE IF NOT EXISTS rank_prefs (
    user_id INTEGER PRIMARY KEY,
    accent  TEXT,
    bg      TEXT
);

-- Giveaways and their entries.
CREATE TABLE IF NOT EXISTS giveaways (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id   INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    message_id INTEGER,
    prize      TEXT    NOT NULL,
    winners    INTEGER NOT NULL DEFAULT 1,
    host_id    INTEGER NOT NULL,
    ends_at    INTEGER NOT NULL,
    ended      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_gw_due ON giveaways (ended, ends_at);

CREATE TABLE IF NOT EXISTS giveaway_entries (
    message_id INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    PRIMARY KEY (message_id, user_id)
);

-- Roles granted access to an entire command category (e.g. moderation roles).
CREATE TABLE IF NOT EXISTS category_roles (
    guild_id     INTEGER NOT NULL,
    category_key TEXT    NOT NULL,
    role_id      INTEGER NOT NULL,
    PRIMARY KEY (guild_id, category_key, role_id)
);

-- Economy: per-guild settings.
CREATE TABLE IF NOT EXISTS economy_config (
    guild_id       INTEGER PRIMARY KEY,
    enabled        INTEGER NOT NULL DEFAULT 1,
    symbol         TEXT    NOT NULL DEFAULT '🪙',
    currency_name  TEXT    NOT NULL DEFAULT 'монеты',
    start_balance  INTEGER NOT NULL DEFAULT 0,
    timely_amount  INTEGER NOT NULL DEFAULT 250,
    timely_cooldown INTEGER NOT NULL DEFAULT 43200,
    daily_amount   INTEGER NOT NULL DEFAULT 250,
    daily_cooldown INTEGER NOT NULL DEFAULT 86400,
    work_min       INTEGER NOT NULL DEFAULT 50,
    work_max       INTEGER NOT NULL DEFAULT 250,
    work_cooldown  INTEGER NOT NULL DEFAULT 3600,
    money_multiplier REAL  NOT NULL DEFAULT 1,
    commission_pct INTEGER NOT NULL DEFAULT 0,
    rob_cooldown   INTEGER NOT NULL DEFAULT 86400,
    rob_success    INTEGER NOT NULL DEFAULT 40,
    rob_max_pct    INTEGER NOT NULL DEFAULT 30,
    bet_min        INTEGER NOT NULL DEFAULT 150,
    bet_max        INTEGER NOT NULL DEFAULT 100000,
    shop_owner_id  INTEGER,
    shop_log_channel_id INTEGER,
    grant_log_channel_id INTEGER,
    grant_cooldown INTEGER NOT NULL DEFAULT 0,
    grant_daily_limit INTEGER NOT NULL DEFAULT 0
);

-- Economy: users/roles allowed to grant currency without full admin access.
CREATE TABLE IF NOT EXISTS economy_managers (
    guild_id    INTEGER NOT NULL,
    target_type TEXT    NOT NULL, -- user | role
    target_id   INTEGER NOT NULL,
    PRIMARY KEY (guild_id, target_type, target_id)
);

-- Economy: per-user wallet/bank and cooldown timestamps.
CREATE TABLE IF NOT EXISTS economy_users (
    guild_id   INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    wallet     INTEGER NOT NULL DEFAULT 0,
    bank       INTEGER NOT NULL DEFAULT 0,
    shop_wallet INTEGER NOT NULL DEFAULT 0,
    last_timely INTEGER NOT NULL DEFAULT 0,
    last_daily INTEGER NOT NULL DEFAULT 0,
    last_work  INTEGER NOT NULL DEFAULT 0,
    last_rob   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_econ_top ON economy_users (guild_id, wallet DESC, bank DESC);

-- Economy: shop items that grant roles (permanent or temporary).
CREATE TABLE IF NOT EXISTS shop_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    INTEGER NOT NULL,
    name        TEXT    NOT NULL,
    description TEXT,
    price       INTEGER NOT NULL,
    role_id     INTEGER NOT NULL,
    duration    INTEGER NOT NULL DEFAULT 0,  -- seconds; 0 = permanent
    item_type   TEXT    NOT NULL DEFAULT 'role', -- role | time_role | manual | inventory
    purchase_limit INTEGER NOT NULL DEFAULT 0,  -- 0 = unlimited
    response_message TEXT,
    income_amount INTEGER NOT NULL DEFAULT 0,
    income_cooldown INTEGER NOT NULL DEFAULT 0,
    category    TEXT    NOT NULL DEFAULT 'roles',
    stock       INTEGER NOT NULL DEFAULT 0, -- 0 = unlimited
    active      INTEGER NOT NULL DEFAULT 1,
    delivery_type TEXT NOT NULL DEFAULT 'role', -- role | time_role | manual | inventory
    catalog_key TEXT
);
CREATE INDEX IF NOT EXISTS idx_shop_guild ON shop_items (guild_id);

-- Economy: purchases are used for shop limits and temporary-role expiry.
CREATE TABLE IF NOT EXISTS shop_purchases (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id     INTEGER NOT NULL,
    user_id      INTEGER NOT NULL,
    item_id      INTEGER NOT NULL,
    role_id      INTEGER NOT NULL,
    item_type    TEXT    NOT NULL,
    purchased_at INTEGER NOT NULL,
    expires_at   INTEGER,
    last_income_at INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_shop_purchases_user ON shop_purchases (guild_id, user_id, item_id);
CREATE INDEX IF NOT EXISTS idx_shop_purchases_income ON shop_purchases (item_type, last_income_at);

-- Economy: bought non-role items kept in a user's inventory.
CREATE TABLE IF NOT EXISTS shop_inventory (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id     INTEGER NOT NULL,
    user_id      INTEGER NOT NULL,
    item_id      INTEGER NOT NULL,
    item_type    TEXT    NOT NULL,
    name         TEXT    NOT NULL,
    metadata     TEXT,
    purchased_at INTEGER NOT NULL,
    used_at      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_shop_inventory_user ON shop_inventory (guild_id, user_id, used_at);

-- Economy: manual-delivery purchases awaiting staff action.
CREATE TABLE IF NOT EXISTS shop_requests (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id     INTEGER NOT NULL,
    user_id      INTEGER NOT NULL,
    item_id      INTEGER NOT NULL,
    item_name    TEXT    NOT NULL,
    price        INTEGER NOT NULL DEFAULT 0,
    details      TEXT,
    status       TEXT    NOT NULL DEFAULT 'pending', -- pending | done | cancelled
    created_at   INTEGER NOT NULL,
    claimed_by   INTEGER,
    closed_at    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_shop_requests_guild ON shop_requests (guild_id, status, created_at DESC);

-- Economy: temporary roles awaiting auto-removal.
CREATE TABLE IF NOT EXISTS temp_roles (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id   INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    role_id    INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);

-- Economy: Akemi-like transaction history.
CREATE TABLE IF NOT EXISTS economy_transactions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    INTEGER NOT NULL,
    operation   TEXT    NOT NULL, -- increment | decrement | transfer | role_listing_created
    command     TEXT,
    amount      INTEGER NOT NULL,
    sender_id   INTEGER,
    receiver_id INTEGER,
    note        TEXT,
    voided      INTEGER NOT NULL DEFAULT 0,
    created_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_econ_tx_guild ON economy_transactions (guild_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_econ_tx_user ON economy_transactions (guild_id, sender_id, receiver_id);
"""

# Columns added to existing tables after their first release. Each entry is
# (table, column, definition); applied if missing.
_MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    ("tickets", "claimed_by", "INTEGER"),
    ("tickets", "theme_key", "TEXT"),
    ("guild_config", "case_counter", "INTEGER NOT NULL DEFAULT 0"),
    ("guild_config", "warn_expiry_days", "INTEGER NOT NULL DEFAULT 0"),
    ("guild_config", "levels_enabled", "INTEGER NOT NULL DEFAULT 0"),
    ("guild_config", "levelup_channel_id", "INTEGER"),
    ("guild_config", "welcome_channel_id", "INTEGER"),
    ("guild_config", "welcome_message", "TEXT"),
    ("guild_config", "goodbye_channel_id", "INTEGER"),
    ("guild_config", "goodbye_message", "TEXT"),
    ("guild_config", "autorole_id", "INTEGER"),
    ("guild_config", "starboard_channel_id", "INTEGER"),
    ("guild_config", "starboard_emoji", "TEXT NOT NULL DEFAULT '⭐'"),
    ("guild_config", "starboard_threshold", "INTEGER NOT NULL DEFAULT 3"),
    ("guild_config", "adminlog_channel_id", "INTEGER"),
    ("ticket_config", "cooldown_seconds", "INTEGER NOT NULL DEFAULT 0"),
    ("ticket_config", "requests_channel_id", "INTEGER"),
    ("ticket_config", "tlog_channel_id", "INTEGER"),
    ("ticket_config", "transcript_channel_id", "INTEGER"),
    ("ticket_config", "panel_message_id", "INTEGER"),
    ("ticket_config", "panel_text", "TEXT"),
    ("ticket_requests", "closed_by", "INTEGER"),
    ("ticket_requests", "close_reason", "TEXT"),
    ("ticket_requests", "reopened_by", "INTEGER"),
    ("ticket_requests", "reopened_at", "INTEGER"),
    ("ticket_requests", "rating", "INTEGER"),
    ("ticket_requests", "rating_comment", "TEXT"),
    ("ticket_requests", "rated_at", "INTEGER"),
    ("economy_users", "last_weekly", "INTEGER NOT NULL DEFAULT 0"),
    ("economy_users", "last_monthly", "INTEGER NOT NULL DEFAULT 0"),
    ("economy_users", "last_timely", "INTEGER NOT NULL DEFAULT 0"),
    ("economy_users", "shop_wallet", "INTEGER NOT NULL DEFAULT 0"),
    # Safe / jail / steal (reference shop mechanics).
    ("economy_users", "safe_balance", "INTEGER NOT NULL DEFAULT 0"),
    ("economy_users", "jail_until", "INTEGER NOT NULL DEFAULT 0"),
    ("economy_users", "jail_start", "INTEGER NOT NULL DEFAULT 0"),
    ("economy_users", "jail_type", "TEXT"),
    ("economy_users", "jail_count", "INTEGER NOT NULL DEFAULT 0"),
    ("economy_users", "total_jail_time", "INTEGER NOT NULL DEFAULT 0"),
    ("economy_users", "steal_success", "INTEGER NOT NULL DEFAULT 0"),
    ("economy_users", "steal_fail", "INTEGER NOT NULL DEFAULT 0"),
    ("economy_users", "last_steal", "INTEGER NOT NULL DEFAULT 0"),
    ("economy_users", "last_grant", "INTEGER NOT NULL DEFAULT 0"),
    ("economy_users", "steal_boost_until", "INTEGER NOT NULL DEFAULT 0"),
    ("economy_users", "steal_insurance_until", "INTEGER NOT NULL DEFAULT 0"),
    ("economy_users", "active_banner", "TEXT"),
    ("economy_config", "enabled", "INTEGER NOT NULL DEFAULT 1"),
    ("economy_config", "timely_amount", "INTEGER NOT NULL DEFAULT 250"),
    ("economy_config", "timely_cooldown", "INTEGER NOT NULL DEFAULT 43200"),
    ("economy_config", "weekly_amount", "INTEGER NOT NULL DEFAULT 1000"),
    ("economy_config", "weekly_cooldown", "INTEGER NOT NULL DEFAULT 604800"),
    ("economy_config", "monthly_amount", "INTEGER NOT NULL DEFAULT 5000"),
    ("economy_config", "monthly_cooldown", "INTEGER NOT NULL DEFAULT 2592000"),
    ("economy_config", "money_multiplier", "REAL NOT NULL DEFAULT 1"),
    ("economy_config", "commission_pct", "INTEGER NOT NULL DEFAULT 0"),
    ("economy_config", "shop_owner_id", "INTEGER"),
    ("economy_config", "shop_log_channel_id", "INTEGER"),
    ("economy_config", "grant_log_channel_id", "INTEGER"),
    ("economy_config", "grant_cooldown", "INTEGER NOT NULL DEFAULT 0"),
    ("economy_config", "grant_daily_limit", "INTEGER NOT NULL DEFAULT 0"),
    # Settings replicated from the reference shop (jail / steal).
    ("economy_config", "jail_min_minutes", "INTEGER NOT NULL DEFAULT 30"),
    ("economy_config", "jail_max_minutes", "INTEGER NOT NULL DEFAULT 180"),
    ("economy_config", "steal_chance", "INTEGER NOT NULL DEFAULT 50"),
    ("economy_config", "steal_cooldown", "INTEGER NOT NULL DEFAULT 10800"),
    ("economy_config", "steal_min_amount", "INTEGER NOT NULL DEFAULT 500"),
    ("economy_config", "grant_protect_hours", "INTEGER NOT NULL DEFAULT 6"),
    ("shop_items", "item_type", "TEXT NOT NULL DEFAULT 'role'"),
    ("shop_items", "purchase_limit", "INTEGER NOT NULL DEFAULT 0"),
    ("shop_items", "response_message", "TEXT"),
    ("shop_items", "income_amount", "INTEGER NOT NULL DEFAULT 0"),
    ("shop_items", "income_cooldown", "INTEGER NOT NULL DEFAULT 0"),
    ("shop_items", "category", "TEXT NOT NULL DEFAULT 'roles'"),
    ("shop_items", "stock", "INTEGER NOT NULL DEFAULT 0"),
    ("shop_items", "active", "INTEGER NOT NULL DEFAULT 1"),
    ("shop_items", "delivery_type", "TEXT NOT NULL DEFAULT 'role'"),
    ("shop_items", "catalog_key", "TEXT"),
    ("shop_requests", "price", "INTEGER NOT NULL DEFAULT 0"),
    ("guild_config", "adminlog_channel_id", "INTEGER"),
)


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._conn: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path, timeout=30)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL;")
        await self._conn.execute("PRAGMA busy_timeout=30000;")
        await self._conn.execute("PRAGMA synchronous=NORMAL;")
        await self._conn.execute("PRAGMA foreign_keys=ON;")
        await self._conn.executescript(SCHEMA)
        await self._migrate()
        await self._conn.commit()

    async def _migrate(self) -> None:
        """Lightweight additive migrations for databases created by older versions."""
        table_cols: dict[str, set[str]] = {}
        for table, column, definition in _MIGRATIONS:
            if table not in table_cols:
                async with self._conn.execute(f"PRAGMA table_info({table})") as cur:
                    table_cols[table] = {row["name"] for row in await cur.fetchall()}
            if column not in table_cols[table]:
                await self._conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
                )
                table_cols[table].add(column)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database is not connected")
        return self._conn

    # ---- guild config ---------------------------------------------------

    async def get_guild_config(self, guild_id: int) -> aiosqlite.Row:
        async with self.conn.execute(
            "SELECT * FROM guild_config WHERE guild_id = ?", (guild_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            await self.conn.execute(
                "INSERT INTO guild_config (guild_id) VALUES (?)", (guild_id,)
            )
            await self.conn.commit()
            async with self.conn.execute(
                "SELECT * FROM guild_config WHERE guild_id = ?", (guild_id,)
            ) as cur:
                row = await cur.fetchone()
        return row

    async def update_guild_config(self, guild_id: int, **fields) -> None:
        if not fields:
            return
        await self.get_guild_config(guild_id)  # ensure row exists
        columns = ", ".join(f"{k} = ?" for k in fields)
        await self.conn.execute(
            f"UPDATE guild_config SET {columns} WHERE guild_id = ?",
            (*fields.values(), guild_id),
        )
        await self.conn.commit()

    # ---- command settings / permissions ---------------------------------

    async def is_command_enabled(self, guild_id: int, command_key: str, default: bool) -> bool:
        async with self.conn.execute(
            "SELECT enabled FROM command_settings WHERE guild_id = ? AND command_key = ?",
            (guild_id, command_key),
        ) as cur:
            row = await cur.fetchone()
        return bool(row["enabled"]) if row is not None else default

    async def set_command_enabled(self, guild_id: int, command_key: str, enabled: bool) -> None:
        await self.conn.execute(
            """
            INSERT INTO command_settings (guild_id, command_key, enabled)
            VALUES (?, ?, ?)
            ON CONFLICT(guild_id, command_key) DO UPDATE SET enabled = excluded.enabled
            """,
            (guild_id, command_key, int(enabled)),
        )
        await self.conn.commit()

    async def get_permission_targets(
        self, guild_id: int, command_key: str
    ) -> tuple[list[int], list[int]]:
        """Return (role_ids, user_ids) granted access to a command."""
        async with self.conn.execute(
            "SELECT target_type, target_id FROM command_permissions "
            "WHERE guild_id = ? AND command_key = ?",
            (guild_id, command_key),
        ) as cur:
            rows = await cur.fetchall()
        roles = [r["target_id"] for r in rows if r["target_type"] == "role"]
        users = [r["target_id"] for r in rows if r["target_type"] == "user"]
        return roles, users

    async def set_permission_targets(
        self,
        guild_id: int,
        command_key: str,
        target_type: str,
        target_ids: Iterable[int],
    ) -> None:
        """Replace the full set of role|user grants for a command."""
        await self.conn.execute(
            "DELETE FROM command_permissions "
            "WHERE guild_id = ? AND command_key = ? AND target_type = ?",
            (guild_id, command_key, target_type),
        )
        await self.conn.executemany(
            "INSERT OR IGNORE INTO command_permissions "
            "(guild_id, command_key, target_type, target_id) VALUES (?, ?, ?, ?)",
            [(guild_id, command_key, target_type, tid) for tid in target_ids],
        )
        await self.conn.commit()

    # ---- warnings -------------------------------------------------------

    async def add_warning(
        self, guild_id: int, user_id: int, moderator_id: int, reason: str
    ) -> int:
        cur = await self.conn.execute(
            "INSERT INTO warnings (guild_id, user_id, moderator_id, reason, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (guild_id, user_id, moderator_id, reason, int(time.time())),
        )
        await self.conn.commit()
        return cur.lastrowid

    async def get_warnings(self, guild_id: int, user_id: int) -> list[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM warnings WHERE guild_id = ? AND user_id = ? ORDER BY created_at DESC",
            (guild_id, user_id),
        ) as cur:
            return list(await cur.fetchall())

    async def clear_warnings(self, guild_id: int, user_id: int) -> int:
        cur = await self.conn.execute(
            "DELETE FROM warnings WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        await self.conn.commit()
        return cur.rowcount

    # ---- tickets --------------------------------------------------------

    async def get_ticket_config(self, guild_id: int) -> aiosqlite.Row:
        async with self.conn.execute(
            "SELECT * FROM ticket_config WHERE guild_id = ?", (guild_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            await self.conn.execute(
                "INSERT INTO ticket_config (guild_id) VALUES (?)", (guild_id,)
            )
            await self.conn.commit()
            async with self.conn.execute(
                "SELECT * FROM ticket_config WHERE guild_id = ?", (guild_id,)
            ) as cur:
                row = await cur.fetchone()
        return row

    async def update_ticket_config(self, guild_id: int, **fields) -> None:
        if not fields:
            return
        await self.get_ticket_config(guild_id)
        columns = ", ".join(f"{k} = ?" for k in fields)
        await self.conn.execute(
            f"UPDATE ticket_config SET {columns} WHERE guild_id = ?",
            (*fields.values(), guild_id),
        )
        await self.conn.commit()

    async def next_ticket_number(self, guild_id: int) -> int:
        await self.get_ticket_config(guild_id)
        await self.conn.execute(
            "UPDATE ticket_config SET ticket_counter = ticket_counter + 1 WHERE guild_id = ?",
            (guild_id,),
        )
        await self.conn.commit()
        async with self.conn.execute(
            "SELECT ticket_counter FROM ticket_config WHERE guild_id = ?", (guild_id,)
        ) as cur:
            row = await cur.fetchone()
        return int(row["ticket_counter"])

    async def create_ticket(
        self,
        guild_id: int,
        channel_id: int,
        owner_id: int,
        number: int,
        theme_key: Optional[str] = None,
    ) -> int:
        cur = await self.conn.execute(
            "INSERT INTO tickets (guild_id, channel_id, owner_id, number, created_at, theme_key) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (guild_id, channel_id, owner_id, number, int(time.time()), theme_key),
        )
        await self.conn.commit()
        return cur.lastrowid

    async def last_ticket_for(self, guild_id: int, owner_id: int) -> Optional[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM tickets WHERE guild_id = ? AND owner_id = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (guild_id, owner_id),
        ) as cur:
            return await cur.fetchone()

    async def get_ticket_by_channel(self, channel_id: int) -> Optional[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM tickets WHERE channel_id = ?", (channel_id,)
        ) as cur:
            return await cur.fetchone()

    async def has_open_ticket(self, guild_id: int, owner_id: int) -> Optional[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM tickets WHERE guild_id = ? AND owner_id = ? AND status = 'open'",
            (guild_id, owner_id),
        ) as cur:
            return await cur.fetchone()

    async def close_ticket(self, channel_id: int, closed_by: int) -> None:
        await self.conn.execute(
            "UPDATE tickets SET status = 'closed', closed_at = ?, closed_by = ? "
            "WHERE channel_id = ?",
            (int(time.time()), closed_by, channel_id),
        )
        await self.conn.commit()

    async def claim_ticket(self, channel_id: int, claimed_by: int) -> None:
        await self.conn.execute(
            "UPDATE tickets SET claimed_by = ? WHERE channel_id = ?",
            (claimed_by, channel_id),
        )
        await self.conn.commit()

    async def reopen_ticket(self, channel_id: int) -> None:
        await self.conn.execute(
            "UPDATE tickets SET status = 'open', closed_at = NULL, closed_by = NULL "
            "WHERE channel_id = ?",
            (channel_id,),
        )
        await self.conn.commit()

    # ---- ticket themes --------------------------------------------------

    async def get_ticket_themes(self, guild_id: int) -> list[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM ticket_themes WHERE guild_id = ? ORDER BY position, id",
            (guild_id,),
        ) as cur:
            return list(await cur.fetchall())

    async def get_ticket_theme(self, guild_id: int, theme_key: str) -> Optional[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM ticket_themes WHERE guild_id = ? AND theme_key = ?",
            (guild_id, theme_key),
        ) as cur:
            return await cur.fetchone()

    async def add_ticket_theme(
        self,
        guild_id: int,
        theme_key: str,
        label: str,
        emoji: Optional[str],
        description: Optional[str],
        category_id: Optional[int],
        support_role_id: Optional[int],
        intro: Optional[str],
    ) -> None:
        await self.conn.execute(
            "INSERT INTO ticket_themes "
            "(guild_id, theme_key, label, emoji, description, category_id, support_role_id, intro, position) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, (SELECT COALESCE(MAX(position)+1,0) FROM ticket_themes WHERE guild_id = ?))",
            (guild_id, theme_key, label, emoji, description, category_id, support_role_id, intro, guild_id),
        )
        await self.conn.commit()

    async def remove_ticket_theme(self, guild_id: int, theme_key: str) -> int:
        cur = await self.conn.execute(
            "DELETE FROM ticket_themes WHERE guild_id = ? AND theme_key = ?",
            (guild_id, theme_key),
        )
        await self.conn.commit()
        return cur.rowcount

    # ---- ticket roles / questions / info / requests (v3) ---------------

    async def get_ticket_roles(self, guild_id: int, kind: str) -> list[int]:
        async with self.conn.execute(
            "SELECT role_id FROM ticket_roles WHERE guild_id = ? AND kind = ?",
            (guild_id, kind),
        ) as cur:
            return [r["role_id"] for r in await cur.fetchall()]

    async def set_ticket_roles(self, guild_id: int, kind: str, role_ids) -> None:
        await self.conn.execute(
            "DELETE FROM ticket_roles WHERE guild_id = ? AND kind = ?", (guild_id, kind)
        )
        await self.conn.executemany(
            "INSERT OR IGNORE INTO ticket_roles (guild_id, kind, role_id) VALUES (?, ?, ?)",
            [(guild_id, kind, rid) for rid in role_ids],
        )
        await self.conn.commit()

    async def get_ticket_questions(self, guild_id: int) -> list[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM ticket_questions WHERE guild_id = ? ORDER BY position, id",
            (guild_id,),
        ) as cur:
            return list(await cur.fetchall())

    async def add_ticket_question(
        self, guild_id: int, label: str, placeholder, required: int, paragraph: int
    ) -> None:
        await self.conn.execute(
            "INSERT INTO ticket_questions (guild_id, position, label, placeholder, required, paragraph) "
            "VALUES (?, (SELECT COALESCE(MAX(position)+1,0) FROM ticket_questions WHERE guild_id=?), ?, ?, ?, ?)",
            (guild_id, guild_id, label, placeholder, required, paragraph),
        )
        await self.conn.commit()

    async def clear_ticket_questions(self, guild_id: int) -> None:
        await self.conn.execute("DELETE FROM ticket_questions WHERE guild_id = ?", (guild_id,))
        await self.conn.commit()

    async def get_panel_info(self, guild_id: int) -> list[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM ticket_panel_info WHERE guild_id = ? ORDER BY position, id",
            (guild_id,),
        ) as cur:
            return list(await cur.fetchall())

    async def get_panel_info_one(self, info_id: int) -> Optional[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM ticket_panel_info WHERE id = ?", (info_id,)
        ) as cur:
            return await cur.fetchone()

    async def add_panel_info(self, guild_id: int, label: str, description, answer: str) -> None:
        await self.conn.execute(
            "INSERT INTO ticket_panel_info (guild_id, position, label, description, answer) "
            "VALUES (?, (SELECT COALESCE(MAX(position)+1,0) FROM ticket_panel_info WHERE guild_id=?), ?, ?, ?)",
            (guild_id, guild_id, label, description, answer),
        )
        await self.conn.commit()

    async def clear_panel_info(self, guild_id: int) -> None:
        await self.conn.execute("DELETE FROM ticket_panel_info WHERE guild_id = ?", (guild_id,))
        await self.conn.commit()

    async def create_request(
        self, guild_id: int, number: int, requester_id: int, answers: str
    ) -> int:
        cur = await self.conn.execute(
            "INSERT INTO ticket_requests (guild_id, number, requester_id, answers, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (guild_id, number, requester_id, answers, int(time.time())),
        )
        await self.conn.commit()
        return cur.lastrowid

    async def get_request(self, request_id: int) -> Optional[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM ticket_requests WHERE id = ?", (request_id,)
        ) as cur:
            return await cur.fetchone()

    async def get_request_by_channel(self, channel_id: int) -> Optional[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM ticket_requests WHERE channel_id = ?", (channel_id,)
        ) as cur:
            return await cur.fetchone()

    async def open_request_for(self, guild_id: int, requester_id: int) -> Optional[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM ticket_requests WHERE guild_id = ? AND requester_id = ? "
            "AND status IN ('pending','claimed','open','waiting_user','waiting_staff','resolved') "
            "ORDER BY id DESC LIMIT 1",
            (guild_id, requester_id),
        ) as cur:
            return await cur.fetchone()

    async def update_request(self, request_id: int, **fields) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        await self.conn.execute(
            f"UPDATE ticket_requests SET {cols} WHERE id = ?", (*fields.values(), request_id)
        )
        await self.conn.commit()

    async def add_ticket_status_event(
        self,
        request_id: int,
        guild_id: int,
        status: str,
        *,
        actor_id: Optional[int] = None,
        note: Optional[str] = None,
    ) -> int:
        cur = await self.conn.execute(
            "INSERT INTO ticket_status_events "
            "(request_id, guild_id, status, actor_id, note, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (request_id, guild_id, status, actor_id, note, int(time.time())),
        )
        await self.conn.commit()
        return cur.lastrowid

    async def set_ticket_status(
        self,
        request_id: int,
        guild_id: int,
        status: str,
        *,
        actor_id: Optional[int] = None,
        note: Optional[str] = None,
    ) -> None:
        await self.update_request(request_id, status=status)
        await self.add_ticket_status_event(
            request_id, guild_id, status, actor_id=actor_id, note=note
        )

    async def rate_ticket(
        self,
        request_id: int,
        rating: int,
        comment: Optional[str],
    ) -> None:
        await self.conn.execute(
            "UPDATE ticket_requests SET rating = ?, rating_comment = ?, rated_at = ? WHERE id = ?",
            (rating, comment, int(time.time()), request_id),
        )
        await self.conn.commit()

    async def ticket_status_counts(self, guild_id: int) -> dict[str, int]:
        async with self.conn.execute(
            "SELECT status, COUNT(*) AS c FROM ticket_requests WHERE guild_id = ? GROUP BY status",
            (guild_id,),
        ) as cur:
            return {row["status"]: int(row["c"]) for row in await cur.fetchall()}

    async def ticket_rating_summary(self, guild_id: int) -> aiosqlite.Row:
        async with self.conn.execute(
            "SELECT COUNT(rating) AS count, AVG(rating) AS avg_rating "
            "FROM ticket_requests WHERE guild_id = ? AND rating IS NOT NULL",
            (guild_id,),
        ) as cur:
            return await cur.fetchone()

    async def ticket_staff_stats(
        self, guild_id: int, limit: int = 5
    ) -> list[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT claimed_by, COUNT(*) AS closed_count, AVG(rating) AS avg_rating "
            "FROM ticket_requests WHERE guild_id = ? AND claimed_by IS NOT NULL "
            "AND status = 'closed' GROUP BY claimed_by "
            "ORDER BY closed_count DESC, avg_rating DESC LIMIT ?",
            (guild_id, limit),
        ) as cur:
            return list(await cur.fetchall())

    # ---- reputation -----------------------------------------------------

    async def latest_reputation_from(
        self, guild_id: int, giver_id: int
    ) -> Optional[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM reputation_events WHERE guild_id = ? AND giver_id = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (guild_id, giver_id),
        ) as cur:
            return await cur.fetchone()

    async def add_reputation(
        self, guild_id: int, giver_id: int, receiver_id: int, reason: Optional[str]
    ) -> int:
        cur = await self.conn.execute(
            "INSERT INTO reputation_events (guild_id, giver_id, receiver_id, reason, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (guild_id, giver_id, receiver_id, reason, int(time.time())),
        )
        await self.conn.commit()
        return cur.lastrowid

    async def reputation_score(self, guild_id: int, user_id: int) -> int:
        async with self.conn.execute(
            "SELECT COUNT(*) AS c FROM reputation_events WHERE guild_id = ? AND receiver_id = ?",
            (guild_id, user_id),
        ) as cur:
            row = await cur.fetchone()
        return int(row["c"]) if row is not None else 0

    async def reputation_given_count(self, guild_id: int, user_id: int) -> int:
        async with self.conn.execute(
            "SELECT COUNT(*) AS c FROM reputation_events WHERE guild_id = ? AND giver_id = ?",
            (guild_id, user_id),
        ) as cur:
            row = await cur.fetchone()
        return int(row["c"]) if row is not None else 0

    async def reputation_top(
        self, guild_id: int, limit: int = 10
    ) -> list[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT receiver_id, COUNT(*) AS score FROM reputation_events "
            "WHERE guild_id = ? GROUP BY receiver_id ORDER BY score DESC, receiver_id LIMIT ?",
            (guild_id, limit),
        ) as cur:
            return list(await cur.fetchall())

    async def reputation_recent(
        self, guild_id: int, user_id: int, limit: int = 5
    ) -> list[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM reputation_events WHERE guild_id = ? AND receiver_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (guild_id, user_id, limit),
        ) as cur:
            return list(await cur.fetchall())

    # ---- server analytics ----------------------------------------------

    async def increment_server_stat(
        self, guild_id: int, stat_date: str, field: str, delta: int = 1
    ) -> None:
        allowed = {"messages", "commands_used", "members_joined", "members_left"}
        if field not in allowed:
            raise ValueError(f"Unknown stat field: {field}")
        for attempt in range(3):
            try:
                await self.conn.execute(
                    "INSERT INTO server_daily_stats (guild_id, stat_date, messages, commands_used, members_joined, members_left) "
                    "VALUES (?, ?, 0, 0, 0, 0) ON CONFLICT(guild_id, stat_date) DO NOTHING",
                    (guild_id, stat_date),
                )
                await self.conn.execute(
                    f"UPDATE server_daily_stats SET {field} = {field} + ? WHERE guild_id = ? AND stat_date = ?",
                    (delta, guild_id, stat_date),
                )
                await self.conn.commit()
                return
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt == 2:
                    raise
                await asyncio.sleep(0.25 * (attempt + 1))

    async def server_stats_recent(
        self, guild_id: int, days: int = 7
    ) -> list[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM server_daily_stats WHERE guild_id = ? "
            "ORDER BY stat_date DESC LIMIT ?",
            (guild_id, days),
        ) as cur:
            return list(await cur.fetchall())

    async def server_stats_totals(self, guild_id: int, days: int = 7) -> aiosqlite.Row:
        async with self.conn.execute(
            "SELECT COALESCE(SUM(messages), 0) AS messages, "
            "COALESCE(SUM(commands_used), 0) AS commands_used, "
            "COALESCE(SUM(members_joined), 0) AS members_joined, "
            "COALESCE(SUM(members_left), 0) AS members_left "
            "FROM (SELECT * FROM server_daily_stats WHERE guild_id = ? "
            "ORDER BY stat_date DESC LIMIT ?)",
            (guild_id, days),
        ) as cur:
            return await cur.fetchone()

    # ---- events ---------------------------------------------------------

    async def create_event(
        self,
        guild_id: int,
        author_id: int,
        title: str,
        genre: str,
        duration: str,
        description: str,
        image_url: Optional[str],
        voice_channel_id: int,
        draft_channel_id: Optional[int],
    ) -> int:
        cur = await self.conn.execute(
            "INSERT INTO events "
            "(guild_id, author_id, title, genre, duration, description, image_url, "
            "voice_channel_id, draft_channel_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                guild_id,
                author_id,
                title,
                genre,
                duration,
                description,
                image_url,
                voice_channel_id,
                draft_channel_id,
                int(time.time()),
            ),
        )
        await self.conn.commit()
        return cur.lastrowid

    async def get_event(self, event_id: int) -> Optional[aiosqlite.Row]:
        async with self.conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)) as cur:
            return await cur.fetchone()

    async def get_event_by_approval_message(
        self, message_id: int
    ) -> Optional[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM events WHERE approval_message_id = ?", (message_id,)
        ) as cur:
            return await cur.fetchone()

    async def update_event(self, event_id: int, **fields) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        await self.conn.execute(
            f"UPDATE events SET {cols} WHERE id = ?", (*fields.values(), event_id)
        )
        await self.conn.commit()

    async def publish_event(
        self,
        event_id: int,
        publish_channel_id: int,
        public_message_id: int,
    ) -> None:
        await self.update_event(
            event_id,
            status="published",
            publish_channel_id=publish_channel_id,
            public_message_id=public_message_id,
            published_at=int(time.time()),
        )

    async def active_events_for_voice(
        self, guild_id: int, voice_channel_id: int
    ) -> list[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM events WHERE guild_id = ? AND voice_channel_id = ? "
            "AND status = 'published'",
            (guild_id, voice_channel_id),
        ) as cur:
            return list(await cur.fetchall())

    async def active_event_for_author(
        self, guild_id: int, author_id: int
    ) -> Optional[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM events WHERE guild_id = ? AND author_id = ? "
            "AND status = 'published' ORDER BY published_at DESC LIMIT 1",
            (guild_id, author_id),
        ) as cur:
            return await cur.fetchone()

    async def active_event_for_guild(self, guild_id: int) -> Optional[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM events WHERE guild_id = ? AND status = 'published' "
            "ORDER BY published_at DESC LIMIT 1",
            (guild_id,),
        ) as cur:
            return await cur.fetchone()

    async def latest_event_for_author(
        self, guild_id: int, author_id: int
    ) -> Optional[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM events WHERE guild_id = ? AND author_id = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (guild_id, author_id),
        ) as cur:
            return await cur.fetchone()

    async def latest_event_for_guild(self, guild_id: int) -> Optional[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM events WHERE guild_id = ? ORDER BY created_at DESC LIMIT 1",
            (guild_id,),
        ) as cur:
            return await cur.fetchone()

    async def events_needing_end_reminder(
        self, age_seconds: int
    ) -> list[aiosqlite.Row]:
        now = int(time.time())
        async with self.conn.execute(
            "SELECT * FROM events WHERE status = 'published' AND published_at IS NOT NULL "
            "AND reminded_at IS NULL AND published_at + ? <= ?",
            (age_seconds, now),
        ) as cur:
            return list(await cur.fetchall())

    async def events_needing_stats(self, age_seconds: int) -> list[aiosqlite.Row]:
        now = int(time.time())
        async with self.conn.execute(
            "SELECT * FROM events WHERE status = 'ended' AND ended_at IS NOT NULL "
            "AND stats_sent_at IS NULL AND ended_at + ? <= ?",
            (age_seconds, now),
        ) as cur:
            return list(await cur.fetchall())

    async def mark_event_attendance(
        self, event_id: int, guild_id: int, user_id: int
    ) -> None:
        now = int(time.time())
        await self.conn.execute(
            "INSERT INTO event_attendance (event_id, guild_id, user_id, first_seen, last_seen) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(event_id, user_id) DO UPDATE SET last_seen = excluded.last_seen",
            (event_id, guild_id, user_id, now, now),
        )
        await self.conn.commit()

    async def event_attendees(self, event_id: int) -> list[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM event_attendance WHERE event_id = ? ORDER BY first_seen",
            (event_id,),
        ) as cur:
            return list(await cur.fetchall())

    async def upsert_event_feedback(
        self,
        event_id: int,
        guild_id: int,
        user_id: int,
        event_rating: int,
        host_rating: int,
        comment: Optional[str],
    ) -> None:
        await self.conn.execute(
            "INSERT INTO event_feedback "
            "(event_id, guild_id, user_id, event_rating, host_rating, comment, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(event_id, user_id) DO UPDATE SET "
            "event_rating = excluded.event_rating, host_rating = excluded.host_rating, "
            "comment = excluded.comment, created_at = excluded.created_at",
            (event_id, guild_id, user_id, event_rating, host_rating, comment, int(time.time())),
        )
        await self.conn.commit()

    async def event_feedback_summary(self, event_id: int) -> aiosqlite.Row:
        async with self.conn.execute(
            "SELECT COUNT(*) AS feedback_count, AVG(event_rating) AS avg_event, "
            "AVG(host_rating) AS avg_host FROM event_feedback WHERE event_id = ?",
            (event_id,),
        ) as cur:
            return await cur.fetchone()

    async def event_feedback_rows(
        self, event_id: int, limit: int = 5
    ) -> list[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM event_feedback WHERE event_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (event_id, limit),
        ) as cur:
            return list(await cur.fetchall())

    async def event_host_stats(
        self,
        guild_id: int,
        limit: int = 10,
        since: Optional[int] = None,
        until: Optional[int] = None,
    ) -> list[aiosqlite.Row]:
        period_filter = ""
        params: list[int] = [guild_id]
        if since is not None:
            period_filter += " AND COALESCE(e.ended_at, e.created_at) >= ?"
            params.append(since)
        if until is not None:
            period_filter += " AND COALESCE(e.ended_at, e.created_at) < ?"
            params.append(until)
        params.append(limit)
        async with self.conn.execute(
            f"""
            SELECT
                e.author_id AS author_id,
                COUNT(*) AS total_events,
                SUM(CASE WHEN e.status = 'ended' THEN 1 ELSE 0 END) AS ended_events,
                COALESCE(SUM(att.attendees), 0) AS total_attendees,
                COALESCE(SUM(fb.feedback_count), 0) AS feedback_count,
                AVG(fb.avg_event) AS avg_event,
                AVG(fb.avg_host) AS avg_host
            FROM events e
            LEFT JOIN event_host_stat_resets reset
                ON reset.guild_id = e.guild_id AND reset.host_id = e.author_id
            LEFT JOIN (
                SELECT event_id, COUNT(*) AS attendees
                FROM event_attendance
                GROUP BY event_id
            ) att ON att.event_id = e.id
            LEFT JOIN (
                SELECT event_id, COUNT(*) AS feedback_count,
                       AVG(event_rating) AS avg_event,
                       AVG(host_rating) AS avg_host
                FROM event_feedback
                GROUP BY event_id
            ) fb ON fb.event_id = e.id
            WHERE e.guild_id = ?
              AND COALESCE(e.ended_at, e.created_at) > COALESCE(reset.reset_at, 0)
              {period_filter}
            GROUP BY e.author_id
            ORDER BY ended_events DESC, total_attendees DESC, avg_host DESC
            LIMIT ?
            """,
            params,
        ) as cur:
            return list(await cur.fetchall())

    async def event_host_stat_reset_get(
        self, guild_id: int, host_id: int
    ) -> Optional[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM event_host_stat_resets WHERE guild_id = ? AND host_id = ?",
            (guild_id, host_id),
        ) as cur:
            return await cur.fetchone()

    async def event_host_stat_reset_set(
        self,
        guild_id: int,
        host_id: int,
        actor_id: int,
        reason: Optional[str] = None,
    ) -> int:
        reset_at = int(time.time())
        await self.conn.execute(
            "INSERT INTO event_host_stat_resets (guild_id, host_id, reset_at, actor_id, reason) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(guild_id, host_id) DO UPDATE SET "
            "reset_at = excluded.reset_at, actor_id = excluded.actor_id, reason = excluded.reason",
            (guild_id, host_id, reset_at, actor_id, reason),
        )
        await self.conn.commit()
        return reset_at

    # ---- admin applications --------------------------------------------

    async def get_app_config(self, guild_id: int) -> aiosqlite.Row:
        async with self.conn.execute(
            "SELECT * FROM app_config WHERE guild_id = ?", (guild_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            await self.conn.execute("INSERT INTO app_config (guild_id) VALUES (?)", (guild_id,))
            await self.conn.commit()
            async with self.conn.execute(
                "SELECT * FROM app_config WHERE guild_id = ?", (guild_id,)
            ) as cur:
                row = await cur.fetchone()
        return row

    async def update_app_config(self, guild_id: int, **fields) -> None:
        if not fields:
            return
        await self.get_app_config(guild_id)
        cols = ", ".join(f"{k} = ?" for k in fields)
        await self.conn.execute(
            f"UPDATE app_config SET {cols} WHERE guild_id = ?", (*fields.values(), guild_id))
        await self.conn.commit()

    async def add_app_position(self, guild_id, key, label, description, role_id, emoji) -> int:
        cur = await self.conn.execute(
            "INSERT INTO app_positions (guild_id, key, label, description, role_id, emoji, position) "
            "VALUES (?, ?, ?, ?, ?, ?, (SELECT COALESCE(MAX(position)+1,0) FROM app_positions WHERE guild_id=?))",
            (guild_id, key, label, description, role_id, emoji, guild_id))
        await self.conn.commit()
        return cur.lastrowid

    async def remove_app_position(self, guild_id: int, key: str) -> int:
        async with self.conn.execute(
            "SELECT id FROM app_positions WHERE guild_id = ? AND key = ?", (guild_id, key)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return 0
        await self.conn.execute("DELETE FROM app_questions WHERE position_id = ?", (row["id"],))
        await self.conn.execute("DELETE FROM app_positions WHERE id = ?", (row["id"],))
        await self.conn.commit()
        return 1

    async def get_app_positions(self, guild_id: int) -> list[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM app_positions WHERE guild_id = ? ORDER BY position, id", (guild_id,)
        ) as cur:
            return list(await cur.fetchall())

    async def get_app_position(self, position_id: int) -> Optional[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM app_positions WHERE id = ?", (position_id,)
        ) as cur:
            return await cur.fetchone()

    async def get_app_position_by_key(self, guild_id: int, key: str) -> Optional[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM app_positions WHERE guild_id = ? AND key = ?", (guild_id, key)
        ) as cur:
            return await cur.fetchone()

    async def add_app_question(self, guild_id, position_id, label, placeholder,
                               required, paragraph) -> None:
        await self.conn.execute(
            "INSERT INTO app_questions (guild_id, position_id, label, placeholder, required, paragraph, position) "
            "VALUES (?, ?, ?, ?, ?, ?, (SELECT COALESCE(MAX(position)+1,0) FROM app_questions WHERE position_id=?))",
            (guild_id, position_id, label, placeholder, required, paragraph, position_id))
        await self.conn.commit()

    async def get_app_questions(self, position_id: int) -> list[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM app_questions WHERE position_id = ? ORDER BY position, id", (position_id,)
        ) as cur:
            return list(await cur.fetchall())

    async def clear_app_questions(self, position_id: int) -> None:
        await self.conn.execute("DELETE FROM app_questions WHERE position_id = ?", (position_id,))
        await self.conn.commit()

    async def create_application(self, guild_id, position_id, position_label,
                                 applicant_id, answers) -> int:
        cur = await self.conn.execute(
            "INSERT INTO applications (guild_id, position_id, position_label, applicant_id, answers, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (guild_id, position_id, position_label, applicant_id, answers, int(time.time())))
        await self.conn.commit()
        return cur.lastrowid

    async def get_application(self, app_id: int) -> Optional[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM applications WHERE id = ?", (app_id,)
        ) as cur:
            return await cur.fetchone()

    async def get_application_by_message(self, message_id: int) -> Optional[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM applications WHERE message_id = ?", (message_id,)
        ) as cur:
            return await cur.fetchone()

    async def pending_application_for(self, guild_id, applicant_id) -> Optional[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM applications WHERE guild_id = ? AND applicant_id = ? AND status = 'pending'",
            (guild_id, applicant_id)
        ) as cur:
            return await cur.fetchone()

    async def update_application(self, app_id: int, **fields) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        await self.conn.execute(
            f"UPDATE applications SET {cols} WHERE id = ?", (*fields.values(), app_id))
        await self.conn.commit()

    async def get_user_applications(self, guild_id, applicant_id) -> list[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM applications WHERE guild_id = ? AND applicant_id = ? ORDER BY id DESC",
            (guild_id, applicant_id)
        ) as cur:
            return list(await cur.fetchall())

    async def get_app_reviewer_roles(self, guild_id: int) -> list[int]:
        async with self.conn.execute(
            "SELECT role_id FROM app_roles WHERE guild_id = ?", (guild_id,)
        ) as cur:
            return [r["role_id"] for r in await cur.fetchall()]

    async def set_app_reviewer_roles(self, guild_id: int, role_ids) -> None:
        await self.conn.execute("DELETE FROM app_roles WHERE guild_id = ?", (guild_id,))
        await self.conn.executemany(
            "INSERT OR IGNORE INTO app_roles (guild_id, role_id) VALUES (?, ?)",
            [(guild_id, rid) for rid in role_ids])
        await self.conn.commit()

    # ---- mod cases ------------------------------------------------------

    async def add_case(
        self,
        guild_id: int,
        user_id: int,
        moderator_id: int,
        action: str,
        reason: Optional[str],
        expires_at: Optional[int] = None,
    ) -> int:
        cfg = await self.get_guild_config(guild_id)
        case_number = int(cfg["case_counter"]) + 1
        await self.conn.execute(
            "UPDATE guild_config SET case_counter = ? WHERE guild_id = ?",
            (case_number, guild_id),
        )
        await self.conn.execute(
            "INSERT INTO mod_cases "
            "(guild_id, case_number, user_id, moderator_id, action, reason, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (guild_id, case_number, user_id, moderator_id, action, reason, int(time.time()), expires_at),
        )
        await self.conn.commit()
        return case_number

    async def get_case(self, guild_id: int, case_number: int) -> Optional[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM mod_cases WHERE guild_id = ? AND case_number = ?",
            (guild_id, case_number),
        ) as cur:
            return await cur.fetchone()

    async def get_user_cases(self, guild_id: int, user_id: int) -> list[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM mod_cases WHERE guild_id = ? AND user_id = ? ORDER BY case_number DESC",
            (guild_id, user_id),
        ) as cur:
            return list(await cur.fetchall())

    async def active_warn_count(self, guild_id: int, user_id: int) -> int:
        now = int(time.time())
        async with self.conn.execute(
            "SELECT COUNT(*) AS c FROM mod_cases WHERE guild_id = ? AND user_id = ? "
            "AND action = 'warn' AND active = 1 AND (expires_at IS NULL OR expires_at > ?)",
            (guild_id, user_id, now),
        ) as cur:
            row = await cur.fetchone()
        return int(row["c"])

    async def clear_warns(self, guild_id: int, user_id: int) -> int:
        cur = await self.conn.execute(
            "UPDATE mod_cases SET active = 0 WHERE guild_id = ? AND user_id = ? "
            "AND action = 'warn' AND active = 1",
            (guild_id, user_id),
        )
        await self.conn.commit()
        return cur.rowcount

    async def due_tempbans(self) -> list[aiosqlite.Row]:
        now = int(time.time())
        async with self.conn.execute(
            "SELECT * FROM mod_cases WHERE action = 'tempban' AND active = 1 "
            "AND expires_at IS NOT NULL AND expires_at <= ?",
            (now,),
        ) as cur:
            return list(await cur.fetchall())

    async def deactivate_case(self, case_id: int) -> None:
        await self.conn.execute(
            "UPDATE mod_cases SET active = 0 WHERE id = ?", (case_id,)
        )
        await self.conn.commit()

    # ---- warn punishments (escalation ladder) ---------------------------

    async def set_warn_punishment(
        self, guild_id: int, threshold: int, action: str, duration_seconds: Optional[int]
    ) -> None:
        await self.conn.execute(
            "INSERT INTO warn_punishments (guild_id, threshold, action, duration_seconds) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(guild_id, threshold) DO UPDATE SET "
            "action = excluded.action, duration_seconds = excluded.duration_seconds",
            (guild_id, threshold, action, duration_seconds),
        )
        await self.conn.commit()

    async def remove_warn_punishment(self, guild_id: int, threshold: int) -> int:
        cur = await self.conn.execute(
            "DELETE FROM warn_punishments WHERE guild_id = ? AND threshold = ?",
            (guild_id, threshold),
        )
        await self.conn.commit()
        return cur.rowcount

    async def get_warn_punishments(self, guild_id: int) -> list[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM warn_punishments WHERE guild_id = ? ORDER BY threshold",
            (guild_id,),
        ) as cur:
            return list(await cur.fetchall())

    async def warn_punishment_for(self, guild_id: int, count: int) -> Optional[aiosqlite.Row]:
        """The highest configured punishment whose threshold == count (exact match)."""
        async with self.conn.execute(
            "SELECT * FROM warn_punishments WHERE guild_id = ? AND threshold = ?",
            (guild_id, count),
        ) as cur:
            return await cur.fetchone()

    # ---- self-assign role panels ----------------------------------------

    async def create_selfrole_panel(
        self,
        message_id: int,
        guild_id: int,
        channel_id: int,
        title: Optional[str],
        max_roles: int,
        options: list[tuple[int, Optional[str], Optional[str], Optional[str]]],
    ) -> None:
        await self.conn.execute(
            "INSERT OR REPLACE INTO selfrole_panels "
            "(message_id, guild_id, channel_id, title, max_roles) VALUES (?, ?, ?, ?, ?)",
            (message_id, guild_id, channel_id, title, max_roles),
        )
        await self.conn.executemany(
            "INSERT OR REPLACE INTO selfrole_options "
            "(message_id, role_id, label, emoji, description, position) VALUES (?, ?, ?, ?, ?, ?)",
            [
                (message_id, role_id, label, emoji, desc, idx)
                for idx, (role_id, label, emoji, desc) in enumerate(options)
            ],
        )
        await self.conn.commit()

    async def get_selfrole_panel(self, message_id: int) -> Optional[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM selfrole_panels WHERE message_id = ?", (message_id,)
        ) as cur:
            return await cur.fetchone()

    async def get_selfrole_options(self, message_id: int) -> list[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM selfrole_options WHERE message_id = ? ORDER BY position",
            (message_id,),
        ) as cur:
            return list(await cur.fetchall())

    async def latest_selfrole_panel(self, channel_id: int) -> Optional[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM selfrole_panels WHERE channel_id = ? ORDER BY message_id DESC LIMIT 1",
            (channel_id,),
        ) as cur:
            return await cur.fetchone()

    async def add_selfrole_option(
        self, message_id: int, role_id: int, label: Optional[str],
        emoji: Optional[str], description: Optional[str],
    ) -> None:
        async with self.conn.execute(
            "SELECT COALESCE(MAX(position)+1, 0) AS p FROM selfrole_options WHERE message_id = ?",
            (message_id,),
        ) as cur:
            pos = (await cur.fetchone())["p"]
        await self.conn.execute(
            "INSERT OR REPLACE INTO selfrole_options "
            "(message_id, role_id, label, emoji, description, position) VALUES (?, ?, ?, ?, ?, ?)",
            (message_id, role_id, label, emoji, description, pos),
        )
        await self.conn.commit()

    async def remove_selfrole_option(self, message_id: int, role_id: int) -> int:
        cur = await self.conn.execute(
            "DELETE FROM selfrole_options WHERE message_id = ? AND role_id = ?",
            (message_id, role_id),
        )
        await self.conn.commit()
        return cur.rowcount

    async def create_selfrole_panel_row(
        self, message_id: int, guild_id: int, channel_id: int,
        title: Optional[str], max_roles: int,
    ) -> None:
        await self.conn.execute(
            "INSERT OR REPLACE INTO selfrole_panels "
            "(message_id, guild_id, channel_id, title, max_roles) VALUES (?, ?, ?, ?, ?)",
            (message_id, guild_id, channel_id, title, max_roles),
        )
        await self.conn.commit()

    # ---- levels / XP ----------------------------------------------------

    async def get_level_row(self, guild_id: int, user_id: int) -> aiosqlite.Row:
        async with self.conn.execute(
            "SELECT * FROM user_levels WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            await self.conn.execute(
                "INSERT INTO user_levels (guild_id, user_id) VALUES (?, ?)",
                (guild_id, user_id),
            )
            await self.conn.commit()
            async with self.conn.execute(
                "SELECT * FROM user_levels WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ) as cur:
                row = await cur.fetchone()
        return row

    async def update_level_row(
        self, guild_id: int, user_id: int, xp: int, level: int, last_msg_ts: int
    ) -> None:
        await self.conn.execute(
            "UPDATE user_levels SET xp = ?, level = ?, last_msg_ts = ? "
            "WHERE guild_id = ? AND user_id = ?",
            (xp, level, last_msg_ts, guild_id, user_id),
        )
        await self.conn.commit()

    async def get_rank(self, guild_id: int, user_id: int) -> Optional[int]:
        async with self.conn.execute(
            "SELECT COUNT(*) AS c FROM user_levels WHERE guild_id = ? AND xp > "
            "(SELECT xp FROM user_levels WHERE guild_id = ? AND user_id = ?)",
            (guild_id, guild_id, user_id),
        ) as cur:
            row = await cur.fetchone()
        return int(row["c"]) + 1 if row is not None else None

    async def leaderboard(self, guild_id: int, limit: int = 10) -> list[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM user_levels WHERE guild_id = ? ORDER BY xp DESC LIMIT ?",
            (guild_id, limit),
        ) as cur:
            return list(await cur.fetchall())

    async def set_level_reward(self, guild_id: int, level: int, role_id: int) -> None:
        await self.conn.execute(
            "INSERT INTO level_rewards (guild_id, level, role_id) VALUES (?, ?, ?) "
            "ON CONFLICT(guild_id, level) DO UPDATE SET role_id = excluded.role_id",
            (guild_id, level, role_id),
        )
        await self.conn.commit()

    async def remove_level_reward(self, guild_id: int, level: int) -> int:
        cur = await self.conn.execute(
            "DELETE FROM level_rewards WHERE guild_id = ? AND level = ?", (guild_id, level)
        )
        await self.conn.commit()
        return cur.rowcount

    async def get_level_rewards(self, guild_id: int) -> list[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM level_rewards WHERE guild_id = ? ORDER BY level", (guild_id,)
        ) as cur:
            return list(await cur.fetchall())

    async def get_rank_prefs(self, user_id: int) -> Optional[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM rank_prefs WHERE user_id = ?", (user_id,)
        ) as cur:
            return await cur.fetchone()

    async def set_rank_prefs(
        self,
        user_id: int,
        *,
        accent: Optional[str] = None,
        bg: Optional[str] = None,
    ) -> None:
        await self.conn.execute(
            "INSERT INTO rank_prefs (user_id, accent, bg) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "accent = COALESCE(excluded.accent, rank_prefs.accent), "
            "bg = COALESCE(excluded.bg, rank_prefs.bg)",
            (user_id, accent, bg),
        )
        await self.conn.commit()

    async def reset_rank_prefs(self, user_id: int) -> int:
        cur = await self.conn.execute(
            "DELETE FROM rank_prefs WHERE user_id = ?", (user_id,)
        )
        await self.conn.commit()
        return cur.rowcount

    # ---- giveaways ------------------------------------------------------

    async def create_giveaway(
        self,
        guild_id: int,
        channel_id: int,
        prize: str,
        winners: int,
        host_id: int,
        ends_at: int,
    ) -> int:
        cur = await self.conn.execute(
            "INSERT INTO giveaways (guild_id, channel_id, prize, winners, host_id, ends_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (guild_id, channel_id, prize, winners, host_id, ends_at),
        )
        await self.conn.commit()
        return cur.lastrowid

    async def set_giveaway_message(self, giveaway_id: int, message_id: int) -> None:
        await self.conn.execute(
            "UPDATE giveaways SET message_id = ? WHERE id = ?",
            (message_id, giveaway_id),
        )
        await self.conn.commit()

    async def get_giveaway(self, giveaway_id: int) -> Optional[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM giveaways WHERE id = ?", (giveaway_id,)
        ) as cur:
            return await cur.fetchone()

    async def get_giveaway_by_message(self, message_id: int) -> Optional[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM giveaways WHERE message_id = ?", (message_id,)
        ) as cur:
            return await cur.fetchone()

    async def giveaways_due(self) -> list[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM giveaways WHERE ended = 0 AND message_id IS NOT NULL AND ends_at <= ?",
            (int(time.time()),),
        ) as cur:
            return list(await cur.fetchall())

    async def mark_giveaway_ended(self, giveaway_id: int) -> None:
        await self.conn.execute(
            "UPDATE giveaways SET ended = 1 WHERE id = ?",
            (giveaway_id,),
        )
        await self.conn.commit()

    async def add_giveaway_entry(self, message_id: int, user_id: int) -> bool:
        cur = await self.conn.execute(
            "INSERT OR IGNORE INTO giveaway_entries (message_id, user_id) VALUES (?, ?)",
            (message_id, user_id),
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def giveaway_entries(self, message_id: int) -> list[int]:
        async with self.conn.execute(
            "SELECT user_id FROM giveaway_entries WHERE message_id = ?",
            (message_id,),
        ) as cur:
            return [row["user_id"] for row in await cur.fetchall()]

    async def giveaway_entry_count(self, message_id: int) -> int:
        async with self.conn.execute(
            "SELECT COUNT(*) AS c FROM giveaway_entries WHERE message_id = ?",
            (message_id,),
        ) as cur:
            row = await cur.fetchone()
        return int(row["c"]) if row is not None else 0

    # ---- starboard ------------------------------------------------------

    async def get_starboard_post(
        self, guild_id: int, original_message_id: int
    ) -> Optional[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM starboard_posts WHERE guild_id = ? AND original_message_id = ?",
            (guild_id, original_message_id),
        ) as cur:
            return await cur.fetchone()

    async def upsert_starboard_post(
        self, guild_id: int, original_message_id: int, board_message_id: Optional[int]
    ) -> None:
        await self.conn.execute(
            "INSERT INTO starboard_posts (guild_id, original_message_id, board_message_id) "
            "VALUES (?, ?, ?) ON CONFLICT(guild_id, original_message_id) "
            "DO UPDATE SET board_message_id = excluded.board_message_id",
            (guild_id, original_message_id, board_message_id),
        )
        await self.conn.commit()

    # ---- category access roles ------------------------------------------

    async def get_category_roles(self, guild_id: int, category_key: str) -> list[int]:
        async with self.conn.execute(
            "SELECT role_id FROM category_roles WHERE guild_id = ? AND category_key = ?",
            (guild_id, category_key),
        ) as cur:
            return [r["role_id"] for r in await cur.fetchall()]

    async def set_category_roles(
        self, guild_id: int, category_key: str, role_ids: Iterable[int]
    ) -> None:
        await self.conn.execute(
            "DELETE FROM category_roles WHERE guild_id = ? AND category_key = ?",
            (guild_id, category_key),
        )
        await self.conn.executemany(
            "INSERT OR IGNORE INTO category_roles (guild_id, category_key, role_id) VALUES (?, ?, ?)",
            [(guild_id, category_key, rid) for rid in role_ids],
        )
        await self.conn.commit()

    async def all_category_roles(self, guild_id: int) -> dict[str, list[int]]:
        async with self.conn.execute(
            "SELECT category_key, role_id FROM category_roles WHERE guild_id = ?",
            (guild_id,),
        ) as cur:
            result: dict[str, list[int]] = {}
            for row in await cur.fetchall():
                result.setdefault(row["category_key"], []).append(row["role_id"])
        return result

    # ---- economy: config ------------------------------------------------

    async def get_econ_config(self, guild_id: int) -> aiosqlite.Row:
        async with self.conn.execute(
            "SELECT * FROM economy_config WHERE guild_id = ?", (guild_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            await self.conn.execute("INSERT INTO economy_config (guild_id) VALUES (?)", (guild_id,))
            await self.conn.commit()
            async with self.conn.execute(
                "SELECT * FROM economy_config WHERE guild_id = ?", (guild_id,)
            ) as cur:
                row = await cur.fetchone()
        return row

    async def update_econ_config(self, guild_id: int, **fields) -> None:
        if not fields:
            return
        await self.get_econ_config(guild_id)
        cols = ", ".join(f"{k} = ?" for k in fields)
        await self.conn.execute(
            f"UPDATE economy_config SET {cols} WHERE guild_id = ?",
            (*fields.values(), guild_id),
        )
        await self.conn.commit()

    async def econ_manager_set(
        self, guild_id: int, target_type: str, target_id: int, enabled: bool = True
    ) -> None:
        if enabled:
            await self.conn.execute(
                "INSERT OR IGNORE INTO economy_managers "
                "(guild_id, target_type, target_id) VALUES (?, ?, ?)",
                (guild_id, target_type, target_id),
            )
        else:
            await self.conn.execute(
                "DELETE FROM economy_managers WHERE guild_id = ? AND target_type = ? AND target_id = ?",
                (guild_id, target_type, target_id),
            )
        await self.conn.commit()

    async def econ_managers(self, guild_id: int) -> list[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM economy_managers WHERE guild_id = ? ORDER BY target_type, target_id",
            (guild_id,),
        ) as cur:
            return list(await cur.fetchall())

    async def econ_is_manager(
        self, guild_id: int, user_id: int, role_ids: Iterable[int]
    ) -> bool:
        ids = list(role_ids)
        checks = [("user", user_id), *(("role", rid) for rid in ids)]
        placeholders = ", ".join("(?, ?)" for _ in checks)
        params: list[int | str] = [guild_id]
        for target_type, target_id in checks:
            params.extend([target_type, target_id])
        async with self.conn.execute(
            "SELECT 1 FROM economy_managers WHERE guild_id = ? "
            f"AND (target_type, target_id) IN ({placeholders}) LIMIT 1",
            tuple(params),
        ) as cur:
            return await cur.fetchone() is not None

    async def econ_actor_last_grant(self, guild_id: int, actor_id: int) -> Optional[int]:
        async with self.conn.execute(
            "SELECT created_at FROM economy_transactions WHERE guild_id = ? "
            "AND sender_id = ? AND command IN ('shop_grant', 'shop_take', 'shop_set') "
            "ORDER BY created_at DESC LIMIT 1",
            (guild_id, actor_id),
        ) as cur:
            row = await cur.fetchone()
        return int(row["created_at"]) if row else None

    async def econ_actor_granted_since(self, guild_id: int, actor_id: int, since: int) -> int:
        async with self.conn.execute(
            "SELECT COALESCE(SUM(amount), 0) AS total FROM economy_transactions "
            "WHERE guild_id = ? AND sender_id = ? AND command = 'shop_grant' "
            "AND created_at >= ? AND voided = 0",
            (guild_id, actor_id, since),
        ) as cur:
            row = await cur.fetchone()
        return int(row["total"] or 0)

    # ---- economy: users -------------------------------------------------

    async def get_econ_user(self, guild_id: int, user_id: int) -> aiosqlite.Row:
        async with self.conn.execute(
            "SELECT * FROM economy_users WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            cfg = await self.get_econ_config(guild_id)
            await self.conn.execute(
                "INSERT INTO economy_users (guild_id, user_id, wallet) VALUES (?, ?, ?)",
                (guild_id, user_id, cfg["start_balance"]),
            )
            await self.conn.commit()
            async with self.conn.execute(
                "SELECT * FROM economy_users WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ) as cur:
                row = await cur.fetchone()
        return row

    async def econ_update(self, guild_id: int, user_id: int, **fields) -> None:
        await self.get_econ_user(guild_id, user_id)
        cols = ", ".join(f"{k} = ?" for k in fields)
        await self.conn.execute(
            f"UPDATE economy_users SET {cols} WHERE guild_id = ? AND user_id = ?",
            (*fields.values(), guild_id, user_id),
        )
        await self.conn.commit()

    async def econ_add_wallet(
        self,
        guild_id: int,
        user_id: int,
        delta: int,
        *,
        command: Optional[str] = None,
        actor_id: Optional[int] = None,
        note: Optional[str] = None,
        record: bool = True,
    ) -> int:
        row = await self.get_econ_user(guild_id, user_id)
        new = max(0, row["wallet"] + delta)
        await self.econ_update(guild_id, user_id, wallet=new)
        if record and delta:
            await self.add_transaction(
                guild_id,
                "increment" if delta > 0 else "decrement",
                abs(delta),
                command=command,
                sender_id=actor_id,
                receiver_id=user_id,
                note=note,
            )
        return new

    async def econ_add_shop_wallet(
        self,
        guild_id: int,
        user_id: int,
        delta: int,
        *,
        command: Optional[str] = None,
        actor_id: Optional[int] = None,
        note: Optional[str] = None,
        record: bool = True,
    ) -> int:
        row = await self.get_econ_user(guild_id, user_id)
        new = max(0, row["shop_wallet"] + delta)
        await self.econ_update(guild_id, user_id, shop_wallet=new)
        if record and delta:
            await self.add_transaction(
                guild_id,
                "increment" if delta > 0 else "decrement",
                abs(delta),
                command=command,
                sender_id=actor_id,
                receiver_id=user_id,
                note=note,
            )
        return new

    # ---- safe / jail / steal (reference shop mechanics) ----------------

    async def econ_safe_move(self, guild_id: int, user_id: int, amount: int, to_safe: bool) -> bool:
        """Move ``amount`` between shop_wallet and safe_balance. Returns False if insufficient."""
        row = await self.get_econ_user(guild_id, user_id)
        if to_safe:
            if row["shop_wallet"] < amount:
                return False
            await self.econ_update(guild_id, user_id, shop_wallet=row["shop_wallet"] - amount,
                                   safe_balance=row["safe_balance"] + amount)
        else:
            if row["safe_balance"] < amount:
                return False
            await self.econ_update(guild_id, user_id, shop_wallet=row["shop_wallet"] + amount,
                                   safe_balance=row["safe_balance"] - amount)
        return True

    async def econ_jail_remaining(self, guild_id: int, user_id: int) -> int:
        """Seconds left in jail; auto-releases and credits served time when expired."""
        row = await self.get_econ_user(guild_id, user_id)
        until = row["jail_until"] or 0
        if not until:
            return 0
        now = int(time.time())
        if until > now:
            return until - now
        served = max(0, (until - (row["jail_start"] or until)) // 60)
        await self.econ_update(guild_id, user_id, jail_until=0, jail_start=0, jail_type=None,
                               total_jail_time=row["total_jail_time"] + served)
        return 0

    async def econ_put_in_jail(self, guild_id: int, user_id: int, minutes: int,
                               jail_type: str = "Тюрьма") -> None:
        row = await self.get_econ_user(guild_id, user_id)
        now = int(time.time())
        await self.econ_update(guild_id, user_id, jail_until=now + minutes * 60, jail_start=now,
                               jail_type=jail_type, jail_count=row["jail_count"] + 1)

    async def econ_release_jail(self, guild_id: int, user_id: int) -> bool:
        row = await self.get_econ_user(guild_id, user_id)
        if not row["jail_until"]:
            return False
        now = int(time.time())
        served = max(0, (now - (row["jail_start"] or now)) // 60)
        await self.econ_update(guild_id, user_id, jail_until=0, jail_start=0, jail_type=None,
                               total_jail_time=row["total_jail_time"] + served)
        return True

    async def econ_record_steal(self, guild_id: int, user_id: int, success: bool) -> None:
        row = await self.get_econ_user(guild_id, user_id)
        field = "steal_success" if success else "steal_fail"
        await self.econ_update(guild_id, user_id, **{field: row[field] + 1},
                               last_steal=int(time.time()))

    async def econ_robbery_leaderboard(self, guild_id: int, limit: int = 10) -> list[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM economy_users WHERE guild_id = ? AND steal_success > 0 "
            "ORDER BY steal_success DESC LIMIT ?", (guild_id, limit)) as cur:
            return list(await cur.fetchall())

    async def econ_set_grant_protection(self, guild_id: int, user_id: int) -> None:
        await self.econ_update(guild_id, user_id, last_grant=int(time.time()))

    async def econ_set_boost(self, guild_id: int, user_id: int, kind: str, until: int) -> None:
        await self.econ_update(guild_id, user_id, **{kind: until})

    async def econ_transfer(
        self,
        guild_id: int,
        sender_id: int,
        receiver_id: int,
        amount: int,
        *,
        command: str,
        note: Optional[str] = None,
    ) -> tuple[int, int]:
        sender = await self.get_econ_user(guild_id, sender_id)
        receiver = await self.get_econ_user(guild_id, receiver_id)
        sender_wallet = max(0, sender["wallet"] - amount)
        receiver_wallet = receiver["wallet"] + amount
        await self.conn.execute(
            "UPDATE economy_users SET wallet = ? WHERE guild_id = ? AND user_id = ?",
            (sender_wallet, guild_id, sender_id),
        )
        await self.conn.execute(
            "UPDATE economy_users SET wallet = ? WHERE guild_id = ? AND user_id = ?",
            (receiver_wallet, guild_id, receiver_id),
        )
        await self.add_transaction(
            guild_id,
            "transfer",
            amount,
            command=command,
            sender_id=sender_id,
            receiver_id=receiver_id,
            note=note,
            commit=False,
        )
        await self.conn.commit()
        return sender_wallet, receiver_wallet

    async def econ_leaderboard(self, guild_id: int, limit: int = 10) -> list[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM economy_users WHERE guild_id = ? "
            "ORDER BY (shop_wallet + safe_balance) DESC LIMIT ?",
            (guild_id, limit),
        ) as cur:
            return list(await cur.fetchall())

    # ---- economy: shop --------------------------------------------------

    async def add_transaction(
        self,
        guild_id: int,
        operation: str,
        amount: int,
        *,
        command: Optional[str] = None,
        sender_id: Optional[int] = None,
        receiver_id: Optional[int] = None,
        note: Optional[str] = None,
        commit: bool = True,
    ) -> int:
        cur = await self.conn.execute(
            "INSERT INTO economy_transactions "
            "(guild_id, operation, command, amount, sender_id, receiver_id, note, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (guild_id, operation, command, amount, sender_id, receiver_id, note, int(time.time())),
        )
        if commit:
            await self.conn.commit()
        return cur.lastrowid

    async def list_transactions(
        self, guild_id: int, *, user_id: Optional[int] = None, limit: int = 10
    ) -> list[aiosqlite.Row]:
        limit = max(1, min(limit, 25))
        if user_id is None:
            async with self.conn.execute(
                "SELECT * FROM economy_transactions WHERE guild_id = ? "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                (guild_id, limit),
            ) as cur:
                return list(await cur.fetchall())
        async with self.conn.execute(
            "SELECT * FROM economy_transactions WHERE guild_id = ? "
            "AND (sender_id = ? OR receiver_id = ?) "
            "ORDER BY created_at DESC, id DESC LIMIT ?",
            (guild_id, user_id, user_id, limit),
        ) as cur:
            return list(await cur.fetchall())

    # ---- economy: shop --------------------------------------------------

    async def shop_add(
        self, guild_id: int, name: str, description: Optional[str],
        price: int, role_id: int, duration: int, *, item_type: str = "role",
        purchase_limit: int = 0, response_message: Optional[str] = None,
        income_amount: int = 0, income_cooldown: int = 0,
        category: str = "roles", stock: int = 0, active: bool = True,
        delivery_type: Optional[str] = None, catalog_key: Optional[str] = None,
    ) -> int:
        delivery = delivery_type or item_type
        cur = await self.conn.execute(
            "INSERT INTO shop_items "
            "(guild_id, name, description, price, role_id, duration, item_type, "
            "purchase_limit, response_message, income_amount, income_cooldown, "
            "category, stock, active, delivery_type, catalog_key) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                guild_id, name, description, price, role_id, duration, item_type,
                purchase_limit, response_message, income_amount, income_cooldown,
                category, stock, int(active), delivery, catalog_key,
            ),
        )
        await self.conn.commit()
        return cur.lastrowid

    async def shop_catalog_upsert(
        self,
        guild_id: int,
        *,
        catalog_key: str,
        name: str,
        description: str,
        price: int,
        category: str,
        delivery_type: str,
        overwrite: bool = False,
        activate: bool = False,
    ) -> tuple[int, bool, bool]:
        async with self.conn.execute(
            "SELECT * FROM shop_items WHERE guild_id = ? AND catalog_key = ?",
            (guild_id, catalog_key),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            item_id = await self.shop_add(
                guild_id,
                name,
                description,
                price,
                0,
                0,
                item_type=delivery_type,
                category=category,
                delivery_type=delivery_type,
                catalog_key=catalog_key,
            )
            return item_id, True, False
        if overwrite:
            await self.conn.execute(
                "UPDATE shop_items SET name = ?, description = ?, price = ?, category = ?, "
                "item_type = ?, delivery_type = ?, active = ? WHERE guild_id = ? AND id = ?",
                (
                    name, description, price, category, delivery_type, delivery_type,
                    1 if activate else row["active"], guild_id, row["id"],
                ),
            )
            await self.conn.commit()
            return int(row["id"]), False, True
        if activate and not row["active"]:
            await self.conn.execute(
                "UPDATE shop_items SET active = 1 WHERE guild_id = ? AND id = ?",
                (guild_id, row["id"]),
            )
            await self.conn.commit()
            return int(row["id"]), False, True
        return int(row["id"]), False, False

    async def shop_remove(self, guild_id: int, item_id: int) -> int:
        cur = await self.conn.execute(
            "DELETE FROM shop_items WHERE guild_id = ? AND id = ?", (guild_id, item_id)
        )
        await self.conn.commit()
        return cur.rowcount

    async def shop_list(
        self,
        guild_id: int,
        *,
        category: Optional[str] = None,
        include_inactive: bool = False,
    ) -> list[aiosqlite.Row]:
        where = ["guild_id = ?"]
        params: list[int | str] = [guild_id]
        if category:
            where.append("LOWER(category) = LOWER(?)")
            params.append(category)
        if not include_inactive:
            where.append("active = 1")
        async with self.conn.execute(
            f"SELECT * FROM shop_items WHERE {' AND '.join(where)} ORDER BY category, price, id",
            tuple(params),
        ) as cur:
            return list(await cur.fetchall())

    async def shop_get(self, guild_id: int, item_id: int) -> Optional[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM shop_items WHERE guild_id = ? AND id = ?", (guild_id, item_id)
        ) as cur:
            return await cur.fetchone()

    async def shop_purchase_count(self, guild_id: int, item_id: int) -> int:
        async with self.conn.execute(
            "SELECT COUNT(*) AS c FROM shop_purchases WHERE guild_id = ? AND item_id = ?",
            (guild_id, item_id),
        ) as cur:
            row = await cur.fetchone()
        return int(row["c"])

    async def shop_update_active(self, guild_id: int, item_id: int, active: bool) -> int:
        cur = await self.conn.execute(
            "UPDATE shop_items SET active = ? WHERE guild_id = ? AND id = ?",
            (int(active), guild_id, item_id),
        )
        await self.conn.commit()
        return cur.rowcount

    async def shop_user_purchases(
        self, guild_id: int, user_id: int, item_id: Optional[int] = None
    ) -> list[aiosqlite.Row]:
        if item_id is None:
            async with self.conn.execute(
                "SELECT * FROM shop_purchases WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ) as cur:
                return list(await cur.fetchall())
        async with self.conn.execute(
            "SELECT * FROM shop_purchases WHERE guild_id = ? AND user_id = ? AND item_id = ?",
            (guild_id, user_id, item_id),
        ) as cur:
            return list(await cur.fetchall())

    async def shop_record_purchase(
        self,
        guild_id: int,
        user_id: int,
        item_id: int,
        role_id: int,
        item_type: str,
        expires_at: Optional[int],
    ) -> int:
        now = int(time.time())
        cur = await self.conn.execute(
            "INSERT INTO shop_purchases "
            "(guild_id, user_id, item_id, role_id, item_type, purchased_at, expires_at, last_income_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (guild_id, user_id, item_id, role_id, item_type, now, expires_at, now),
        )
        await self.conn.commit()
        return cur.lastrowid

    async def shop_inventory_add(
        self,
        guild_id: int,
        user_id: int,
        item_id: int,
        item_type: str,
        name: str,
        metadata: Optional[str] = None,
    ) -> int:
        cur = await self.conn.execute(
            "INSERT INTO shop_inventory "
            "(guild_id, user_id, item_id, item_type, name, metadata, purchased_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (guild_id, user_id, item_id, item_type, name, metadata, int(time.time())),
        )
        await self.conn.commit()
        return cur.lastrowid

    async def shop_inventory_list(
        self, guild_id: int, user_id: int, *, include_used: bool = False
    ) -> list[aiosqlite.Row]:
        where = "guild_id = ? AND user_id = ?"
        if not include_used:
            where += " AND used_at IS NULL"
        async with self.conn.execute(
            f"SELECT * FROM shop_inventory WHERE {where} ORDER BY purchased_at DESC, id DESC",
            (guild_id, user_id),
        ) as cur:
            return list(await cur.fetchall())

    async def shop_inventory_use_by_key(self, guild_id: int, user_id: int, catalog_key: str) -> bool:
        """Mark one unused inventory item (matched by catalog key) as used. Returns True if consumed."""
        async with self.conn.execute(
            "SELECT inv.id FROM shop_inventory inv JOIN shop_items si ON inv.item_id = si.id "
            "WHERE inv.guild_id = ? AND inv.user_id = ? AND inv.used_at IS NULL "
            "AND si.catalog_key = ? LIMIT 1",
            (guild_id, user_id, catalog_key),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return False
        await self.conn.execute("UPDATE shop_inventory SET used_at = ? WHERE id = ?",
                                (int(time.time()), row["id"]))
        await self.conn.commit()
        return True

    async def shop_inventory_use_first_by_keys(
        self, guild_id: int, user_id: int, catalog_keys: Iterable[str]
    ) -> Optional[str]:
        """Consume one unused inventory item matching the first available key."""
        keys = list(catalog_keys)
        if not keys:
            return None
        placeholders = ", ".join("?" for _ in keys)
        async with self.conn.execute(
            "SELECT inv.id, si.catalog_key FROM shop_inventory inv JOIN shop_items si ON inv.item_id = si.id "
            "WHERE inv.guild_id = ? AND inv.user_id = ? AND inv.used_at IS NULL "
            f"AND si.catalog_key IN ({placeholders}) "
            "ORDER BY inv.purchased_at ASC, inv.id ASC LIMIT 1",
            (guild_id, user_id, *keys),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        await self.conn.execute(
            "UPDATE shop_inventory SET used_at = ? WHERE id = ?",
            (int(time.time()), row["id"]),
        )
        await self.conn.commit()
        return str(row["catalog_key"])

    async def shop_owns_key(self, guild_id: int, user_id: int, catalog_key: str) -> bool:
        async with self.conn.execute(
            "SELECT 1 FROM shop_inventory inv JOIN shop_items si ON inv.item_id = si.id "
            "WHERE inv.guild_id = ? AND inv.user_id = ? AND inv.used_at IS NULL "
            "AND si.catalog_key = ? LIMIT 1",
            (guild_id, user_id, catalog_key),
        ) as cur:
            return await cur.fetchone() is not None

    async def shop_owned_keys(self, guild_id: int, user_id: int) -> list[str]:
        async with self.conn.execute(
            "SELECT DISTINCT si.catalog_key FROM shop_inventory inv "
            "JOIN shop_items si ON inv.item_id = si.id "
            "WHERE inv.guild_id = ? AND inv.user_id = ? AND inv.used_at IS NULL "
            "AND si.catalog_key IS NOT NULL",
            (guild_id, user_id),
        ) as cur:
            return [r["catalog_key"] for r in await cur.fetchall()]

    async def shop_request_create(
        self,
        guild_id: int,
        user_id: int,
        item_id: int,
        item_name: str,
        price: int = 0,
        details: Optional[str] = None,
    ) -> int:
        cur = await self.conn.execute(
            "INSERT INTO shop_requests "
            "(guild_id, user_id, item_id, item_name, price, details, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (guild_id, user_id, item_id, item_name, price, details, int(time.time())),
        )
        await self.conn.commit()
        return cur.lastrowid

    async def shop_requests_list(
        self, guild_id: int, *, status: Optional[str] = "pending", limit: int = 10
    ) -> list[aiosqlite.Row]:
        limit = max(1, min(limit, 25))
        if status:
            async with self.conn.execute(
                "SELECT * FROM shop_requests WHERE guild_id = ? AND status = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (guild_id, status, limit),
            ) as cur:
                return list(await cur.fetchall())
        async with self.conn.execute(
            "SELECT * FROM shop_requests WHERE guild_id = ? ORDER BY created_at DESC LIMIT ?",
            (guild_id, limit),
        ) as cur:
            return list(await cur.fetchall())

    async def shop_request_get(self, guild_id: int, request_id: int) -> Optional[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM shop_requests WHERE guild_id = ? AND id = ?",
            (guild_id, request_id),
        ) as cur:
            return await cur.fetchone()

    async def shop_request_update(
        self,
        guild_id: int,
        request_id: int,
        status: str,
        actor_id: Optional[int] = None,
    ) -> int:
        cur = await self.conn.execute(
            "UPDATE shop_requests SET status = ?, claimed_by = ?, closed_at = ? "
            "WHERE guild_id = ? AND id = ?",
            (status, actor_id, int(time.time()), guild_id, request_id),
        )
        await self.conn.commit()
        return cur.rowcount

    # ---- economy: temp roles -------------------------------------------

    async def temp_role_add(
        self, guild_id: int, user_id: int, role_id: int, expires_at: int
    ) -> None:
        await self.conn.execute(
            "INSERT INTO temp_roles (guild_id, user_id, role_id, expires_at) VALUES (?, ?, ?, ?)",
            (guild_id, user_id, role_id, expires_at),
        )
        await self.conn.commit()

    async def temp_roles_due(self) -> list[aiosqlite.Row]:
        now = int(time.time())
        async with self.conn.execute(
            "SELECT * FROM temp_roles WHERE expires_at <= ?", (now,)
        ) as cur:
            return list(await cur.fetchall())

    async def temp_role_delete(self, row_id: int) -> None:
        await self.conn.execute("DELETE FROM temp_roles WHERE id = ?", (row_id,))
        await self.conn.commit()

    async def expire_shop_purchase_role(
        self, guild_id: int, user_id: int, role_id: int
    ) -> None:
        await self.conn.execute(
            "DELETE FROM shop_purchases WHERE guild_id = ? AND user_id = ? "
            "AND role_id = ? AND expires_at IS NOT NULL AND expires_at <= ?",
            (guild_id, user_id, role_id, int(time.time())),
        )
        await self.conn.commit()
