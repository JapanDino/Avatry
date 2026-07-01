"""Event publishing, attendance tracking and feedback collection."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
from core import embeds
from core.permissions import is_admin_access

EVENT_MANAGER_ROLE_ID = 1520372868930863104
EVENT_ADMIN_ROLE_ID = 1519305526226452632
EVENT_DRAFT_CHANNEL_ID = 1521271360754548896
EVENT_PUBLISH_CHANNEL_ID = 1521271158857531472
EVENT_PING_ROLE_ID = 1521271052057968731
EVENT_MANAGER_ROLE_NAME = "Eventer"
EVENT_ADMIN_ROLE_NAME = "Event Admin"
EVENT_PING_ROLE_NAME = "Event"
END_REMINDER_SECONDS = 90 * 60
AUTO_STATS_SECONDS = 24 * 60 * 60
EVENT_STATS_TZ = timezone(timedelta(hours=3))


def _event_footer(event_id: int) -> str:
    return f"event_id:{event_id}"


def _event_id_from_message(message: Optional[discord.Message]) -> Optional[int]:
    if message is None or not message.embeds:
        return None
    footer = message.embeds[0].footer.text or ""
    marker = "event_id:"
    if marker not in footer:
        return None
    try:
        return int(footer.split(marker, 1)[1].split()[0])
    except (ValueError, IndexError):
        return None


def _has_event_role(member: discord.Member) -> bool:
    return any(role.id == EVENT_MANAGER_ROLE_ID for role in member.roles)


def _has_event_admin_role(member: discord.Member) -> bool:
    return is_admin_access(member, member.guild) or any(role.id == EVENT_ADMIN_ROLE_ID for role in member.roles)


def _can_use_event_tools(member: discord.Member) -> bool:
    return _has_event_role(member) or _has_event_admin_role(member)


def _can_manage_event(member: discord.Member, event) -> bool:
    return event["author_id"] == member.id or _has_event_admin_role(member)


def _role_ref(guild: Optional[discord.Guild], role_id: int, fallback: str) -> str:
    role = guild.get_role(role_id) if guild is not None else None
    return role.mention if role is not None else f"`{fallback}`"


def _format_seconds(seconds: int) -> str:
    seconds = max(0, seconds)
    hours, rem = divmod(seconds, 3600)
    minutes, _ = divmod(rem, 60)
    if hours:
        return f"{hours} ч {minutes} мин"
    return f"{minutes} мин"


def _event_hosts_period(period: str) -> tuple[Optional[int], Optional[int], str]:
    now = datetime.now(EVENT_STATS_TZ)
    current_week_start = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    if period == "current_week":
        end = current_week_start + timedelta(days=7)
        return int(current_week_start.timestamp()), int(end.timestamp()), "текущая неделя"
    if period == "last_week":
        start = current_week_start - timedelta(days=7)
        return int(start.timestamp()), int(current_week_start.timestamp()), "прошлая неделя"
    return None, None, "всё время"


async def _command_enabled(interaction: discord.Interaction, key: str) -> bool:
    if interaction.guild is None:
        return False
    enabled = await interaction.client.db.is_command_enabled(  # type: ignore[attr-defined]
        interaction.guild.id, key, True
    )
    if not enabled:
        await interaction.response.send_message(
            embed=embeds.error("Эта команда отключена на сервере через `/config`."),
            ephemeral=True,
        )
        return False
    return True


async def _event_workspace_allowed(interaction: discord.Interaction) -> bool:
    if interaction.channel_id != EVENT_DRAFT_CHANNEL_ID:
        await interaction.response.send_message(
            embed=embeds.error(
                f"Команды ивентов работают только в рабочем канале <#{EVENT_DRAFT_CHANNEL_ID}>."
            ),
            ephemeral=True,
        )
        return False
    if not isinstance(interaction.user, discord.Member) or not _can_use_event_tools(interaction.user):
        await interaction.response.send_message(
            embed=embeds.error(
                f"Для управления ивентами нужна роль организатора "
                f"{_role_ref(interaction.guild, EVENT_MANAGER_ROLE_ID, EVENT_MANAGER_ROLE_NAME)} "
                f"или роль полного доступа "
                f"{_role_ref(interaction.guild, EVENT_ADMIN_ROLE_ID, EVENT_ADMIN_ROLE_NAME)}."
            ),
            ephemeral=True,
        )
        return False
    return True


def build_event_embed(row, *, published: bool = False) -> discord.Embed:
    embed = discord.Embed(
        title=f"🎪 {row['title']}",
        description=row["description"],
        color=config.EMBED_COLOR,
        timestamp=None if published else discord.utils.utcnow(),
    )
    embed.add_field(name="Жанр", value=row["genre"], inline=True)
    embed.add_field(name="Длительность", value=row["duration"], inline=True)
    embed.add_field(name="Войс", value=f"<#{row['voice_channel_id']}>", inline=True)
    embed.add_field(name="Ведущий", value=f"<@{row['author_id']}>", inline=True)
    if not published:
        embed.add_field(name="Статус", value="Ожидает утверждения", inline=True)
    if row["image_url"]:
        embed.set_image(url=row["image_url"])
    if not published:
        embed.set_footer(text=_event_footer(row["id"]))
    return embed


async def build_stats_embed(bot: commands.Bot, event_id: int) -> discord.Embed:
    event = await bot.db.get_event(event_id)  # type: ignore[attr-defined]
    if event is None:
        return embeds.error("Ивент не найден.")
    attendees = await bot.db.event_attendees(event_id)  # type: ignore[attr-defined]
    feedback = await bot.db.event_feedback_summary(event_id)  # type: ignore[attr-defined]
    comments = await bot.db.event_feedback_rows(event_id, limit=5)  # type: ignore[attr-defined]

    feedback_count = int(feedback["feedback_count"] or 0)
    avg_event = feedback["avg_event"]
    avg_host = feedback["avg_host"]
    event_rating = f"{avg_event:.2f}/5" if avg_event is not None else "нет оценок"
    host_rating = f"{avg_host:.2f}/5" if avg_host is not None else "нет оценок"

    embed = discord.Embed(
        title=f"📊 Статистика: {event['title']}",
        color=config.EMBED_COLOR,
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Посещения", value=str(len(attendees)), inline=True)
    embed.add_field(name="Ответов формы", value=str(feedback_count), inline=True)
    embed.add_field(name="Оценка мероприятия", value=event_rating, inline=True)
    embed.add_field(name="Оценка ведущего", value=host_rating, inline=True)
    if event["published_at"]:
        end_ts = event["ended_at"] or int(time.time())
        embed.add_field(
            name="Время проведения",
            value=(
                f"С <t:{event['published_at']}:f>\n"
                f"По <t:{end_ts}:f>\n"
                f"Длительность: **{_format_seconds(end_ts - event['published_at'])}**"
            ),
            inline=False,
        )
    if comments:
        lines = []
        for row in comments:
            if row["comment"]:
                lines.append(f"⭐ {row['event_rating']}/5 · 🎙️ {row['host_rating']}/5 — {row['comment'][:140]}")
        if lines:
            embed.add_field(name="Последние комментарии", value="\n".join(lines[:5]), inline=False)
    embed.set_footer(text=_event_footer(event_id))
    return embed


class PublishEventView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Опубликовать", style=discord.ButtonStyle.success,
                       emoji="📣", custom_id="event:publish")
    async def publish(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await publish_event(interaction)


class EventStatsView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Получить статистику", style=discord.ButtonStyle.primary,
                       emoji="📊", custom_id="event:stats")
    async def stats(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        event_id = _event_id_from_message(interaction.message)
        if event_id is None:
            await interaction.response.send_message(
                embed=embeds.error("Не удалось определить ивент."), ephemeral=True
            )
            return
        event = await interaction.client.db.get_event(event_id)  # type: ignore[attr-defined]
        if event is None:
            await interaction.response.send_message(embed=embeds.error("Ивент не найден."), ephemeral=True)
            return
        if not await _event_workspace_allowed(interaction):
            return
        if not isinstance(interaction.user, discord.Member) or not _can_manage_event(interaction.user, event):
            await interaction.response.send_message(
                embed=embeds.error("Статистику по кнопке может получить автор ивента или роль полного доступа."),
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            embed=await build_stats_embed(interaction.client, event_id), ephemeral=True
        )


class EventFeedbackModal(discord.ui.Modal):
    def __init__(self, event_id: int, event_rating: int) -> None:
        super().__init__(title=f"Оценка мероприятия: {event_rating}/5")
        self.event_id = event_id
        self.event_rating = event_rating
        self.host_rating = discord.ui.TextInput(
            label="Оценка ведущего от 1 до 5",
            placeholder="Например: 5",
            min_length=1,
            max_length=1,
            required=True,
        )
        self.comment = discord.ui.TextInput(
            label="Комментарий",
            placeholder="Что понравилось или что улучшить?",
            style=discord.TextStyle.paragraph,
            max_length=500,
            required=False,
        )
        self.add_item(self.host_rating)
        self.add_item(self.comment)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            host_rating = int(str(self.host_rating.value).strip())
        except ValueError:
            await interaction.response.send_message(
                embed=embeds.error("Оценка ведущего должна быть числом от 1 до 5."), ephemeral=True
            )
            return
        if host_rating < 1 or host_rating > 5:
            await interaction.response.send_message(
                embed=embeds.error("Оценка ведущего должна быть от 1 до 5."), ephemeral=True
            )
            return
        event = await interaction.client.db.get_event(self.event_id)  # type: ignore[attr-defined]
        if event is None:
            await interaction.response.send_message(embed=embeds.error("Ивент не найден."), ephemeral=True)
            return
        await interaction.client.db.upsert_event_feedback(  # type: ignore[attr-defined]
            self.event_id,
            event["guild_id"],
            interaction.user.id,
            self.event_rating,
            host_rating,
            str(self.comment.value).strip() or None,
        )
        await interaction.response.send_message(
            embed=embeds.success("Спасибо! Отзыв по мероприятию сохранён."), ephemeral=True
        )


class EventFeedbackView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)
        for rating in range(1, 6):
            self.add_item(EventFeedbackButton(rating))


class EventFeedbackButton(discord.ui.Button):
    def __init__(self, rating: int) -> None:
        super().__init__(
            label=str(rating),
            style=discord.ButtonStyle.secondary,
            emoji="⭐",
            custom_id=f"event:feedback:{rating}",
        )
        self.rating = rating

    async def callback(self, interaction: discord.Interaction) -> None:
        event_id = _event_id_from_message(interaction.message)
        if event_id is None:
            await interaction.response.send_message(
                embed=embeds.error("Не удалось определить ивент."), ephemeral=True
            )
            return
        await interaction.response.send_modal(EventFeedbackModal(event_id, self.rating))


async def publish_event(interaction: discord.Interaction) -> None:
    guild = interaction.guild
    if guild is None or interaction.message is None:
        return
    if not isinstance(interaction.user, discord.Member):
        return
    event = await interaction.client.db.get_event_by_approval_message(interaction.message.id)  # type: ignore[attr-defined]
    if event is None:
        await interaction.response.send_message(embed=embeds.error("Черновик ивента не найден."), ephemeral=True)
        return
    if not _can_manage_event(interaction.user, event):
        await interaction.response.send_message(
            embed=embeds.error("Опубликовать ивент может автор или роль полного доступа."), ephemeral=True
        )
        return
    if not _can_use_event_tools(interaction.user):
        await interaction.response.send_message(
            embed=embeds.error(
                f"Для публикации ивента нужна роль организатора "
                f"{_role_ref(guild, EVENT_MANAGER_ROLE_ID, EVENT_MANAGER_ROLE_NAME)} "
                f"или роль полного доступа "
                f"{_role_ref(guild, EVENT_ADMIN_ROLE_ID, EVENT_ADMIN_ROLE_NAME)}."
            ),
            ephemeral=True,
        )
        return
    if not await _event_workspace_allowed(interaction):
        return
    if event["status"] != "draft":
        await interaction.response.send_message(embed=embeds.warn("Этот ивент уже опубликован."), ephemeral=True)
        return

    publish_channel = guild.get_channel(EVENT_PUBLISH_CHANNEL_ID)
    if not isinstance(publish_channel, discord.TextChannel):
        await interaction.response.send_message(
            embed=embeds.error("Канал публикации ивентов не найден."), ephemeral=True
        )
        return
    ping_role = guild.get_role(EVENT_PING_ROLE_ID)
    if ping_role is None:
        await interaction.response.send_message(
            embed=embeds.error(
                f"Роль для пинга участников `{EVENT_PING_ROLE_NAME}` не найдена. Проверьте ID роли."
            ),
            ephemeral=True,
        )
        return

    public_message = await publish_channel.send(
        content=ping_role.mention,
        embed=build_event_embed(event, published=True),
        allowed_mentions=discord.AllowedMentions(roles=True),
    )
    await interaction.client.db.publish_event(  # type: ignore[attr-defined]
        event["id"], publish_channel.id, public_message.id
    )
    voice = guild.get_channel(event["voice_channel_id"])
    if isinstance(voice, (discord.VoiceChannel, discord.StageChannel)):
        for member in voice.members:
            if not member.bot:
                await interaction.client.db.mark_event_attendance(  # type: ignore[attr-defined]
                    event["id"], guild.id, member.id
                )
    await interaction.message.edit(embed=build_event_embed(event, published=True), view=None)
    await interaction.response.send_message(
        embed=embeds.success(f"Ивент опубликован в {publish_channel.mention}."),
        ephemeral=True,
    )


class Events(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.event_maintenance.start()

    def cog_unload(self) -> None:
        self.event_maintenance.cancel()

    @app_commands.command(name="create_event", description="Создать карточку мероприятия на утверждение")
    @app_commands.rename(
        title="название",
        genre="жанр",
        duration="длительность",
        description="описание",
        voice="войс",
        image_url="картинка",
        image_file="файл_картинки",
    )
    @app_commands.describe(
        title="Название мероприятия",
        genre="Жанр мероприятия",
        duration="Пример: 1 час 30 минут",
        description="Описание мероприятия",
        voice="Голосовой канал или трибуна проведения",
        image_url="URL картинки",
        image_file="Картинка файлом, если нет URL",
    )
    @app_commands.guild_only()
    async def create_event(
        self,
        interaction: discord.Interaction,
        title: str,
        genre: str,
        duration: str,
        description: str,
        voice: discord.VoiceChannel | discord.StageChannel,
        image_url: Optional[str] = None,
        image_file: Optional[discord.Attachment] = None,
    ) -> None:
        guild = interaction.guild
        if guild is None or not isinstance(interaction.user, discord.Member):
            return
        if not await _command_enabled(interaction, "event_create"):
            return
        if not await _event_workspace_allowed(interaction):
            return

        image = image_url
        if image_file is not None:
            image = image_file.url
        event_id = await self.bot.db.create_event(
            guild.id,
            interaction.user.id,
            title[:120],
            genre[:100],
            duration[:100],
            description[:2000],
            image,
            voice.id,
            interaction.channel_id,
        )
        event = await self.bot.db.get_event(event_id)
        assert event is not None
        msg = await interaction.channel.send(embed=build_event_embed(event), view=PublishEventView())
        await self.bot.db.update_event(event_id, approval_message_id=msg.id)
        await interaction.response.send_message(
            embed=embeds.success("Карточка создана. Нажмите «Опубликовать», когда всё готово."),
            ephemeral=True,
        )

    @app_commands.command(name="finish_event", description="Завершить свой активный ивент")
    @app_commands.describe(event_id="ID ивента, если активных несколько")
    @app_commands.guild_only()
    async def finish_event(
        self, interaction: discord.Interaction, event_id: Optional[int] = None
    ) -> None:
        guild = interaction.guild
        if guild is None or not isinstance(interaction.user, discord.Member):
            return
        if not await _command_enabled(interaction, "event_finish"):
            return
        if not await _event_workspace_allowed(interaction):
            return
        if event_id:
            event = await self.bot.db.get_event(event_id)
        elif _has_event_admin_role(interaction.user):
            event = await self.bot.db.active_event_for_guild(guild.id)
        else:
            event = await self.bot.db.active_event_for_author(guild.id, interaction.user.id)
        if event is None or event["guild_id"] != guild.id:
            await interaction.response.send_message(
                embed=embeds.error("Активный ивент не найден."), ephemeral=True
            )
            return
        if not _can_manage_event(interaction.user, event):
            await interaction.response.send_message(
                embed=embeds.error("Завершить ивент может автор или роль полного доступа."), ephemeral=True
            )
            return
        if event["status"] != "published":
            await interaction.response.send_message(
                embed=embeds.warn("Этот ивент уже не активен."), ephemeral=True
            )
            return

        voice = guild.get_channel(event["voice_channel_id"])
        if isinstance(voice, (discord.VoiceChannel, discord.StageChannel)):
            for member in voice.members:
                if not member.bot:
                    await self.bot.db.mark_event_attendance(event["id"], guild.id, member.id)
        await self.bot.db.update_event(event["id"], status="ended", ended_at=int(time.time()))
        delivered, failed = await self._send_feedback(event["id"])

        fresh = await self.bot.db.get_event(event["id"])
        assert fresh is not None
        embed = embeds.base(
            title=f"🏁 Ивент завершён: {fresh['title']}",
            description=(
                f"Форма обратной связи отправлена участникам.\n"
                f"Доставлено ЛС: **{delivered}**, не доставлено: **{failed}**."
            ),
        )
        embed.set_footer(text=_event_footer(fresh["id"]))
        await interaction.response.send_message(embed=embed, view=EventStatsView())

    @app_commands.command(name="event_stats", description="Получить статистику своего ивента")
    @app_commands.describe(event_id="ID ивента; если не указать, будет взят последний ваш ивент")
    @app_commands.guild_only()
    async def event_stats(
        self, interaction: discord.Interaction, event_id: Optional[int] = None
    ) -> None:
        guild = interaction.guild
        if guild is None:
            return
        if not await _command_enabled(interaction, "event_stats"):
            return
        if not await _event_workspace_allowed(interaction):
            return
        if not isinstance(interaction.user, discord.Member):
            return
        if event_id:
            event = await self.bot.db.get_event(event_id)
        elif _has_event_admin_role(interaction.user):
            event = await self.bot.db.latest_event_for_guild(guild.id)
        else:
            event = await self.bot.db.latest_event_for_author(guild.id, interaction.user.id)
        if event is None or event["guild_id"] != guild.id:
            await interaction.response.send_message(embed=embeds.error("Ивент не найден."), ephemeral=True)
            return
        if not _can_manage_event(interaction.user, event):
            await interaction.response.send_message(
                embed=embeds.error("Статистику может получить автор или роль полного доступа."),
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            embed=await build_stats_embed(self.bot, event["id"]), ephemeral=True
        )

    async def _event_hosts_embed(self, guild_id: int, limit: int, period_key: str) -> discord.Embed:
        since, until, period_label = _event_hosts_period(period_key)
        rows = await self.bot.db.event_host_stats(guild_id, int(limit), since=since, until=until)
        if not rows:
            return embeds.warn(f"Статистики ведущих за период «{period_label}» пока нет.")

        lines = []
        for index, row in enumerate(rows, 1):
            avg_event = f"{row['avg_event']:.2f}" if row["avg_event"] is not None else "—"
            avg_host = f"{row['avg_host']:.2f}" if row["avg_host"] is not None else "—"
            lines.append(
                f"**{index}.** <@{row['author_id']}> — "
                f"провёл: **{row['ended_events']}** / всего: {row['total_events']}; "
                f"посещения: **{row['total_attendees']}**; "
                f"оценка ивента: **{avg_event}**; ведущий: **{avg_host}**"
            )
        embed = embeds.base(title="📊 Статистика ведущих", description="\n".join(lines))
        embed.add_field(name="Период", value=period_label, inline=True)
        if since is not None and until is not None:
            embed.add_field(name="Диапазон", value=f"<t:{since}:d> — <t:{until - 1}:d>", inline=True)
        return embed

    @app_commands.command(name="event_hosts", description="Общая статистика ведущих по ивентам")
    @app_commands.rename(limit="лимит")
    @app_commands.describe(limit="Сколько ведущих показать: 1-25")
    @app_commands.guild_only()
    async def event_hosts(
        self,
        interaction: discord.Interaction,
        limit: app_commands.Range[int, 1, 25] = 10,
    ) -> None:
        guild = interaction.guild
        if guild is None or not isinstance(interaction.user, discord.Member):
            return
        if not await _command_enabled(interaction, "event_hosts"):
            return
        if not await _event_workspace_allowed(interaction):
            return
        await interaction.response.send_message(
            embed=await self._event_hosts_embed(guild.id, int(limit), "all"),
            view=EventHostsView(self, int(limit)),
        )

    @app_commands.command(
        name="event_hosts_reset",
        description="Обнулить общую статистику ведущего по ивентам",
    )
    @app_commands.rename(user="ведущий", confirm="подтвердить", reason="причина")
    @app_commands.describe(
        user="Ведущий, чью статистику нужно обнулить",
        confirm="Поставьте true, чтобы подтвердить сброс",
        reason="Необязательная причина сброса",
    )
    @app_commands.guild_only()
    async def event_hosts_reset(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        confirm: bool = False,
        reason: Optional[str] = None,
    ) -> None:
        guild = interaction.guild
        if guild is None or not isinstance(interaction.user, discord.Member):
            return
        if not await _command_enabled(interaction, "event_hosts_reset"):
            return
        if not await _event_workspace_allowed(interaction):
            return
        if not _has_event_admin_role(interaction.user):
            await interaction.response.send_message(
                embed=embeds.error(
                    "Обнулять статистику ведущих может только роль полного доступа к ивентам."
                ),
                ephemeral=True,
            )
            return
        if user.bot:
            await interaction.response.send_message(
                embed=embeds.error("У ботов нет статистики ведущего для обнуления."),
                ephemeral=True,
            )
            return
        previous = await self.bot.db.event_host_stat_reset_get(guild.id, user.id)
        if not confirm:
            previous_text = (
                f"\nПоследний сброс уже был: <t:{previous['reset_at']}:f>."
                if previous is not None
                else ""
            )
            await interaction.response.send_message(
                embed=embeds.warn(
                    f"Вы собираетесь обнулить общую статистику ведущего {user.mention} "
                    "для `/event_hosts`.\n"
                    "Старые мероприятия, посещения и отзывы не удалятся, но перестанут "
                    "учитываться в общей статистике ведущих."
                    f"{previous_text}\n\n"
                    "Чтобы подтвердить, повторите команду с `подтвердить: True`."
                ),
                ephemeral=True,
            )
            return

        reset_at = await self.bot.db.event_host_stat_reset_set(
            guild.id,
            user.id,
            interaction.user.id,
            reason[:300] if reason else None,
        )
        description = (
            f"Статистика ведущего {user.mention} обнулена для общей сводки `/event_hosts`.\n"
            f"Новая точка отсчёта: <t:{reset_at}:f>."
        )
        if reason:
            description += f"\nПричина: {reason[:300]}"
        await interaction.response.send_message(embed=embeds.success(description))

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if member.bot:
            return
        channels = {state.channel.id for state in (before, after) if state.channel is not None}
        for channel_id in channels:
            rows = await self.bot.db.active_events_for_voice(member.guild.id, channel_id)
            for event in rows:
                in_before = before.channel is not None and before.channel.id == channel_id
                in_after = after.channel is not None and after.channel.id == channel_id
                if in_before or in_after:
                    await self.bot.db.mark_event_attendance(event["id"], member.guild.id, member.id)

    async def _send_feedback(self, event_id: int) -> tuple[int, int]:
        event = await self.bot.db.get_event(event_id)
        if event is None:
            return 0, 0
        attendees = await self.bot.db.event_attendees(event_id)
        delivered = 0
        failed = 0
        for attendee in attendees:
            user = self.bot.get_user(attendee["user_id"])
            if user is None:
                try:
                    user = await self.bot.fetch_user(attendee["user_id"])
                except discord.HTTPException:
                    failed += 1
                    continue
            try:
                embed = embeds.base(
                    title=f"Оцените мероприятие: {event['title']}",
                    description=(
                        "Поставьте оценку мероприятию от 1 до 5. "
                        "После выбора откроется короткая форма с оценкой ведущего."
                    ),
                )
                embed.set_footer(text=_event_footer(event_id))
                await user.send(embed=embed, view=EventFeedbackView())
                delivered += 1
            except (discord.HTTPException, discord.Forbidden):
                failed += 1
        await self.bot.db.update_event(event_id, feedback_sent_at=int(time.time()))
        return delivered, failed

    @tasks.loop(minutes=5)
    async def event_maintenance(self) -> None:
        await self.bot.wait_until_ready()
        for event in await self.bot.db.events_needing_end_reminder(END_REMINDER_SECONDS):
            guild = self.bot.get_guild(event["guild_id"])
            author = guild.get_member(event["author_id"]) if guild else None
            if author is None:
                try:
                    author = await self.bot.fetch_user(event["author_id"])
                except discord.HTTPException:
                    author = None
            if author is not None:
                try:
                    await author.send(
                        embed=embeds.warn(
                            f"Ивент **{event['title']}** идёт уже больше 1.5 часов. "
                            "Если он завершился, используйте `/finish_event`."
                        )
                    )
                except (discord.HTTPException, discord.Forbidden):
                    pass
            await self.bot.db.update_event(event["id"], reminded_at=int(time.time()))

        for event in await self.bot.db.events_needing_stats(AUTO_STATS_SECONDS):
            author = self.bot.get_user(event["author_id"])
            if author is None:
                try:
                    author = await self.bot.fetch_user(event["author_id"])
                except discord.HTTPException:
                    author = None
            if author is not None:
                try:
                    await author.send(embed=await build_stats_embed(self.bot, event["id"]))
                except (discord.HTTPException, discord.Forbidden):
                    pass
            await self.bot.db.update_event(event["id"], stats_sent_at=int(time.time()))

    @event_maintenance.before_loop
    async def before_event_maintenance(self) -> None:
        await self.bot.wait_until_ready()


class EventHostsPeriodSelect(discord.ui.Select):
    def __init__(self) -> None:
        super().__init__(
            placeholder="Выберите период топа...",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label="Всё время", value="all", emoji="🏆"),
                discord.SelectOption(label="Текущая неделя", value="current_week", emoji="📅"),
                discord.SelectOption(label="Прошлая неделя", value="last_week", emoji="↩️"),
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or self.view is None:
            return
        view: EventHostsView = self.view  # type: ignore[assignment]
        for option in self.options:
            option.default = option.value == self.values[0]
        await interaction.response.edit_message(
            embed=await view.cog._event_hosts_embed(interaction.guild.id, view.limit, self.values[0]),
            view=view,
        )


class EventHostsView(discord.ui.View):
    def __init__(self, cog: Events, limit: int) -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.limit = limit
        self.add_item(EventHostsPeriodSelect())


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Events(bot))
