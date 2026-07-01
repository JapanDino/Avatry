"""Moderation suite (hybrid: every command works as /ban and k.ban).

Commands: ban, unban, tempban, kick, timeout, untimeout, warn, warnings,
clearwarns, case, history, note, notes, purge, slowmode, lock, unlock, and the
warnpunish escalation-ladder config. Every action is recorded as a numbered
case in ``mod_cases`` and mirrored to the moderation log channel.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core import embeds, modlog
from core.permissions import requires_hybrid


def _ts(epoch: int) -> str:
    return discord.utils.format_dt(datetime.fromtimestamp(epoch, tz=timezone.utc), style="R")


class Moderation(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.tempban_check.start()

    async def cog_unload(self) -> None:
        self.tempban_check.cancel()

    @tasks.loop(minutes=1)
    async def tempban_check(self) -> None:
        for case in await self.bot.db.due_tempbans():
            guild = self.bot.get_guild(case["guild_id"])
            if guild is None:
                await self.bot.db.deactivate_case(case["id"])
                continue
            try:
                user = await self.bot.fetch_user(case["user_id"])
                await guild.unban(user, reason="Срок временного бана истёк")
                await modlog.send_modlog(
                    self.bot, guild.id,
                    modlog.action_embed("♻️ Авто-разбан", user, self.bot.user,
                                        f"истёк tempban (кейс #{case['case_number']})"),
                )
            except discord.NotFound:
                pass
            except discord.HTTPException:
                continue
            await self.bot.db.deactivate_case(case["id"])

    @tempban_check.before_loop
    async def _before_tempban(self) -> None:
        await self.bot.wait_until_ready()

    async def _dm(self, member: discord.Member, embed: discord.Embed) -> None:
        try:
            await member.send(embed=embed)
        except discord.HTTPException:
            pass

    async def _reply(self, ctx: commands.Context, embed: discord.Embed) -> None:
        await ctx.reply(embed=embed, mention_author=False)

    async def _log_case(self, guild, title, target, moderator, reason, action, *,
                        expires_at=None, extra=None) -> int:
        case_no = await self.bot.db.add_case(guild.id, target.id, moderator.id, action,
                                             reason, expires_at)
        fields = {"Кейс": f"#{case_no}", **(extra or {})}
        await modlog.send_modlog(self.bot, guild.id,
                                 modlog.action_embed(title, target, moderator, reason, fields))
        return case_no

    # ---- ban / unban / tempban -----------------------------------------

    @commands.hybrid_command(name="ban", aliases=["бан"], description="Забанить пользователя")
    @app_commands.describe(user="Кого забанить", reason="Причина бана",
                           delete_days="Сколько дней его недавних сообщений стереть (0–7)")
    @commands.guild_only()
    @requires_hybrid("ban")
    async def ban(self, ctx: commands.Context, user: discord.Member,
                  reason: Optional[str] = None,
                  delete_days: commands.Range[int, 0, 7] = 0) -> None:
        guild = ctx.guild
        assert guild and isinstance(ctx.author, discord.Member)
        block = modlog.hierarchy_block(guild, ctx.author, user)
        if block:
            await self._reply(ctx, embeds.error(block))
            return
        reason = reason or "не указана"
        await self._dm(user, embeds.error(f"Вы забанены на **{guild.name}**.\nПричина: {reason}"))
        try:
            await guild.ban(user, reason=f"{ctx.author}: {reason}",
                            delete_message_seconds=delete_days * 86400)
        except discord.Forbidden:
            await self._reply(ctx, embeds.error("Недостаточно прав, чтобы забанить этого пользователя."))
            return
        case_no = await self._log_case(guild, "🔨 Бан", user, ctx.author, reason, "ban")
        await self._reply(ctx, embeds.success(
            f"{user.mention} забанен (кейс #{case_no}). Причина: {reason}"))

    @commands.hybrid_command(name="tempban", aliases=["вбан"],
                             description="Временный бан с авто-разбаном")
    @app_commands.describe(user="Кого забанить", duration="Срок: 1h, 1d, 7d", reason="Причина")
    @commands.guild_only()
    @requires_hybrid("tempban")
    async def tempban(self, ctx: commands.Context, user: discord.Member, duration: str,
                      reason: Optional[str] = None) -> None:
        guild = ctx.guild
        assert guild and isinstance(ctx.author, discord.Member)
        block = modlog.hierarchy_block(guild, ctx.author, user)
        if block:
            await self._reply(ctx, embeds.error(block))
            return
        seconds = modlog.parse_duration(duration)
        if not seconds or seconds <= 0:
            await self._reply(ctx, embeds.error("Неверный срок. Примеры: `12h`, `1d`, `7d`."))
            return
        reason = reason or "не указана"
        until = int(datetime.now(tz=timezone.utc).timestamp()) + seconds
        await self._dm(user, embeds.error(
            f"Вы временно забанены на **{guild.name}** до {_ts(until)}.\nПричина: {reason}"))
        try:
            await guild.ban(user, reason=f"{ctx.author} (tempban): {reason}", delete_message_seconds=0)
        except discord.Forbidden:
            await self._reply(ctx, embeds.error("Недостаточно прав для бана."))
            return
        case_no = await self._log_case(guild, "⏳ Временный бан", user, ctx.author, reason, "tempban",
                                       expires_at=until, extra={"Разбан": _ts(until)})
        await self._reply(ctx, embeds.success(
            f"{user.mention} забанен до {_ts(until)} (кейс #{case_no}). Причина: {reason}"))

    @commands.hybrid_command(name="unban", aliases=["разбан"],
                             description="Разбанить пользователя по ID")
    @app_commands.describe(user_id="ID пользователя", reason="Причина")
    @commands.guild_only()
    @requires_hybrid("unban")
    async def unban(self, ctx: commands.Context, user_id: str, reason: Optional[str] = None) -> None:
        guild = ctx.guild
        assert guild and isinstance(ctx.author, discord.Member)
        if not user_id.isdigit():
            await self._reply(ctx, embeds.error("ID должен быть числом."))
            return
        reason = reason or "не указана"
        try:
            user = await self.bot.fetch_user(int(user_id))
            await guild.unban(user, reason=f"{ctx.author}: {reason}")
        except discord.NotFound:
            await self._reply(ctx, embeds.error("Пользователь не найден в банах."))
            return
        case_no = await self._log_case(guild, "♻️ Разбан", user, ctx.author, reason, "unban")
        await self._reply(ctx, embeds.success(f"{user} разбанен (кейс #{case_no}). Причина: {reason}"))

    # ---- kick -----------------------------------------------------------

    @commands.hybrid_command(name="kick", aliases=["кик"], description="Кикнуть пользователя")
    @app_commands.describe(user="Кого кикнуть", reason="Причина")
    @commands.guild_only()
    @requires_hybrid("kick")
    async def kick(self, ctx: commands.Context, user: discord.Member,
                   reason: Optional[str] = None) -> None:
        guild = ctx.guild
        assert guild and isinstance(ctx.author, discord.Member)
        block = modlog.hierarchy_block(guild, ctx.author, user)
        if block:
            await self._reply(ctx, embeds.error(block))
            return
        reason = reason or "не указана"
        await self._dm(user, embeds.warn(f"Вас кикнули с **{guild.name}**.\nПричина: {reason}"))
        try:
            await user.kick(reason=f"{ctx.author}: {reason}")
        except discord.Forbidden:
            await self._reply(ctx, embeds.error("Недостаточно прав, чтобы кикнуть."))
            return
        case_no = await self._log_case(guild, "👢 Кик", user, ctx.author, reason, "kick")
        await self._reply(ctx, embeds.success(
            f"{user.mention} кикнут (кейс #{case_no}). Причина: {reason}"))

    # ---- timeout --------------------------------------------------------

    @commands.hybrid_command(name="timeout", aliases=["mute", "мут"],
                             description="Выдать мут (тайм-аут)")
    @app_commands.describe(user="Кого", duration="30s, 10m, 2h, 1d (≤28д)", reason="Причина")
    @commands.guild_only()
    @requires_hybrid("timeout")
    async def timeout(self, ctx: commands.Context, user: discord.Member, duration: str,
                      reason: Optional[str] = None) -> None:
        guild = ctx.guild
        assert guild and isinstance(ctx.author, discord.Member)
        block = modlog.hierarchy_block(guild, ctx.author, user)
        if block:
            await self._reply(ctx, embeds.error(block))
            return
        seconds = modlog.parse_duration(duration)
        if not seconds or seconds <= 0:
            await self._reply(ctx, embeds.error(
                "Неверная длительность. Примеры: `30s`, `10m`, `2h`, `1d`."))
            return
        seconds = min(seconds, modlog.MAX_TIMEOUT_SECONDS)
        reason = reason or "не указана"
        until = discord.utils.utcnow() + timedelta(seconds=seconds)
        try:
            await user.timeout(until, reason=f"{ctx.author}: {reason}")
        except discord.Forbidden:
            await self._reply(ctx, embeds.error("Недостаточно прав, чтобы замутить."))
            return
        case_no = await self._log_case(guild, "🔇 Тайм-аут", user, ctx.author, reason, "mute",
                                       expires_at=int(until.timestamp()),
                                       extra={"До": discord.utils.format_dt(until, style="f")})
        await self._reply(ctx, embeds.success(
            f"{user.mention} замучен до {discord.utils.format_dt(until, style='R')} "
            f"(кейс #{case_no}). Причина: {reason}"))

    @commands.hybrid_command(name="untimeout", aliases=["unmute", "размут"], description="Снять мут")
    @app_commands.describe(user="С кого снять мут", reason="Причина")
    @commands.guild_only()
    @requires_hybrid("untimeout")
    async def untimeout(self, ctx: commands.Context, user: discord.Member,
                        reason: Optional[str] = None) -> None:
        guild = ctx.guild
        assert guild and isinstance(ctx.author, discord.Member)
        if not user.is_timed_out():
            await self._reply(ctx, embeds.warn("У пользователя нет активного мута."))
            return
        reason = reason or "не указана"
        try:
            await user.timeout(None, reason=f"{ctx.author}: {reason}")
        except discord.Forbidden:
            await self._reply(ctx, embeds.error("Недостаточно прав."))
            return
        case_no = await self._log_case(guild, "🔊 Снятие мута", user, ctx.author, reason, "unmute")
        await self._reply(ctx, embeds.success(f"Мут снят с {user.mention} (кейс #{case_no})."))

    # ---- warnings + escalation -----------------------------------------

    @commands.hybrid_command(name="warn", aliases=["варн", "пред"],
                             description="Выдать предупреждение")
    @app_commands.describe(user="Кому", reason="Причина")
    @commands.guild_only()
    @requires_hybrid("warn")
    async def warn(self, ctx: commands.Context, user: discord.Member, *, reason: str) -> None:
        guild = ctx.guild
        assert guild and isinstance(ctx.author, discord.Member)
        block = modlog.hierarchy_block(guild, ctx.author, user)
        if block:
            await self._reply(ctx, embeds.error(block))
            return
        cfg = await self.bot.db.get_guild_config(guild.id)
        expires_at = None
        if cfg["warn_expiry_days"]:
            expires_at = int(datetime.now(tz=timezone.utc).timestamp()) + cfg["warn_expiry_days"] * 86400
        case_no = await self._log_case(guild, "⚠️ Предупреждение", user, ctx.author, reason, "warn",
                                       expires_at=expires_at)
        count = await self.bot.db.active_warn_count(guild.id, user.id)
        await self._dm(user, embeds.warn(
            f"Предупреждение на **{guild.name}**.\nПричина: {reason}\nАктивных предупреждений: {count}"))
        punish = await self.bot.db.warn_punishment_for(guild.id, count)
        applied_txt = ""
        if punish is not None:
            try:
                label = await modlog.apply_action(
                    guild, user, punish["action"], duration_seconds=punish["duration_seconds"],
                    reason=f"Авто-наказание за {count} предупреждений")
                applied_txt = f"\n⚖️ Авто-наказание ({count} варнов): **{label}**"
                await self._log_case(guild, "⚖️ Авто-наказание", user, self.bot.user,
                                     f"{count} предупреждений", punish["action"])
            except discord.Forbidden:
                applied_txt = "\n⚖️ Авто-наказание не применено: у бота нет прав."
        await self._reply(ctx, embeds.success(
            f"{user.mention} получил предупреждение (кейс #{case_no}). "
            f"Активных: **{count}**. Причина: {reason}{applied_txt}"))

    @commands.hybrid_command(name="warnings", aliases=["warns", "варны"],
                             description="Показать активные предупреждения")
    @app_commands.describe(user="Чьи предупреждения")
    @commands.guild_only()
    @requires_hybrid("warnings")
    async def warnings(self, ctx: commands.Context, user: discord.Member) -> None:
        guild = ctx.guild
        cases = [c for c in await self.bot.db.get_user_cases(guild.id, user.id)
                 if c["action"] == "warn" and c["active"]]
        if not cases:
            await self._reply(ctx, embeds.base(
                description=f"У {user.mention} нет активных предупреждений."))
            return
        embed = embeds.base(title=f"Предупреждения — {user.display_name} ({len(cases)})")
        for c in cases[:25]:
            exp = f" · истекает {_ts(c['expires_at'])}" if c["expires_at"] else ""
            embed.add_field(name=f"Кейс #{c['case_number']} · {_ts(c['created_at'])}{exp}",
                            value=f"<@{c['moderator_id']}>: {c['reason'] or 'без причины'}", inline=False)
        await self._reply(ctx, embed)

    @commands.hybrid_command(name="clearwarns", aliases=["счистьварны"],
                             description="Снять все предупреждения пользователя")
    @app_commands.describe(user="С кого снять")
    @commands.guild_only()
    @requires_hybrid("clearwarns")
    async def clearwarns(self, ctx: commands.Context, user: discord.Member) -> None:
        guild = ctx.guild
        assert guild and isinstance(ctx.author, discord.Member)
        count = await self.bot.db.clear_warns(guild.id, user.id)
        await self._reply(ctx, embeds.success(f"Снято предупреждений у {user.mention}: **{count}**."))
        if count:
            await self._log_case(guild, "🧹 Снятие предупреждений", user, ctx.author,
                                 f"снято {count}", "note")

    # ---- case history ---------------------------------------------------

    @commands.hybrid_command(name="case", aliases=["кейс"], description="Показать кейс по номеру")
    @app_commands.describe(number="Номер кейса")
    @commands.guild_only()
    @requires_hybrid("case")
    async def case(self, ctx: commands.Context, number: int) -> None:
        guild = ctx.guild
        c = await self.bot.db.get_case(guild.id, number)
        if c is None:
            await self._reply(ctx, embeds.error(f"Кейс #{number} не найден."))
            return
        embed = embeds.base(title=f"Кейс #{c['case_number']} · {c['action']}")
        embed.add_field(name="Пользователь", value=f"<@{c['user_id']}> (`{c['user_id']}`)", inline=False)
        embed.add_field(name="Модератор", value=f"<@{c['moderator_id']}>", inline=True)
        embed.add_field(name="Когда", value=_ts(c["created_at"]), inline=True)
        if c["expires_at"]:
            embed.add_field(name="Истекает", value=_ts(c["expires_at"]), inline=True)
        embed.add_field(name="Статус", value="активен" if c["active"] else "снят", inline=True)
        embed.add_field(name="Причина", value=c["reason"] or "—", inline=False)
        await self._reply(ctx, embed)

    @commands.hybrid_command(name="history", aliases=["история"],
                             description="История наказаний пользователя")
    @app_commands.describe(user="Чья история")
    @commands.guild_only()
    @requires_hybrid("history")
    async def history(self, ctx: commands.Context, user: discord.Member) -> None:
        guild = ctx.guild
        cases = await self.bot.db.get_user_cases(guild.id, user.id)
        if not cases:
            await self._reply(ctx, embeds.base(description=f"У {user.mention} нет записей."))
            return
        embed = embeds.base(title=f"История — {user.display_name} ({len(cases)})")
        icons = {"warn": "⚠️", "mute": "🔇", "unmute": "🔊", "kick": "👢",
                 "ban": "🔨", "unban": "♻️", "tempban": "⏳", "note": "📝"}
        for c in cases[:25]:
            icon = icons.get(c["action"], "•")
            status = "" if c["active"] else " ~~(снят)~~"
            embed.add_field(
                name=f"{icon} #{c['case_number']} · {c['action']}{status} · {_ts(c['created_at'])}",
                value=f"<@{c['moderator_id']}>: {c['reason'] or '—'}", inline=False)
        await self._reply(ctx, embed)

    @commands.hybrid_command(name="note", aliases=["заметка"],
                             description="Приватная заметка модератора о пользователе")
    @app_commands.describe(user="О ком", text="Текст заметки")
    @commands.guild_only()
    @requires_hybrid("note")
    async def note(self, ctx: commands.Context, user: discord.Member, *, text: str) -> None:
        guild = ctx.guild
        assert guild and isinstance(ctx.author, discord.Member)
        case_no = await self.bot.db.add_case(guild.id, user.id, ctx.author.id, "note", text)
        await self._reply(ctx, embeds.success(
            f"Заметка добавлена для {user.mention} (кейс #{case_no})."))

    @commands.hybrid_command(name="notes", aliases=["заметки"],
                             description="Показать заметки о пользователе")
    @app_commands.describe(user="О ком")
    @commands.guild_only()
    @requires_hybrid("notes")
    async def notes(self, ctx: commands.Context, user: discord.Member) -> None:
        guild = ctx.guild
        cases = [c for c in await self.bot.db.get_user_cases(guild.id, user.id)
                 if c["action"] == "note"]
        if not cases:
            await self._reply(ctx, embeds.base(description=f"Заметок о {user.mention} нет."))
            return
        embed = embeds.base(title=f"Заметки — {user.display_name} ({len(cases)})")
        for c in cases[:25]:
            embed.add_field(name=f"#{c['case_number']} · {_ts(c['created_at'])} · <@{c['moderator_id']}>",
                            value=c["reason"] or "—", inline=False)
        await self._reply(ctx, embed)

    # ---- channel tools --------------------------------------------------

    @commands.hybrid_command(name="purge", aliases=["clear", "очистить"],
                             description="Удалить последние сообщения")
    @app_commands.describe(amount="Сколько (1–100)", user="Только сообщения пользователя")
    @commands.guild_only()
    @requires_hybrid("purge")
    async def purge(self, ctx: commands.Context, amount: commands.Range[int, 1, 100],
                    user: Optional[discord.Member] = None) -> None:
        channel = ctx.channel
        if not isinstance(channel, discord.TextChannel):
            await self._reply(ctx, embeds.error("Работает только в текстовых каналах."))
            return
        if ctx.interaction is not None:
            await ctx.defer(ephemeral=True)
        deleted = await channel.purge(limit=amount,
                                      check=lambda m: user is None or m.author.id == user.id,
                                      reason=f"purge by {ctx.author}")
        msg = embeds.success(f"Удалено сообщений: **{len(deleted)}**.")
        if ctx.interaction is not None:
            await ctx.send(embed=msg, ephemeral=True)
        else:
            await channel.send(embed=msg, delete_after=5)

    @commands.hybrid_command(name="slowmode", aliases=["медленно"],
                             description="Медленный режим в канале")
    @app_commands.describe(seconds="Задержка в секундах (0–21600, 0 = выкл)")
    @commands.guild_only()
    @requires_hybrid("slowmode")
    async def slowmode(self, ctx: commands.Context, seconds: commands.Range[int, 0, 21600]) -> None:
        channel = ctx.channel
        if not isinstance(channel, discord.TextChannel):
            await self._reply(ctx, embeds.error("Работает только в текстовых каналах."))
            return
        try:
            await channel.edit(slowmode_delay=seconds, reason=f"slowmode by {ctx.author}")
        except discord.Forbidden:
            await self._reply(ctx, embeds.error("Нет прав «Управление каналом»."))
            return
        msg = "Медленный режим выключен." if seconds == 0 else f"Медленный режим: **{seconds}с**."
        await self._reply(ctx, embeds.success(msg))

    @commands.hybrid_command(name="lock", aliases=["закрыть"], description="Закрыть канал для @everyone")
    @app_commands.describe(reason="Причина")
    @commands.guild_only()
    @requires_hybrid("lock")
    async def lock(self, ctx: commands.Context, *, reason: Optional[str] = None) -> None:
        await self._set_lock(ctx, True, reason)

    @commands.hybrid_command(name="unlock", aliases=["открыть"], description="Открыть канал для @everyone")
    @app_commands.describe(reason="Причина")
    @commands.guild_only()
    @requires_hybrid("unlock")
    async def unlock(self, ctx: commands.Context, *, reason: Optional[str] = None) -> None:
        await self._set_lock(ctx, False, reason)

    async def _set_lock(self, ctx: commands.Context, lock: bool, reason: Optional[str]) -> None:
        guild = ctx.guild
        channel = ctx.channel
        if guild is None or not isinstance(channel, discord.TextChannel):
            await self._reply(ctx, embeds.error("Работает только в текстовых каналах."))
            return
        overwrite = channel.overwrites_for(guild.default_role)
        overwrite.send_messages = False if lock else None
        try:
            await channel.set_permissions(
                guild.default_role, overwrite=overwrite,
                reason=f"{'lock' if lock else 'unlock'} by {ctx.author}: {reason or '—'}")
        except discord.Forbidden:
            await self._reply(ctx, embeds.error("Нет прав «Управление ролями/каналом»."))
            return
        await self._reply(ctx, embeds.success("🔒 Канал закрыт." if lock else "🔓 Канал открыт."))

    # ---- escalation ladder config --------------------------------------

    @commands.hybrid_group(name="warnpunish", description="Авто-наказания за предупреждения",
                           guild_only=True)
    @requires_hybrid("warnpunish")
    async def warnpunish(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await self._reply(ctx, embeds.warn("Подкоманды: `set`, `remove`, `list`, `expiry`."))

    @warnpunish.command(name="set", description="Задать наказание при N предупреждениях")
    @app_commands.describe(threshold="Сколько активных предупреждений", action="Что сделать",
                           duration="Для тайм-аута: 1h, 1d (иначе игнор)")
    @app_commands.choices(action=[
        app_commands.Choice(name="Тайм-аут", value="timeout"),
        app_commands.Choice(name="Кик", value="kick"),
        app_commands.Choice(name="Бан", value="ban"),
    ])
    @requires_hybrid("warnpunish")
    async def wp_set(self, ctx: commands.Context, threshold: commands.Range[int, 1, 50],
                     action: app_commands.Choice[str], duration: Optional[str] = None) -> None:
        guild = ctx.guild
        dur = modlog.parse_duration(duration) if (action.value == "timeout" and duration) else None
        if action.value == "timeout" and not dur:
            dur = 3600
        await self.bot.db.set_warn_punishment(guild.id, threshold, action.value, dur)
        extra = f" на {duration}" if (action.value == "timeout" and duration) else ""
        await self._reply(ctx, embeds.success(
            f"При **{threshold}** предупреждениях → **{action.name}**{extra}."))

    @warnpunish.command(name="remove", description="Убрать правило для порога")
    @app_commands.describe(threshold="Порог, который убрать")
    @requires_hybrid("warnpunish")
    async def wp_remove(self, ctx: commands.Context, threshold: commands.Range[int, 1, 50]) -> None:
        n = await self.bot.db.remove_warn_punishment(ctx.guild.id, threshold)
        await self._reply(ctx, embeds.success("Правило удалено." if n else "Такого правила нет."))

    @warnpunish.command(name="list", description="Показать лестницу авто-наказаний")
    @requires_hybrid("warnpunish")
    async def wp_list(self, ctx: commands.Context) -> None:
        guild = ctx.guild
        rules = await self.bot.db.get_warn_punishments(guild.id)
        cfg = await self.bot.db.get_guild_config(guild.id)
        embed = embeds.base(title="⚖️ Авто-наказания за предупреждения")
        if not rules:
            embed.description = "Правил пока нет. Добавьте через `/warnpunish set`."
        else:
            names = {"timeout": "Тайм-аут", "kick": "Кик", "ban": "Бан"}
            for r in rules:
                dur = (f" ({r['duration_seconds'] // 60} мин)"
                       if r["action"] == "timeout" and r["duration_seconds"] else "")
                embed.add_field(name=f"{r['threshold']} предупреждений",
                                value=f"{names.get(r['action'], r['action'])}{dur}", inline=False)
        expiry = cfg["warn_expiry_days"]
        embed.set_footer(text=f"Срок жизни предупреждения: {expiry} дн." if expiry
                         else "Предупреждения не истекают")
        await self._reply(ctx, embed)

    @warnpunish.command(name="expiry",
                        description="Через сколько дней предупреждение истекает (0 = никогда)")
    @app_commands.describe(days="Дней до истечения (0 = бессрочно)")
    @requires_hybrid("warnpunish")
    async def wp_expiry(self, ctx: commands.Context, days: commands.Range[int, 0, 365]) -> None:
        await self.bot.db.update_guild_config(ctx.guild.id, warn_expiry_days=days)
        msg = ("Предупреждения теперь бессрочные." if days == 0
               else f"Срок жизни предупреждения: {days} дн.")
        await self._reply(ctx, embeds.success(msg))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Moderation(bot))
