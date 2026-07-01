"""Ticket system v3 — request → claim → private ticket → transcript.

Flow (à la .gif bot):
  1. A panel posts a dropdown of info options + a "Создать заявку" button.
  2. The button opens a **form modal** built from configured questions.
  3. On submit, a request card lands in the staff **requests channel** with a
     "Взять заявку" button (visible only to claim roles / admins).
  4. A moderator claims it → the bot creates a **private ticket channel** with
     the requester, the claiming admin and the default reviewer roles. The
     welcome message shows the responsible admin and the form answers.
  5. "Завершить тикет" generates an **HTML transcript**, posts it to the review
     channel and the ticket log, then deletes the channel.

The bot can create all needed channels/categories itself via ``/ticket setup``.
All panel/request/ticket views are persistent (custom_id based).
"""
from __future__ import annotations

import asyncio
from datetime import datetime
import hashlib
import json
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import config
from core import embeds, modlog, transcript
from core.permissions import is_admin_access, requires

DEFAULT_QUESTIONS = [
    ("Ваш запрос", "Опишите, что вам нужно", 1, 1),
    ("Прикрепите ссылку, если есть пример", "Если нету: -", 1, 0),
]

TICKET_STATUS_LABELS = {
    "pending": "🟡 Ожидает модератора",
    "claimed": "✅ Принят",
    "open": "🟢 В работе",
    "waiting_user": "👤 Ждём пользователя",
    "waiting_staff": "🛡️ Ждём персонал",
    "resolved": "☑️ Решён",
    "closed": "🔒 Закрыт",
}

TICKET_STATUS_CHOICES = [
    app_commands.Choice(name="В работе", value="open"),
    app_commands.Choice(name="Ждём пользователя", value="waiting_user"),
    app_commands.Choice(name="Ждём персонал", value="waiting_staff"),
    app_commands.Choice(name="Решён", value="resolved"),
    app_commands.Choice(name="Закрыт", value="closed"),
]


def _status_label(status: str) -> str:
    return TICKET_STATUS_LABELS.get(status, status)


def _discord_time(ts: int | None) -> str:
    return f"<t:{int(ts)}:F>" if ts else "—"


def _footer_stamp(ts: int | None) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(int(ts)).strftime("%d.%m.%Y %H:%M")


def _ticket_public_id(guild_id: int, request_id: int, created_at: int | None) -> str:
    raw = f"{guild_id}:{request_id}:{created_at or 0}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:32]


def _request_footer(guild_id: int, request_id: int, created_at: int | None) -> str:
    return f"Ticket ID: {_ticket_public_id(guild_id, request_id, created_at)} • {_footer_stamp(created_at)}"


def _display_name(user: discord.abc.User | discord.Member | None) -> str:
    if user is None:
        return "—"
    return getattr(user, "display_name", getattr(user, "name", str(user)))


def _request_field_label(label: str) -> str:
    lowered = label.strip().lower()
    if "запрос" in lowered:
        return "Запрос:"
    if "пример" in lowered or "ссылк" in lowered:
        return "Пример:"
    return label if label.endswith(":") else f"{label}:"


def _append_status(embed: discord.Embed, value: str) -> None:
    if len(embed.fields) < 25:
        embed.add_field(name="Статус", value=value[:1024], inline=False)


# ---------------------------------------------------------------------------
# Permission helpers
# ---------------------------------------------------------------------------


async def _is_claimer(db, member: discord.Member) -> bool:
    if is_admin_access(member, member.guild):
        return True
    claim = set(await db.get_ticket_roles(member.guild.id, "claim"))
    return any(r.id in claim for r in member.roles)


async def _reviewer_roles(db, guild: discord.Guild) -> list[discord.Role]:
    ids = await db.get_ticket_roles(guild.id, "reviewer")
    return [r for r in (guild.get_role(i) for i in ids) if r is not None]


# ---------------------------------------------------------------------------
# Modal (the form)
# ---------------------------------------------------------------------------


class TicketFormModal(discord.ui.Modal):
    def __init__(self, questions: list) -> None:
        super().__init__(title="Создание заявки")
        self._labels: list[str] = []
        for q in questions[:5]:
            label = q["label"] if not isinstance(q, tuple) else q[0]
            placeholder = q["placeholder"] if not isinstance(q, tuple) else q[1]
            required = bool(q["required"] if not isinstance(q, tuple) else q[2])
            paragraph = bool(q["paragraph"] if not isinstance(q, tuple) else q[3])
            self._labels.append(label)
            self.add_item(discord.ui.TextInput(
                label=label[:45],
                placeholder=(placeholder or "")[:100] or None,
                required=required,
                style=discord.TextStyle.paragraph if paragraph else discord.TextStyle.short,
                max_length=1000,
            ))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        answers = [[label, str(item.value).strip() or "—"]
                   for label, item in zip(self._labels, self.children)]
        await _submit_request(interaction, answers)


async def _submit_request(interaction: discord.Interaction, answers: list) -> None:
    bot = interaction.client
    guild = interaction.guild
    if guild is None or not isinstance(interaction.user, discord.Member):
        return
    db = bot.db  # type: ignore[attr-defined]
    cfg = await db.get_ticket_config(guild.id)

    requests_channel = guild.get_channel(cfg["requests_channel_id"]) if cfg["requests_channel_id"] else None
    if not isinstance(requests_channel, discord.TextChannel):
        await interaction.response.send_message(
            embed=embeds.error("Система тикетов не настроена. Админу: `/ticket setup`."),
            ephemeral=True)
        return

    existing = await db.open_request_for(guild.id, interaction.user.id)
    if existing is not None:
        await interaction.response.send_message(
            embed=embeds.warn("У вас уже есть активная заявка. Дождитесь её обработки."),
            ephemeral=True)
        return

    number = await db.next_ticket_number(guild.id)
    request_id = await db.create_request(guild.id, number, interaction.user.id, json.dumps(answers))
    req_row = await db.get_request(request_id)
    created_at = int(req_row["created_at"]) if req_row else int(time.time())

    embed = discord.Embed(color=config.EMBED_COLOR)
    embed.set_author(name=_display_name(interaction.user), icon_url=interaction.user.display_avatar.url)
    for label, value in answers:
        embed.add_field(name=_request_field_label(label), value=value[:1024], inline=False)
    embed.set_footer(text=_request_footer(guild.id, request_id, created_at))

    claim_roles = await db.get_ticket_roles(guild.id, "claim")
    mention = " ".join(f"<@&{r}>" for r in claim_roles)
    msg = await requests_channel.send(
        content=mention or None, embed=embed, view=RequestClaimView(),
        allowed_mentions=discord.AllowedMentions(roles=True))
    await db.update_request(request_id, request_message_id=msg.id)

    await _ticket_log(bot, guild, f"📨 Новая заявка #{number:04d} от {interaction.user.mention}")
    await interaction.response.send_message(
        embed=embeds.success("Заявка отправлена! Модератор скоро её рассмотрит."), ephemeral=True)


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


class PanelInfoSelect(discord.ui.Select):
    def __init__(self, info: Optional[list] = None) -> None:
        if info:
            options = [discord.SelectOption(label=i["label"][:100], value=str(i["id"]),
                                            description=(i["description"] or "")[:100] or None,
                                            emoji="👑")
                       for i in info]
        else:
            options = [discord.SelectOption(label="—", value="__noop__")]
        super().__init__(placeholder="ℹ️ Частые вопросы…", options=options, custom_id="tpanel:info")

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.values[0] == "__noop__":
            await interaction.response.send_message(
                embed=embeds.warn("Информация ещё не настроена."), ephemeral=True)
            return
        info = await interaction.client.db.get_panel_info_one(int(self.values[0]))  # type: ignore[attr-defined]
        if info is None:
            await interaction.response.send_message(
                embed=embeds.error("Раздел не найден."), ephemeral=True)
            return
        await interaction.response.send_message(
            embed=embeds.base(title=info["label"], description=info["answer"]), ephemeral=True)


class TicketPanelView(discord.ui.View):
    def __init__(self, info: Optional[list] = None) -> None:
        super().__init__(timeout=None)
        self.add_item(PanelInfoSelect(info))

    @discord.ui.button(label="Создать заявку", style=discord.ButtonStyle.primary,
                       emoji="📝", custom_id="tpanel:create", row=1)
    async def create(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        db = interaction.client.db  # type: ignore[attr-defined]
        rows = await db.get_ticket_questions(interaction.guild_id)
        questions = rows if rows else DEFAULT_QUESTIONS
        await interaction.response.send_modal(TicketFormModal(questions))


class RequestClaimView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Взять заявку", style=discord.ButtonStyle.success,
                       emoji="✅", custom_id="treq:claim")
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _claim_request(interaction)


class TicketRatingModal(discord.ui.Modal):
    def __init__(self, request_id: int, rating: int) -> None:
        super().__init__(title=f"Оценка тикета: {rating}/5")
        self.request_id = request_id
        self.rating = rating
        self.comment = discord.ui.TextInput(
            label="Комментарий",
            placeholder="Что получилось хорошо или что стоит улучшить?",
            required=False,
            style=discord.TextStyle.paragraph,
            max_length=500,
        )
        self.add_item(self.comment)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.client.db.rate_ticket(  # type: ignore[attr-defined]
            self.request_id, self.rating, str(self.comment.value).strip() or None
        )
        await interaction.response.send_message(
            embed=embeds.success("Спасибо! Оценка сохранена."), ephemeral=True
        )


class TicketRatingView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)
        for value in range(1, 6):
            self.add_item(TicketRatingButton(value))


class TicketRatingButton(discord.ui.Button):
    def __init__(self, value: int) -> None:
        super().__init__(
            label=str(value),
            style=discord.ButtonStyle.secondary,
            emoji="⭐",
            custom_id=f"tkt:rate:{value}",
        )
        self.rating = value

    async def callback(self, interaction: discord.Interaction) -> None:
        footer = interaction.message.embeds[0].footer.text if interaction.message and interaction.message.embeds else ""
        marker = "ticket_request:"
        if marker not in footer:
            await interaction.response.send_message(
                embed=embeds.error("Не удалось определить тикет для оценки."), ephemeral=True
            )
            return
        try:
            request_id = int(footer.split(marker, 1)[1].split()[0])
        except (ValueError, IndexError):
            await interaction.response.send_message(
                embed=embeds.error("Не удалось прочитать номер тикета."), ephemeral=True
            )
            return
        req = await interaction.client.db.get_request(request_id)  # type: ignore[attr-defined]
        if req is None:
            await interaction.response.send_message(
                embed=embeds.error("Тикет не найден."), ephemeral=True
            )
            return
        if req["rating"] is not None:
            await interaction.response.send_message(
                embed=embeds.warn("Этот тикет уже оценён."), ephemeral=True
            )
            return
        await interaction.response.send_modal(TicketRatingModal(request_id, self.rating))


class TicketChannelView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Завершить тикет", style=discord.ButtonStyle.danger,
                       emoji="🔒", custom_id="tkt:finish")
    async def finish(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _finish_ticket(interaction)

    @discord.ui.button(label="Добавить администратора", style=discord.ButtonStyle.secondary,
                       emoji="➕", custom_id="tkt:addadmin")
    async def add_admin(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        bot = interaction.client
        req = await bot.db.get_request_by_channel(interaction.channel.id)  # type: ignore[attr-defined]
        if req is None or not isinstance(interaction.user, discord.Member):
            return
        if not await _is_claimer(bot.db, interaction.user):
            await interaction.response.send_message(
                embed=embeds.error("Только персонал может добавлять администраторов."), ephemeral=True)
            return
        await interaction.response.send_message(
            embed=embeds.base(description="Выберите, кого добавить в тикет:"),
            view=AddAdminView(), ephemeral=True)


class ClosedTicketView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Переоткрыть тикет", style=discord.ButtonStyle.success,
                       emoji="🔓", custom_id="tkt:reopen")
    async def reopen(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _reopen_ticket(interaction)


class AddAdminView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=120)
        self.add_item(self._Select())

    class _Select(discord.ui.UserSelect):
        def __init__(self) -> None:
            super().__init__(placeholder="Выберите участника…", min_values=1, max_values=1)

        async def callback(self, interaction: discord.Interaction) -> None:
            member = self.values[0]
            channel = interaction.channel
            try:
                await channel.set_permissions(member, overwrite=discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, read_message_history=True))
            except discord.HTTPException:
                await interaction.response.send_message(
                    embed=embeds.error("Не удалось добавить."), ephemeral=True)
                return
            await interaction.response.edit_message(
                embed=embeds.success(f"{member.mention} добавлен в тикет."), view=None)
            await channel.send(embed=embeds.base(description=f"➕ {member.mention} добавлен в тикет."))


# ---------------------------------------------------------------------------
# Claim / finish logic
# ---------------------------------------------------------------------------


async def _ticket_log(bot, guild: discord.Guild, text: str, embed=None, file=None) -> None:
    cfg = await bot.db.get_ticket_config(guild.id)
    ch = guild.get_channel(cfg["tlog_channel_id"]) if cfg["tlog_channel_id"] else None
    if isinstance(ch, discord.TextChannel):
        try:
            await ch.send(content=text, embed=embed, file=file)
        except discord.HTTPException:
            pass


async def _claim_request(interaction: discord.Interaction) -> None:
    bot = interaction.client
    guild = interaction.guild
    if guild is None or not isinstance(interaction.user, discord.Member):
        return
    db = bot.db  # type: ignore[attr-defined]
    if not await _is_claimer(db, interaction.user):
        await interaction.response.send_message(
            embed=embeds.error("Брать заявки может только персонал поддержки."), ephemeral=True)
        return
    req = None
    # Find the request by its message id.
    async with db.conn.execute(
        "SELECT * FROM ticket_requests WHERE request_message_id = ?", (interaction.message.id,)
    ) as cur:
        req = await cur.fetchone()
    if req is None or req["status"] != "pending":
        await interaction.response.send_message(
            embed=embeds.warn("Эта заявка уже взята или закрыта."), ephemeral=True)
        return

    await interaction.response.defer()
    cfg = await db.get_ticket_config(guild.id)
    category = guild.get_channel(cfg["category_id"]) if cfg["category_id"] else None
    requester = guild.get_member(req["requester_id"]) or await bot.fetch_user(req["requester_id"])
    reviewers = await _reviewer_roles(db, guild)

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True,
                                              manage_channels=True, read_message_history=True),
        interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True,
                                                      read_message_history=True),
    }
    if isinstance(requester, discord.Member):
        overwrites[requester] = discord.PermissionOverwrite(
            view_channel=True, send_messages=True, attach_files=True, read_message_history=True)
    for role in reviewers:
        overwrites[role] = discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True)

    try:
        channel = await guild.create_text_channel(
            name=f"ticket-{req['number']:04d}",
            category=category if isinstance(category, discord.CategoryChannel) else None,
            overwrites=overwrites, reason=f"Тикет #{req['number']} принял {interaction.user}")
    except discord.Forbidden:
        await interaction.followup.send(
            embed=embeds.error("Нет прав «Управление каналами»."), ephemeral=True)
        return

    claimed_at = int(time.time())
    await db.update_request(req["id"], status="open", claimed_by=interaction.user.id,
                            claimed_at=claimed_at, channel_id=channel.id)
    await db.add_ticket_status_event(
        req["id"], guild.id, "claimed", actor_id=interaction.user.id, note="Принят администратором"
    )

    # Welcome message with answers + responsible admin.
    answers = json.loads(req["answers"])
    welcome = discord.Embed(title=f"🎫 Тикет #{req['number']:04d}", color=config.EMBED_COLOR)
    welcome.add_field(name="Ответственный администратор", value=interaction.user.mention, inline=False)
    if reviewers:
        welcome.add_field(name="Проверяющие", value=" ".join(r.mention for r in reviewers), inline=False)
    for label, value in answers:
        welcome.add_field(name=label, value=value[:1024], inline=False)
    welcome.set_footer(text="Обязательно скиньте ссылку на работу или саму работу в этот чат")
    mention = f"{interaction.user.mention}, вы приняли тикет. "
    if isinstance(requester, discord.Member):
        mention += f"{requester.mention}, здесь обсудите все детали работы."
    await channel.send(content=mention, embed=welcome, view=TicketChannelView(),
                       allowed_mentions=discord.AllowedMentions(users=True))

    # Update the request card.
    embed = interaction.message.embeds[0]
    _append_status(
        embed,
        f"✅ Принят администратором {interaction.user.mention}\n"
        f"Время: {_discord_time(claimed_at)}",
    )
    embed.colour = discord.Colour(config.SUCCESS_COLOR)
    disabled = RequestClaimView()
    disabled.children[0].disabled = True
    disabled.children[0].label = "Заявка принята"
    await interaction.message.edit(embed=embed, view=disabled)

    await _ticket_log(bot, guild,
                      f"✅ Заявка #{req['number']:04d} принята {interaction.user.mention} → {channel.mention}")
    await interaction.followup.send(embed=embeds.success(f"Тикет создан: {channel.mention}"),
                                    ephemeral=True)


async def _finish_ticket(interaction: discord.Interaction) -> None:
    bot = interaction.client
    guild = interaction.guild
    channel = interaction.channel
    if guild is None or not isinstance(channel, discord.TextChannel):
        return
    if not isinstance(interaction.user, discord.Member):
        return
    db = bot.db  # type: ignore[attr-defined]
    req = await db.get_request_by_channel(channel.id)
    if req is None:
        await interaction.response.send_message(embed=embeds.error("Это не тикет."), ephemeral=True)
        return
    if req["status"] == "closed":
        await interaction.response.send_message(embed=embeds.warn("Тикет уже закрывается."), ephemeral=True)
        return
    if not await _is_claimer(db, interaction.user):
        await interaction.response.send_message(
            embed=embeds.error("Завершить тикет может только персонал."), ephemeral=True)
        return

    now = int(time.time())
    await db.update_request(
        req["id"], status="closed", closed_at=now, closed_by=interaction.user.id
    )
    await db.add_ticket_status_event(
        req["id"], guild.id, "closed", actor_id=interaction.user.id, note="Тикет закрыт"
    )
    await interaction.response.send_message(
        embed=embeds.base(description="Тикет закрывается. Сохраняю транскрипт и отправляю оценку в ЛС…"))

    number = req["number"]
    created_at = int(req["created_at"])
    requester = guild.get_member(req["requester_id"]) or await bot.fetch_user(req["requester_id"])
    claimer = guild.get_member(req["claimed_by"]) if req["claimed_by"] else None
    supervisor = claimer or interaction.user
    filename, data = await transcript.build_html(
        channel, title=f"тикета #{channel.name}",
        meta=(f"Сервер: {guild.name} • Автор: {requester} • "
              f"Принял: {claimer or '—'} • Завершил: {interaction.user} • "
              f"{discord.utils.utcnow():%Y-%m-%d %H:%M UTC}"))

    cfg = await db.get_ticket_config(guild.id)
    review = guild.get_channel(cfg["transcript_channel_id"]) if cfg["transcript_channel_id"] else None
    log_embed = discord.Embed(
        title=f"📝 Транскрипт тикета #{channel.name}",
        color=config.EMBED_COLOR,
        description=(
            "**Информация**\n"
            f"👤 **Автор:** {_display_name(requester)}\n"
            f"🗓️ **Создан:** {_discord_time(created_at)}\n"
            f"🔒 **Закрыт:** {_discord_time(now)}\n"
            f"👮 **Супервизор:** {supervisor.mention}\n\n"
            "**Оригинальный тикет**\n"
            f"[Перейти к тикету]({channel.jump_url})\n\n"
            "**Транскрипт**\n"
            "⬇️ Файл транскрипта прикреплён к этому сообщению."
        ),
    )
    log_embed.set_footer(text=f".gif Tickets • {_footer_stamp(now)}")
    if isinstance(review, discord.TextChannel):
        try:
            await review.send(embed=log_embed, file=transcript.to_file(filename, data))
        except discord.HTTPException:
            pass
    requests_channel = guild.get_channel(cfg["requests_channel_id"]) if cfg["requests_channel_id"] else None
    if isinstance(requests_channel, discord.TextChannel) and req["request_message_id"]:
        try:
            request_message = await requests_channel.fetch_message(req["request_message_id"])
            if request_message.embeds:
                request_embed = request_message.embeds[0]
                _append_status(
                    request_embed,
                    f"🔒 Закрыт администратором {interaction.user.mention}\n"
                    f"Время: {_discord_time(now)}",
                )
                _append_status(
                    request_embed,
                    f"🔒 Тикет закрыт\n"
                    f"👤 Супервизор: {supervisor.mention}\n"
                    f"🗓️ Время: {_discord_time(now)}",
                )
                request_embed.colour = discord.Colour.dark_grey()
                request_embed.set_footer(text=_request_footer(guild.id, req["id"], created_at))
                await request_message.edit(embed=request_embed, view=None)
        except discord.HTTPException:
            pass
    await _ticket_log(bot, guild, f"Админ {interaction.user.mention} завершил тикет")
    if isinstance(requester, (discord.Member, discord.User)):
        try:
            dm_embed = embeds.base(
                title=f"Тикет #{number:04d} закрыт",
                description=(
                    f"Спасибо за обращение на **{guild.name}**!\n"
                    "Оцените, пожалуйста, работу поддержки."
                ),
            )
            dm_embed.set_footer(text=f"ticket_request:{req['id']}")
            await requester.send(
                embed=dm_embed,
                file=transcript.to_file(filename, data),
                view=TicketRatingView(),
            )
        except (discord.HTTPException, discord.Forbidden):
            pass

    await _lock_ticket_channel(channel, requester)
    try:
        await channel.edit(name=f"closed-{number:04d}", reason=f"Ticket #{number} closed by {interaction.user}")
    except discord.HTTPException:
        pass
    await channel.send(
        embed=embeds.base(
            title=f"🔒 Тикет #{number:04d} закрыт",
            description="Канал сохранён для истории. Персонал может переоткрыть тикет кнопкой ниже.",
        ),
        view=ClosedTicketView(),
    )


async def _lock_ticket_channel(channel: discord.TextChannel, requester) -> None:
    if isinstance(requester, discord.Member):
        try:
            await channel.set_permissions(
                requester,
                overwrite=discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=False,
                    attach_files=False,
                    read_message_history=True,
                ),
            )
        except discord.HTTPException:
            pass


async def _unlock_ticket_channel(channel: discord.TextChannel, requester) -> None:
    if isinstance(requester, discord.Member):
        try:
            await channel.set_permissions(
                requester,
                overwrite=discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    attach_files=True,
                    read_message_history=True,
                ),
            )
        except discord.HTTPException:
            pass


async def _reopen_ticket(interaction: discord.Interaction) -> None:
    bot = interaction.client
    guild = interaction.guild
    channel = interaction.channel
    if guild is None or not isinstance(channel, discord.TextChannel):
        return
    if not isinstance(interaction.user, discord.Member):
        return
    db = bot.db  # type: ignore[attr-defined]
    req = await db.get_request_by_channel(channel.id)
    if req is None:
        await interaction.response.send_message(embed=embeds.error("Это не тикет."), ephemeral=True)
        return
    if not await _is_claimer(db, interaction.user):
        await interaction.response.send_message(
            embed=embeds.error("Переоткрыть тикет может только персонал."), ephemeral=True
        )
        return
    if req["status"] != "closed":
        await interaction.response.send_message(embed=embeds.warn("Тикет уже открыт."), ephemeral=True)
        return

    requester = guild.get_member(req["requester_id"]) or await bot.fetch_user(req["requester_id"])
    await db.update_request(
        req["id"],
        status="open",
        reopened_by=interaction.user.id,
        reopened_at=int(time.time()),
        closed_at=None,
        closed_by=None,
    )
    await db.add_ticket_status_event(
        req["id"], guild.id, "open", actor_id=interaction.user.id, note="Тикет переоткрыт"
    )
    await _unlock_ticket_channel(channel, requester)
    try:
        await channel.edit(name=f"ticket-{req['number']:04d}", reason=f"Ticket #{req['number']} reopened")
    except discord.HTTPException:
        pass
    await interaction.response.send_message(
        embed=embeds.success(f"Тикет #{req['number']:04d} переоткрыт.")
    )
    await _ticket_log(bot, guild, f"🔓 Тикет #{req['number']:04d} переоткрыт {interaction.user.mention}")


# ---------------------------------------------------------------------------
# Cog with admin commands
# ---------------------------------------------------------------------------


class Tickets(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    ticket = app_commands.Group(name="ticket", description="Система тикетов", guild_only=True)
    questions = app_commands.Group(name="questions", parent=ticket, description="Вопросы формы заявки")
    info = app_commands.Group(name="info", parent=ticket, description="Частые вопросы на панели")

    async def _sync_staff_perms(self, guild: discord.Guild) -> None:
        cfg = await self.bot.db.get_ticket_config(guild.id)
        claim = await self.bot.db.get_ticket_roles(guild.id, "claim")
        reviewer = await self.bot.db.get_ticket_roles(guild.id, "reviewer")
        role_ids = set(claim) | set(reviewer)
        for key in ("requests_channel_id", "tlog_channel_id", "transcript_channel_id"):
            ch = guild.get_channel(cfg[key]) if cfg[key] else None
            if not isinstance(ch, discord.TextChannel):
                continue
            ow = {guild.default_role: discord.PermissionOverwrite(view_channel=False),
                  guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True)}
            for rid in role_ids:
                role = guild.get_role(rid)
                if role:
                    ow[role] = discord.PermissionOverwrite(view_channel=True,
                                                           send_messages=(key == "requests_channel_id"),
                                                           read_message_history=True)
            try:
                await ch.edit(overwrites=ow)
            except discord.HTTPException:
                pass

    @ticket.command(name="setup", description="Создать каналы и категорию тикетов автоматически")
    @requires("ticket_config")
    async def setup_cmd(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        assert guild
        await interaction.response.defer(ephemeral=True)
        try:
            category = await guild.create_category("🎫 Тикеты", reason="Ticket setup")
            staff_ow = {guild.default_role: discord.PermissionOverwrite(view_channel=False),
                        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True)}
            requests = await guild.create_text_channel("заявки", category=category, overwrites=staff_ow)
            tlog = await guild.create_text_channel("история-тикетов", category=category, overwrites=staff_ow)
            review = await guild.create_text_channel("проверка-тикетов", category=category, overwrites=staff_ow)
        except discord.Forbidden:
            await interaction.followup.send(
                embed=embeds.error("Нет прав «Управление каналами»."), ephemeral=True)
            return
        await self.bot.db.update_ticket_config(
            guild.id, category_id=category.id, requests_channel_id=requests.id,
            tlog_channel_id=tlog.id, transcript_channel_id=review.id)
        await interaction.followup.send(embed=embeds.success(
            f"Готово! Создано:\n• Категория {category.mention if hasattr(category,'mention') else category.name}\n"
            f"• Заявки: {requests.mention}\n• История: {tlog.mention}\n• Проверка: {review.mention}\n\n"
            "Дальше: задайте роли `/ticket roles`, опубликуйте панель `/ticket panel`."), ephemeral=True)

    @ticket.command(name="panel", description="Опубликовать панель создания заявок")
    @app_commands.describe(channel="Канал для панели (по умолчанию — текущий)",
                           text="Текст панели (необязательно)")
    @requires("ticket_panel")
    async def panel(self, interaction: discord.Interaction,
                    channel: Optional[discord.TextChannel] = None, text: Optional[str] = None) -> None:
        guild = interaction.guild
        assert guild
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message(embed=embeds.error("Нужен текстовый канал."),
                                                    ephemeral=True)
            return
        info = await self.bot.db.get_panel_info(guild.id)
        desc = text or ("Здесь вы можете быстро и удобно подать заявку.\n"
                        "Выберите вопрос из списка ниже или нажмите **«Создать заявку»**.")
        embed = discord.Embed(title="🎫 Ticket", description=desc, color=config.EMBED_COLOR)
        try:
            msg = await target.send(embed=embed, view=TicketPanelView(info))
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=embeds.error(f"Нет прав писать в {target.mention}."), ephemeral=True)
            return
        await self.bot.db.update_ticket_config(guild.id, panel_channel_id=target.id,
                                               panel_message_id=msg.id)
        await interaction.response.send_message(
            embed=embeds.success(f"Панель опубликована в {target.mention}."), ephemeral=True)

    @ticket.command(name="roles", description="Настроить роли: кто берёт заявки и кто проверяющий")
    @app_commands.describe(claim_role="Роль, которая видит заявки и берёт тикеты",
                           reviewer_role="Роль-проверяющий (доступ в каждый тикет по умолчанию)")
    @requires("ticket_config")
    async def roles_cmd(self, interaction: discord.Interaction,
                        claim_role: Optional[discord.Role] = None,
                        reviewer_role: Optional[discord.Role] = None) -> None:
        guild = interaction.guild
        assert guild
        if claim_role is None and reviewer_role is None:
            claim = await self.bot.db.get_ticket_roles(guild.id, "claim")
            rev = await self.bot.db.get_ticket_roles(guild.id, "reviewer")
            await interaction.response.send_message(embed=embeds.base(
                title="Роли тикетов",
                description=(f"**Берут заявки:** {' '.join(f'<@&{r}>' for r in claim) or 'не заданы'}\n"
                            f"**Проверяющие:** {' '.join(f'<@&{r}>' for r in rev) or 'не заданы'}")),
                ephemeral=True)
            return
        if claim_role is not None:
            cur = await self.bot.db.get_ticket_roles(guild.id, "claim")
            new = list({*cur, claim_role.id})
            await self.bot.db.set_ticket_roles(guild.id, "claim", new)
        if reviewer_role is not None:
            cur = await self.bot.db.get_ticket_roles(guild.id, "reviewer")
            new = list({*cur, reviewer_role.id})
            await self.bot.db.set_ticket_roles(guild.id, "reviewer", new)
        await self._sync_staff_perms(guild)
        await interaction.response.send_message(
            embed=embeds.success("Роли обновлены, доступ к служебным каналам выдан."), ephemeral=True)

    @ticket.command(name="close", description="Завершить текущий тикет")
    @requires("ticket_close")
    async def close_cmd(self, interaction: discord.Interaction) -> None:
        await _finish_ticket(interaction)

    @ticket.command(name="reopen", description="Переоткрыть закрытый тикет")
    @requires("ticket_reopen")
    async def reopen_cmd(self, interaction: discord.Interaction) -> None:
        await _reopen_ticket(interaction)

    @ticket.command(name="status", description="Изменить статус текущего тикета")
    @app_commands.describe(status="Новый статус", note="Комментарий для истории")
    @app_commands.choices(status=TICKET_STATUS_CHOICES)
    @requires("ticket_status")
    async def status_cmd(
        self,
        interaction: discord.Interaction,
        status: app_commands.Choice[str],
        note: Optional[str] = None,
    ) -> None:
        guild = interaction.guild
        channel = interaction.channel
        if guild is None or not isinstance(channel, discord.TextChannel):
            return
        if not isinstance(interaction.user, discord.Member):
            return
        req = await self.bot.db.get_request_by_channel(channel.id)
        if req is None:
            await interaction.response.send_message(embed=embeds.error("Это не тикет."), ephemeral=True)
            return
        if not await _is_claimer(self.bot.db, interaction.user):
            await interaction.response.send_message(
                embed=embeds.error("Менять статус тикета может только персонал."), ephemeral=True
            )
            return
        if req["status"] == "closed" and status.value != "closed":
            await interaction.response.send_message(
                embed=embeds.warn("Закрытый тикет сначала нужно переоткрыть."), ephemeral=True
            )
            return
        status_at = int(time.time())
        await self.bot.db.set_ticket_status(
            req["id"], guild.id, status.value, actor_id=interaction.user.id, note=note
        )
        await interaction.response.send_message(
            embed=embeds.success(f"Статус тикета: **{_status_label(status.value)}**.")
        )
        cfg = await self.bot.db.get_ticket_config(guild.id)
        requests_channel = guild.get_channel(cfg["requests_channel_id"]) if cfg["requests_channel_id"] else None
        if isinstance(requests_channel, discord.TextChannel) and req["request_message_id"]:
            try:
                request_message = await requests_channel.fetch_message(req["request_message_id"])
                if request_message.embeds:
                    request_embed = request_message.embeds[0]
                    line = f"{_status_label(status.value)} поставил {interaction.user.mention}"
                    if note:
                        line += f"\nКомментарий: {note[:800]}"
                    line += f"\nВремя: {_discord_time(status_at)}"
                    _append_status(request_embed, line)
                    await request_message.edit(embed=request_embed)
            except discord.HTTPException:
                pass
        await _ticket_log(
            self.bot,
            guild,
            f"🏷️ Тикет #{req['number']:04d}: статус `{status.value}` поставил {interaction.user.mention}",
        )

    # ---- questions ------------------------------------------------------

    @questions.command(name="add", description="Добавить вопрос в форму заявки (макс. 5)")
    @app_commands.describe(label="Текст вопроса", placeholder="Подсказка в поле",
                           paragraph="Длинный ответ?", required="Обязательный?")
    @requires("ticket_config")
    async def q_add(self, interaction: discord.Interaction, label: str,
                    placeholder: Optional[str] = None, paragraph: bool = False,
                    required: bool = True) -> None:
        guild = interaction.guild
        assert guild
        if len(await self.bot.db.get_ticket_questions(guild.id)) >= 5:
            await interaction.response.send_message(
                embed=embeds.error("Максимум 5 вопросов (лимит модального окна Discord)."),
                ephemeral=True)
            return
        await self.bot.db.add_ticket_question(guild.id, label, placeholder, int(required), int(paragraph))
        await interaction.response.send_message(
            embed=embeds.success(f"Вопрос добавлен: **{label}**"), ephemeral=True)

    @questions.command(name="clear", description="Удалить все вопросы (вернёт стандартные)")
    @requires("ticket_config")
    async def q_clear(self, interaction: discord.Interaction) -> None:
        await self.bot.db.clear_ticket_questions(interaction.guild_id)
        await interaction.response.send_message(
            embed=embeds.success("Вопросы сброшены к стандартным."), ephemeral=True)

    @questions.command(name="list", description="Показать вопросы формы")
    @requires("ticket_config")
    async def q_list(self, interaction: discord.Interaction) -> None:
        rows = await self.bot.db.get_ticket_questions(interaction.guild_id)
        rows = rows or DEFAULT_QUESTIONS
        lines = []
        for i, q in enumerate(rows, 1):
            label = q["label"] if not isinstance(q, tuple) else q[0]
            lines.append(f"**{i}.** {label}")
        await interaction.response.send_message(
            embed=embeds.base(title="Вопросы формы", description="\n".join(lines)), ephemeral=True)

    # ---- panel info -----------------------------------------------------

    @info.command(name="add", description="Добавить пункт «частого вопроса» на панель")
    @app_commands.describe(label="Заголовок пункта", answer="Ответ", description="Короткое описание")
    @requires("ticket_config")
    async def info_add(self, interaction: discord.Interaction, label: str, answer: str,
                       description: Optional[str] = None) -> None:
        await self.bot.db.add_panel_info(interaction.guild_id, label, description, answer)
        await interaction.response.send_message(
            embed=embeds.success(f"Пункт добавлен: **{label}**. Обновите панель `/ticket panel`."),
            ephemeral=True)

    @info.command(name="clear", description="Удалить все частые вопросы")
    @requires("ticket_config")
    async def info_clear(self, interaction: discord.Interaction) -> None:
        await self.bot.db.clear_panel_info(interaction.guild_id)
        await interaction.response.send_message(embed=embeds.success("Частые вопросы удалены."),
                                                ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Tickets(bot))
