"""SAMURAI Discord bot — entry point.

Run with:  python bot.py
Configure with a .env file (see .env.example).
"""
from __future__ import annotations

import asyncio
import logging
import sys

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
from core import embeds
from core import owner_reports
from core import version
from core.database import Database
from core.notify import OwnerNotifier
from core.permissions import MissingAccess

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
log = logging.getLogger("samurai")

INITIAL_EXTENSIONS = (
    "cogs.config_cog",
    "cogs.help_cog",
    "cogs.about",
    "cogs.userinfo",
    "cogs.moderation",
    "cogs.roles",
    "cogs.selfroles",
    "cogs.tickets",
    "cogs.antiphishing",
    "cogs.welcome",
    "cogs.levels",
    "cogs.starboard",
    "cogs.economy",
    "cogs.giveaways",
    "cogs.events",
    "cogs.reputation",
    "cogs.analytics",
    "cogs.dashboard",
    "cogs.applications",
    "cogs.adminlog",
)


def build_intents() -> discord.Intents:
    intents = discord.Intents.default()
    intents.members = True          # member info, roles, joins
    intents.message_content = True  # anti-phishing message scanning
    intents.moderation = True       # audit / moderation events
    return intents


class SamuraiBot(commands.Bot):
    def __init__(self) -> None:
        super().__init__(
            command_prefix=commands.when_mentioned_or("+", "k.", "K."),
            intents=build_intents(),
            help_command=None,
            allowed_mentions=discord.AllowedMentions(everyone=False, roles=False),
        )
        self.db = Database(config.DATABASE_PATH)
        self.notifier = OwnerNotifier(self)
        self._startup_notified = False
        self.start_time = discord.utils.utcnow()
        self.app_command_count = 0

    async def setup_hook(self) -> None:
        await self.db.connect()
        log.info("Database ready at %s", config.DATABASE_PATH)
        log.info("Build marker: %s", version.BUILD_MARKER)

        for ext in INITIAL_EXTENSIONS:
            try:
                await self.load_extension(ext)
                log.info("Loaded extension %s", ext)
            except Exception as exc:  # noqa: BLE001 — report any load failure
                log.exception("Failed to load extension %s", ext)
                await self.notifier.report_exception(
                    f"Не загрузилось расширение {ext}", exc, key=f"load:{ext}"
                )

        # Register persistent views so their buttons keep working after a
        # restart (custom_id-based, no state needed at registration).
        from cogs.tickets import (
            ClosedTicketView,
            RequestClaimView,
            TicketChannelView,
            TicketPanelView,
            TicketRatingView,
        )

        self.add_view(TicketPanelView())
        self.add_view(RequestClaimView())
        self.add_view(TicketChannelView())
        self.add_view(ClosedTicketView())
        self.add_view(TicketRatingView())

        from cogs.events import EventFeedbackView, EventStatsView, PublishEventView

        self.add_view(PublishEventView())
        self.add_view(EventStatsView())
        self.add_view(EventFeedbackView())

        from cogs.giveaways import GiveawayView

        self.add_view(GiveawayView())

        from cogs.applications import AppPanelView, AppReviewView

        self.add_view(AppPanelView())
        self.add_view(AppReviewView())

        from cogs.adminlog import AdminLogPanelView

        self.add_view(AdminLogPanelView())

        from cogs.selfroles import SelfRoleSelect, SelfRoleView

        srv = SelfRoleView()
        srv.add_item(SelfRoleSelect())
        self.add_view(srv)

        self.tree.on_error = self.on_app_command_error

        if config.DEV_GUILD_IDS:
            # Instant updates: push commands directly to the dev guild(s)...
            for gid in config.DEV_GUILD_IDS:
                guild = discord.Object(id=gid)
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                self.app_command_count = len(synced)
                log.info("Synced %d commands to dev guild %s", len(synced), gid)
            # ...and clear the global set so commands don't appear twice.
            self.tree.clear_commands(guild=None)
            await self.tree.sync()
            log.info("Cleared global commands (dev mode active)")
        else:
            synced = await self.tree.sync()
            self.app_command_count = len(synced)
            log.info("Synced %d global commands", len(synced))

        if not self.owner_digest.is_running():
            self.owner_digest.start()

    @tasks.loop(hours=24)
    async def owner_digest(self) -> None:
        await owner_reports.send_owner_embeds(
            self,
            await owner_reports.build_overview_embeds(self, title="Daily owner report"),
        )

    @owner_digest.before_loop
    async def before_owner_digest(self) -> None:
        await self.wait_until_ready()
        await asyncio.sleep(24 * 60 * 60)

    async def on_ready(self) -> None:
        log.info("Logged in as %s (id=%s)", self.user, self.user.id if self.user else "?")
        # Presence is managed (and rotated) by the About cog.
        # Notify once per process (on_ready also fires on every reconnect).
        if not self._startup_notified:
            self._startup_notified = True
            await self.notifier.send(
                "Бот запущен",
                level="success",
                fields={
                    "Аккаунт": f"{self.user} (`{self.user.id}`)" if self.user else "?",
                    "Серверов": str(len(self.guilds)),
                    "Команд": str(len(self.tree.get_commands())),
                },
                key="startup",
            )
            await owner_reports.send_owner_embeds(
                self,
                await owner_reports.build_overview_embeds(self, title="Startup owner report"),
            )

    async def on_guild_join(self, guild: discord.Guild) -> None:
        detail = await owner_reports.build_guild_detail_embed(
            self, guild, title="Bot joined a guild"
        )
        await self.notifier.send(
            "Бота добавили на сервер",
            level="info",
            fields={
                "Сервер": f"{guild.name} (`{guild.id}`)",
                "Участников": str(guild.member_count),
                "Владелец": f"<@{guild.owner_id}>",
            },
            key=f"join:{guild.id}",
        )
        await owner_reports.send_owner_embeds(self, [detail])

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        await self.notifier.send(
            "Бота удалили с сервера",
            level="warning",
            fields={"Сервер": f"{guild.name} (`{guild.id}`)"},
            key=f"remove:{guild.id}",
        )
        await owner_reports.send_owner_embeds(
            self,
            await owner_reports.build_overview_embeds(self, title="Guild removed owner report"),
        )

    async def on_command_error(
        self, ctx: commands.Context, error: commands.CommandError
    ) -> None:
        """Errors from prefix (k.) command invocations."""
        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, commands.CheckFailure):
            await ctx.reply(embed=embeds.error("У вас нет прав для этой команды."),
                            mention_author=False)
            return
        if isinstance(error, (commands.MissingRequiredArgument, commands.BadArgument,
                              commands.TooManyArguments)):
            await ctx.reply(embed=embeds.error(f"Неверные аргументы. {error}"),
                            mention_author=False)
            return
        if isinstance(error, commands.NoPrivateMessage):
            await ctx.reply(embed=embeds.error("Команда работает только на сервере."),
                            mention_author=False)
            return
        log.exception("Unhandled prefix command error", exc_info=error)

    async def on_error(self, event_method: str, /, *args, **kwargs) -> None:
        """Catch-all for exceptions raised inside event handlers/listeners."""
        log.exception("Unhandled exception in event %s", event_method)
        import sys

        exc = sys.exc_info()[1]
        if exc is not None:
            await self.notifier.report_exception(
                f"Ошибка в обработчике события `{event_method}`",
                exc,
                key=f"event:{event_method}",
            )

    async def close(self) -> None:
        self.owner_digest.cancel()
        await self.db.close()
        await super().close()

    async def on_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, MissingAccess):
            if error.reason == "guild_only":
                msg = "Эту команду можно использовать только на сервере."
            elif error.reason == "disabled":
                msg = "Эта команда отключена на сервере. Включите её через `/config`."
            else:
                msg = (
                    "У вас нет доступа к этой команде. "
                    "Доступ настраивается владельцем сервера через `/config`."
                )
            await self._respond_error(interaction, msg)
            return

        if isinstance(error, app_commands.CommandOnCooldown):
            await self._respond_error(
                interaction, f"Слишком часто. Попробуйте через {error.retry_after:.1f} с."
            )
            return

        # Generic check failure (e.g. hybrid command guarded by requires_hybrid).
        if isinstance(error, app_commands.CheckFailure):
            await self._respond_error(
                interaction,
                "У вас нет доступа к этой команде. Доступ настраивается через `/config` и `/settings`.",
            )
            return

        if isinstance(error, app_commands.MissingPermissions):
            await self._respond_error(
                interaction, "У бота или у вас недостаточно прав Discord для этого действия."
            )
            return

        log.exception("Unhandled app command error", exc_info=error)
        cmd_name = interaction.command.qualified_name if interaction.command else "?"
        guild_name = interaction.guild.name if interaction.guild else "ЛС"
        await self.notifier.report_exception(
            f"Ошибка в команде /{cmd_name}",
            error,
            context={
                "Команда": f"/{cmd_name}",
                "Пользователь": f"{interaction.user} (`{interaction.user.id}`)",
                "Сервер": guild_name,
                "Канал": getattr(interaction.channel, "mention", "—"),
            },
            key=f"cmd:{cmd_name}:{type(error).__name__}",
        )
        await self._respond_error(
            interaction,
            "Произошла непредвиденная ошибка. Я уже сообщил о ней разработчику.",
        )

    @staticmethod
    async def _respond_error(interaction: discord.Interaction, message: str) -> None:
        embed = embeds.error(message)
        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        except discord.HTTPException:
            pass


async def main() -> None:
    log.warning("Starting K2-SO build marker: %s", version.BUILD_MARKER)
    if not config.DISCORD_TOKEN:
        log.error("DISCORD_TOKEN is not set. Copy .env.example to .env and fill it in.")
        sys.exit(1)
    bot = SamuraiBot()
    async with bot:
        await bot.start(config.DISCORD_TOKEN)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
