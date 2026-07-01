"""Staff/admin application system (recruitment).

Flow:
  1. A panel shows server info + a "Выбери роль" dropdown of open positions.
  2. Picking a position opens a **form modal** with that position's questions.
  3. The submitted application lands in a staff **review channel** with
     **Одобрить / Отказать / История админа** buttons.
  4. Approve grants the position's role to the applicant; Deny asks for a reason.
     "История админа" shows the applicant's past applications and whether they
     were ever approved under this Discord account.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import config
from core import embeds
from core.permissions import is_admin_access, requires

DEFAULT_APP_QUESTIONS = [
    ("Ваше имя", "Введите ваше имя", 1, 0),
    ("Ваш возраст", "Введите ваш возраст", 1, 0),
    ("Почему вы хотите стать частью команды?", "Расскажите о своих мотивах", 1, 1),
    ("Опыт модерации", "Расскажите о вашем опыте модерации", 1, 1),
    ("Сколько времени готовы уделять серверу?", "Укажите количество часов в день", 1, 0),
]


def _dt(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


async def _is_reviewer(db, member: discord.Member) -> bool:
    if is_admin_access(member, member.guild):
        return True
    roles = set(await db.get_app_reviewer_roles(member.guild.id))
    return any(r.id in roles for r in member.roles)


# ---------------------------------------------------------------------------
# Modal
# ---------------------------------------------------------------------------


class AppFormModal(discord.ui.Modal):
    def __init__(self, position: dict | object, questions: list) -> None:
        label = position["label"] if not isinstance(position, dict) else position["label"]
        super().__init__(title=f"Заявка на должность {label}"[:45])
        self.position_id = position["id"]
        self.position_label = position["label"]
        self._labels: list[str] = []
        for q in questions[:5]:
            qlabel = q[0] if isinstance(q, tuple) else q["label"]
            placeholder = q[1] if isinstance(q, tuple) else q["placeholder"]
            required = bool(q[2] if isinstance(q, tuple) else q["required"])
            paragraph = bool(q[3] if isinstance(q, tuple) else q["paragraph"])
            self._labels.append(qlabel)
            self.add_item(discord.ui.TextInput(
                label=qlabel[:45], placeholder=(placeholder or "")[:100] or None,
                required=required,
                style=discord.TextStyle.paragraph if paragraph else discord.TextStyle.short,
                max_length=1000))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        answers = [[label, str(item.value).strip() or "—"]
                   for label, item in zip(self._labels, self.children)]
        await _submit_application(interaction, self.position_id, self.position_label, answers)


async def _submit_application(interaction, position_id, position_label, answers) -> None:
    bot = interaction.client
    guild = interaction.guild
    if guild is None or not isinstance(interaction.user, discord.Member):
        return
    db = bot.db
    cfg = await db.get_app_config(guild.id)
    review = guild.get_channel(cfg["review_channel_id"]) if cfg["review_channel_id"] else None
    if not isinstance(review, discord.TextChannel):
        await interaction.response.send_message(
            embed=embeds.error("Приём заявок не настроен. Админу: `/apply setup`."), ephemeral=True)
        return
    if await db.pending_application_for(guild.id, interaction.user.id):
        await interaction.response.send_message(
            embed=embeds.warn("У вас уже есть заявка на рассмотрении."), ephemeral=True)
        return

    app_id = await db.create_application(guild.id, position_id, position_label,
                                         interaction.user.id, json.dumps(answers))
    embed = discord.Embed(title=f"Заявка на должность {position_label}", color=config.EMBED_COLOR,
                          timestamp=discord.utils.utcnow())
    embed.description = f"Новая заявка от {interaction.user.mention} на должность **{position_label}**."
    embed.set_thumbnail(url=interaction.user.display_avatar.url)
    embed.add_field(name="Пользователь", value=f"{interaction.user.mention} (`{interaction.user.id}`)",
                    inline=False)
    embed.add_field(name="​", value="━━━━━━━━━━━━━━━━━━", inline=False)
    for label, value in answers:
        embed.add_field(name=label, value=value[:1024], inline=False)
    embed.set_footer(text=f"ID заявки: {app_id}")

    recruiter_roles = await db.get_app_reviewer_roles(guild.id)
    mention = " ".join(f"<@&{r}>" for r in recruiter_roles)
    msg = await review.send(content=mention or None, embed=embed, view=AppReviewView(),
                            allowed_mentions=discord.AllowedMentions(roles=True))
    await db.update_application(app_id, message_id=msg.id)
    await interaction.response.send_message(
        embed=embeds.success("Заявка отправлена! Ожидайте решения команды."), ephemeral=True)


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


class PositionSelect(discord.ui.Select):
    def __init__(self, positions: Optional[list] = None) -> None:
        if positions:
            options = [discord.SelectOption(
                label=p["label"][:100], value=str(p["id"]),
                description=(p["description"] or "")[:100] or None, emoji=p["emoji"] or "📋")
                for p in positions]
        else:
            options = [discord.SelectOption(label="—", value="__noop__")]
        super().__init__(placeholder="Выбери роль", options=options, custom_id="app:position")

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.values[0] == "__noop__":
            await interaction.response.send_message(
                embed=embeds.warn("Должности ещё не настроены."), ephemeral=True)
            return
        db = interaction.client.db
        position = await db.get_app_position(int(self.values[0]))
        if position is None:
            await interaction.response.send_message(
                embed=embeds.error("Должность не найдена."), ephemeral=True)
            return
        if await db.pending_application_for(interaction.guild_id, interaction.user.id):
            await interaction.response.send_message(
                embed=embeds.warn("У вас уже есть заявка на рассмотрении."), ephemeral=True)
            return
        questions = await db.get_app_questions(position["id"]) or DEFAULT_APP_QUESTIONS
        await interaction.response.send_modal(AppFormModal(position, questions))


class AppPanelView(discord.ui.View):
    def __init__(self, positions: Optional[list] = None) -> None:
        super().__init__(timeout=None)
        self.add_item(PositionSelect(positions))


class AppReviewView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Одобрить", style=discord.ButtonStyle.success, custom_id="app:approve")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _decide(interaction, approve=True)

    @discord.ui.button(label="Отказать", style=discord.ButtonStyle.danger, custom_id="app:deny")
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        bot = interaction.client
        if not isinstance(interaction.user, discord.Member) or not await _is_reviewer(bot.db, interaction.user):
            await interaction.response.send_message(
                embed=embeds.error("Только рекрутеры могут решать заявки."), ephemeral=True)
            return
        app = await bot.db.get_application_by_message(interaction.message.id)
        if app is None or app["status"] != "pending":
            await interaction.response.send_message(
                embed=embeds.warn("Заявка уже обработана."), ephemeral=True)
            return
        await interaction.response.send_modal(DenyReasonModal())

    @discord.ui.button(label="История админа", style=discord.ButtonStyle.secondary, custom_id="app:history")
    async def history(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        bot = interaction.client
        app = await bot.db.get_application_by_message(interaction.message.id)
        if app is None:
            await interaction.response.send_message(embed=embeds.error("Заявка не найдена."), ephemeral=True)
            return
        apps = await bot.db.get_user_applications(interaction.guild_id, app["applicant_id"])
        approved = [a for a in apps if a["status"] == "approved"]
        embed = embeds.base(title="История админа",
                            description=f"Заявок от <@{app['applicant_id']}>: **{len(apps)}**")
        if approved:
            embed.add_field(name="✅ Был одобрен на",
                            value=", ".join(sorted({a["position_label"] for a in approved})), inline=False)
        else:
            embed.add_field(name="Статус", value="Ранее не был одобрен под этим аккаунтом.", inline=False)
        for a in apps[:10]:
            icon = {"approved": "✅", "denied": "🚫", "pending": "🟡"}.get(a["status"], "•")
            line = f"{icon} **{a['position_label']}** · {_dt(a['created_at'])}"
            if a["reviewer_id"]:
                line += f" · <@{a['reviewer_id']}>"
            embed.add_field(name="​", value=line, inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)


class DenyReasonModal(discord.ui.Modal, title="Отказ по заявке"):
    reason = discord.ui.TextInput(label="Причина отказа", style=discord.TextStyle.paragraph,
                                  required=False, max_length=500, placeholder="Необязательно")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _decide(interaction, approve=False, reason=str(self.reason.value).strip() or None)


async def _decide(interaction: discord.Interaction, approve: bool, reason: Optional[str] = None) -> None:
    bot = interaction.client
    guild = interaction.guild
    if guild is None or not isinstance(interaction.user, discord.Member):
        return
    db = bot.db
    if not await _is_reviewer(db, interaction.user):
        await interaction.response.send_message(
            embed=embeds.error("Только рекрутеры могут решать заявки."), ephemeral=True)
        return
    app = await db.get_application_by_message(interaction.message.id)
    if app is None or app["status"] != "pending":
        await interaction.response.send_message(
            embed=embeds.warn("Заявка уже обработана."), ephemeral=True)
        return

    status = "approved" if approve else "denied"
    await db.update_application(app["id"], status=status, reviewer_id=interaction.user.id,
                               reason=reason, decided_at=int(time.time()))

    applicant = guild.get_member(app["applicant_id"])
    granted_txt = ""
    if approve and applicant is not None:
        position = await db.get_app_position(app["position_id"])
        if position and position["role_id"]:
            role = guild.get_role(position["role_id"])
            if role and role < guild.me.top_role:
                try:
                    await applicant.add_roles(role, reason=f"Заявка одобрена {interaction.user}")
                    granted_txt = f"\nВыдана роль: {role.mention}"
                except discord.HTTPException:
                    granted_txt = "\n⚠️ Не удалось выдать роль (права бота)."

    # Update the review embed with a status line.
    embed = interaction.message.embeds[0]
    if approve:
        embed.add_field(name="Одобрил ✅",
                        value=f"{interaction.user.mention} ({_dt(int(time.time()))})", inline=False)
        embed.colour = discord.Colour(config.SUCCESS_COLOR)
    else:
        val = f"{interaction.user.mention} ({_dt(int(time.time()))})"
        if reason:
            val += f"\nПричина: {reason}"
        embed.add_field(name="Отклонил 🚫", value=val, inline=False)
        embed.colour = discord.Colour(config.ERROR_COLOR)
    view = AppReviewView()
    view.children[0].disabled = True
    view.children[1].disabled = True
    await interaction.response.edit_message(embed=embed, view=view)

    # DM the applicant.
    if applicant is not None:
        if approve:
            dm = embeds.success(f"Ваша заявка на **{app['position_label']}** одобрена!{granted_txt}")
        else:
            dm = embeds.error(f"Ваша заявка на **{app['position_label']}** отклонена."
                              + (f"\nПричина: {reason}" if reason else ""))
        try:
            await applicant.send(embed=dm)
        except discord.HTTPException:
            pass


# ---------------------------------------------------------------------------
# Cog with admin commands
# ---------------------------------------------------------------------------


class Applications(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    apply = app_commands.Group(name="apply", description="Заявки на должности (набор)", guild_only=True)
    position = app_commands.Group(name="position", parent=apply, description="Должности")
    question = app_commands.Group(name="question", parent=apply, description="Вопросы анкеты должности")

    @apply.command(name="setup", description="Создать канал для рассмотрения заявок")
    @requires("apply")
    async def setup_cmd(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        assert guild
        await interaction.response.defer(ephemeral=True)
        ow = {guild.default_role: discord.PermissionOverwrite(view_channel=False),
              guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True)}
        try:
            channel = await guild.create_text_channel("заявки-набор", overwrites=ow,
                                                      reason="Application setup")
        except discord.Forbidden:
            await interaction.followup.send(embed=embeds.error("Нет прав «Управление каналами»."),
                                            ephemeral=True)
            return
        await self.bot.db.update_app_config(guild.id, review_channel_id=channel.id)
        await interaction.followup.send(embed=embeds.success(
            f"Канал заявок создан: {channel.mention}\nДальше: `/apply roles`, `/apply position add`, "
            "`/apply panel`."), ephemeral=True)

    @apply.command(name="roles", description="Роль рекрутеров (кто одобряет/отклоняет)")
    @app_commands.describe(role="Роль с доступом к заявкам")
    @requires("apply")
    async def roles_cmd(self, interaction: discord.Interaction, role: discord.Role) -> None:
        guild = interaction.guild
        assert guild
        cur = await self.bot.db.get_app_reviewer_roles(guild.id)
        await self.bot.db.set_app_reviewer_roles(guild.id, list({*cur, role.id}))
        cfg = await self.bot.db.get_app_config(guild.id)
        review = guild.get_channel(cfg["review_channel_id"]) if cfg["review_channel_id"] else None
        if isinstance(review, discord.TextChannel):
            try:
                await review.set_permissions(role, view_channel=True, send_messages=True,
                                             read_message_history=True)
            except discord.HTTPException:
                pass
        await interaction.response.send_message(
            embed=embeds.success(f"{role.mention} теперь рассматривает заявки."), ephemeral=True)

    @apply.command(name="panel", description="Опубликовать панель заявок")
    @app_commands.describe(channel="Канал", title="Заголовок", text="Текст", image="URL картинки")
    @requires("apply")
    async def panel(self, interaction: discord.Interaction,
                    channel: Optional[discord.TextChannel] = None, title: Optional[str] = None,
                    text: Optional[str] = None, image: Optional[str] = None) -> None:
        guild = interaction.guild
        assert guild
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message(embed=embeds.error("Нужен текстовый канал."),
                                                    ephemeral=True)
            return
        positions = await self.bot.db.get_app_positions(guild.id)
        embed = discord.Embed(title=title or "Набор в команду",
                              description=text or "Выберите должность из списка ниже, чтобы подать заявку.",
                              color=config.EMBED_COLOR)
        if image:
            embed.set_image(url=image)
        try:
            msg = await target.send(embed=embed, view=AppPanelView(positions))
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=embeds.error(f"Нет прав писать в {target.mention}."), ephemeral=True)
            return
        await self.bot.db.update_app_config(guild.id, panel_channel_id=target.id, panel_message_id=msg.id)
        await interaction.response.send_message(
            embed=embeds.success(f"Панель заявок опубликована в {target.mention}."), ephemeral=True)

    # ---- positions ------------------------------------------------------

    @position.command(name="add", description="Добавить должность для набора")
    @app_commands.describe(key="Короткий ключ (латиницей)", label="Название должности",
                           role="Роль, выдаваемая при одобрении", description="Описание", emoji="Эмодзи")
    @requires("apply")
    async def pos_add(self, interaction: discord.Interaction, key: str, label: str,
                      role: Optional[discord.Role] = None, description: Optional[str] = None,
                      emoji: Optional[str] = None) -> None:
        guild = interaction.guild
        assert guild
        key = key.lower().strip()
        if not key.isascii() or not key.isidentifier():
            await interaction.response.send_message(
                embed=embeds.error("Ключ — латиница/цифры/подчёркивания, без пробелов."), ephemeral=True)
            return
        if await self.bot.db.get_app_position_by_key(guild.id, key):
            await interaction.response.send_message(
                embed=embeds.error(f"Должность `{key}` уже есть."), ephemeral=True)
            return
        if role is not None and role >= guild.me.top_role:
            await interaction.response.send_message(
                embed=embeds.error("Роль выше роли бота — он не сможет её выдавать."), ephemeral=True)
            return
        await self.bot.db.add_app_position(guild.id, key, label, description,
                                           role.id if role else None, emoji)
        await interaction.response.send_message(embed=embeds.success(
            f"Должность **{label}** добавлена. Анкета: `/apply question add {key} …`, "
            "затем обновите панель `/apply panel`."), ephemeral=True)

    @position.command(name="remove", description="Удалить должность")
    @app_commands.describe(key="Ключ должности")
    @requires("apply")
    async def pos_remove(self, interaction: discord.Interaction, key: str) -> None:
        n = await self.bot.db.remove_app_position(interaction.guild_id, key.lower().strip())
        await interaction.response.send_message(
            embed=embeds.success("Должность удалена." if n else "Должность не найдена."), ephemeral=True)

    @position.command(name="list", description="Показать должности")
    @requires("apply")
    async def pos_list(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        assert guild
        positions = await self.bot.db.get_app_positions(guild.id)
        if not positions:
            await interaction.response.send_message(
                embed=embeds.base(description="Должности не настроены. `/apply position add`."),
                ephemeral=True)
            return
        embed = embeds.base(title="Должности набора")
        for p in positions:
            role = guild.get_role(p["role_id"]) if p["role_id"] else None
            qn = len(await self.bot.db.get_app_questions(p["id"])) or len(DEFAULT_APP_QUESTIONS)
            embed.add_field(name=f"{p['emoji'] or '📋'} {p['label']} (`{p['key']}`)",
                            value=f"{p['description'] or 'без описания'}\nРоль: "
                                  f"{role.mention if role else '—'} · вопросов: {qn}", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ---- questions ------------------------------------------------------

    @question.command(name="add", description="Добавить вопрос в анкету должности (макс. 5)")
    @app_commands.describe(position_key="Ключ должности", label="Вопрос", placeholder="Подсказка",
                           paragraph="Длинный ответ?", required="Обязательный?")
    @requires("apply")
    async def q_add(self, interaction: discord.Interaction, position_key: str, label: str,
                    placeholder: Optional[str] = None, paragraph: bool = False,
                    required: bool = True) -> None:
        guild = interaction.guild
        assert guild
        pos = await self.bot.db.get_app_position_by_key(guild.id, position_key.lower().strip())
        if pos is None:
            await interaction.response.send_message(embed=embeds.error("Должность не найдена."),
                                                    ephemeral=True)
            return
        if len(await self.bot.db.get_app_questions(pos["id"])) >= 5:
            await interaction.response.send_message(
                embed=embeds.error("Максимум 5 вопросов на анкету."), ephemeral=True)
            return
        await self.bot.db.add_app_question(guild.id, pos["id"], label, placeholder,
                                           int(required), int(paragraph))
        await interaction.response.send_message(
            embed=embeds.success(f"Вопрос добавлен в анкету **{pos['label']}**."), ephemeral=True)

    @question.command(name="clear", description="Очистить анкету должности (вернёт стандартную)")
    @app_commands.describe(position_key="Ключ должности")
    @requires("apply")
    async def q_clear(self, interaction: discord.Interaction, position_key: str) -> None:
        guild = interaction.guild
        assert guild
        pos = await self.bot.db.get_app_position_by_key(guild.id, position_key.lower().strip())
        if pos is None:
            await interaction.response.send_message(embed=embeds.error("Должность не найдена."),
                                                    ephemeral=True)
            return
        await self.bot.db.clear_app_questions(pos["id"])
        await interaction.response.send_message(
            embed=embeds.success("Анкета сброшена к стандартной."), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Applications(bot))
