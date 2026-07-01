"""Economy module (à la Akemi): currency, bonuses, transactions, bank, shop
roles/workers, leaderboard, robbery, and casino games. Commands are hybrid —
usable as both ``/balance`` and ``k.balance``.
"""
from __future__ import annotations

import asyncio
import io
import random
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
from core import banners, embeds
from core.permissions import is_admin_access, requires_hybrid


def admin_check():
    async def predicate(ctx: commands.Context) -> bool:
        return ctx.guild is not None and is_admin_access(ctx.author, ctx.guild)
    return commands.check(predicate)


def currency_manager_check():
    async def predicate(ctx: commands.Context) -> bool:
        cog = ctx.bot.get_cog("Economy")
        return cog is not None and await cog._currency_manager_check(ctx)
    return commands.check(predicate)


def fmt(cfg, amount: int) -> str:
    return f"**{amount:,}** {cfg['symbol']}".replace(",", " ")


def fmt_shop(amount: int) -> str:
    return f"**{amount:,}**".replace(",", " ")


def parse_amount(text: Optional[str], maximum: int) -> Optional[int]:
    if text is None:
        return None
    t = text.strip().lower()
    if t in ("all", "всё", "все", "max", "макс"):
        return maximum
    if t in ("half", "половина", "пол"):
        return maximum // 2
    if t.isdigit():
        return int(t)
    return None


def human_time(seconds: int) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if h:
        parts.append(f"{h} ч")
    if m:
        parts.append(f"{m} мин")
    if s and not h:
        parts.append(f"{s} с")
    return " ".join(parts) or "0 с"


# (эмодзи, название работы, множитель оплаты относительно work_min..work_max)
JOBS = [
    ("🔧", "починил дроида-астромеха", 1.0),
    ("📦", "развёз грузы по секторам", 0.9),
    ("🍜", "отстоял смену в кантине", 0.8),
    ("🚀", "настроил гипердрайв корабля", 1.2),
    ("🛰️", "обслужил орбитальную станцию", 1.1),
    ("⚙️", "продал редкие запчасти", 1.0),
    ("🧑‍🚀", "сводил туристов на экскурсию", 0.85),
    ("🛠️", "взломал старый терминал (легально!)", 1.3),
    ("🪐", "добыл минералы на астероиде", 1.15),
    ("📡", "починил антенну связи", 0.95),
]
CRIMES = [
    ("💳", "провернул аферу с кредитами", True),
    ("🏴‍☠️", "ограбил имперский конвой", True),
    ("🕵️", "продал секретные чертежи", True),
    ("🚓", "попался патрулю при взломе", False),
    ("📉", "вложился в пирамиду и прогорел", False),
    ("🔒", "сработала сигнализация на складе", False),
]
SLOT_EMOJI = ["🍒", "🍋", "🔔", "⭐", "💎", "7️⃣"]
TX_ICONS = {
    "daily": "☀️",
    "weekly": "📅",
    "monthly": "🗓️",
    "timely": "⏰",
    "work": "🔧",
    "worker": "🏭",
    "give": "🎁",
    "take": "🧾",
    "pay": "💸",
    "withdraw": "🏦",
    "deposit": "🏦",
    "buy": "🛒",
    "rob": "🦹",
    "blackjack": "🃏",
    "slots": "🎰",
    "roulette": "🎡",
    "coinflip": "🪙",
    "dice": "🎲",
}

SHOP_CATALOG = [
    ("black_box", "Черный ящик", "Позволяет получить случайный приз, как хороший, так и плохой.", 15000, "kami", "inventory"),
    ("nitro", "Discord Nitro", "Возможность приобретения Nitro на 1 месяц.", 17500, "kami", "manual"),
    ("custom_role", "Личная роль", "Создайте свою личную роль.", 12500, "kami", "manual"),
    ("private_voice", "Админский уют", "Создание личного голосового канала в админской категории.", 10000, "kami", "manual"),
    ("remove_reprimand", "Назад в прошлое", "Снять выговор для себя или другого администратора.", 7500, "kami", "manual"),
    ("jail_coupon", "Купон в СИЗО", "Отправьте другого администратора в СИЗО сроком на 5 дней. Трансфер и питание включены.", 3500, "kami", "inventory"),
    ("steal_insurance", "Страховка от кражи", "Полиция следит за вами и не позволяет вас обокрасть в течение 5 суток.", 500, "kami", "inventory"),
    ("vacation_week", "Снятие с дежурства", "Взять дополнительный отпуск на неделю.", 5000, "kami", "manual"),
    ("color_role", "Цвет никнейма", "Позволяет создать роль с вашим цветом для никнейма.", 3500, "kami", "manual"),
    ("jail_free", "Карта выхода", "Позволяет выйти из тюрьмы или СИЗО досрочно.", 1500, "kami", "inventory"),
    ("steal_boost", "Тень вора", "Повышает шансы на кражу в течение 24 часов.", 500, "kami", "inventory"),
    ("weapon_banner", "Оружейная комната", "Кастомизация профиля. Фон с оружейной.", 450, "appearance", "inventory"),
    ("auto_banner", "Ночной дрифт", "Кастомизация профиля. Фон с автомобилем.", 450, "appearance", "inventory"),
    ("meow_banner", "Кошачья мята", "Кастомизация профиля. Фон с милым котиком.", 450, "appearance", "inventory"),
    ("priroda_banner", "Дух природы", "Кастомизация профиля. Фон с природой.", 450, "appearance", "inventory"),
    ("katana_banner", "Клинок самурая", "Кастомизация профиля. Фон с катаной.", 450, "appearance", "inventory"),
    ("crowbar", "Лом", "Тяжелый лом для грубого взлома дверей во время операции.", 2500, "operations", "inventory"),
    ("lockpicks", "Отмычки", "Набор отмычек для тонкого вскрытия сейфов во время операции.", 2500, "operations", "inventory"),
    ("hacker_kit", "Хакерский набор", "Софт и оборудование для взлома камер и удаления записей.", 2500, "operations", "inventory"),
    ("jammer", "Глушилки", "Переносные глушилки, которые мешают полиции быстро отреагировать.", 2500, "operations", "inventory"),
    ("accessory_4_99", "Аксессуар до 4,99$", "Сертификат на один аксессуар: бейдж, аватарка или эффект профиля стоимостью до 4,99$.", 1500, "kami", "manual"),
    ("accessory_5_99", "Аксессуар до 5,99$", "Сертификат на один аксессуар: бейдж, аватарка или эффект профиля стоимостью до 5,99$.", 2500, "kami", "manual"),
    ("accessory_9_99", "Аксессуар до 9,99$", "Сертификат на один аксессуар: бейдж, аватарка или эффект профиля стоимостью до 9,99$.", 3500, "kami", "manual"),
]

CATEGORY_META = {
    "kami": ("Дары ками", "Основные игровые предметы и перки."),
    "appearance": ("Облик самурая", "Кастомизация для вашего профиля."),
    "operations": ("Операции", "Инструменты для ограблений (/coll)."),
}


def _bar(current: int, total: int, width: int = 10) -> str:
    if total <= 0:
        return "▰" * width
    filled = max(0, min(width, round(width * current / total)))
    return "▰" * filled + "▱" * (width - filled)


def parse_duration(text: str) -> Optional[int]:
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800, "y": 31536000}
    total = 0
    buf = ""
    found = False
    for ch in text.strip().lower():
        if ch.isdigit():
            buf += ch
            continue
        if ch in units and buf:
            total += int(buf) * units[ch]
            buf = ""
            found = True
    return total if found and not buf else None


def apply_multiplier(cfg, amount: int) -> int:
    return max(0, int(amount * float(cfg["money_multiplier"])))


def render_shop_message(template: Optional[str], *, user: discord.Member, role: Optional[discord.Role],
                        item, cfg) -> Optional[str]:
    if not template:
        return None
    role_mention = role.mention if role else "—"
    role_name = role.name if role else "—"
    return (
        template
        .replace("{user}", user.mention)
        .replace("{user.name}", user.display_name)
        .replace("{role}", role_mention)
        .replace("{role.name}", role_name)
        .replace("{item}", item["name"])
        .replace("{price}", str(item["price"]))
        .replace("{currency}", str(cfg["symbol"]))
    )


class Economy(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.temp_role_check.start()
        self.worker_income_check.start()

    async def cog_unload(self) -> None:
        self.temp_role_check.cancel()
        self.worker_income_check.cancel()

    @tasks.loop(minutes=1)
    async def temp_role_check(self) -> None:
        for row in await self.bot.db.temp_roles_due():
            guild = self.bot.get_guild(row["guild_id"])
            if guild is not None:
                member = guild.get_member(row["user_id"])
                role = guild.get_role(row["role_id"])
                if member and role:
                    try:
                        await member.remove_roles(role, reason="Истёк срок временной роли")
                    except discord.HTTPException:
                        pass
            await self.bot.db.expire_shop_purchase_role(row["guild_id"], row["user_id"], row["role_id"])
            await self.bot.db.temp_role_delete(row["id"])

    @temp_role_check.before_loop
    async def _before(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=5)
    async def worker_income_check(self) -> None:
        for row in await self.bot.db.worker_purchases_due():
            guild = self.bot.get_guild(row["guild_id"])
            if guild is None:
                continue
            member = guild.get_member(row["user_id"])
            role = guild.get_role(row["role_id"])
            if member is None or role is None or role not in member.roles:
                continue
            await self.bot.db.econ_add_wallet(
                row["guild_id"], row["user_id"], row["income_amount"],
                command="worker", note=f"Доход рабочей роли: {row['name']}",
            )
            await self.bot.db.mark_worker_paid(row["id"])

    @worker_income_check.before_loop
    async def _before_worker(self) -> None:
        await self.bot.wait_until_ready()

    async def _enabled(self, ctx: commands.Context) -> bool:
        cfg = await self.bot.db.get_econ_config(ctx.guild.id)
        if cfg["enabled"]:
            return True
        await ctx.reply(embed=embeds.warn("Экономика на этом сервере выключена."), mention_author=False)
        return False

    async def _is_currency_manager(self, member: discord.Member) -> bool:
        if is_admin_access(member, member.guild):
            return True
        role_ids = [role.id for role in member.roles]
        return await self.bot.db.econ_is_manager(member.guild.id, member.id, role_ids)

    async def _currency_manager_check(self, ctx: commands.Context) -> bool:
        return ctx.guild is not None and isinstance(ctx.author, discord.Member) and await self._is_currency_manager(ctx.author)

    async def _shop_staff_check(self, member: discord.Member) -> bool:
        if is_admin_access(member, member.guild):
            return True
        cfg = await self.bot.db.get_econ_config(member.guild.id)
        if cfg["shop_owner_id"] and member.id == cfg["shop_owner_id"]:
            return True
        return await self._is_currency_manager(member)

    async def _send_econ_log(self, guild: discord.Guild, cfg, embed: discord.Embed, *, shop: bool = False) -> None:
        channel_id = cfg["shop_log_channel_id"] if shop else cfg["grant_log_channel_id"]
        if not channel_id and shop:
            channel_id = cfg["grant_log_channel_id"]
        channel = guild.get_channel(channel_id) if channel_id else None
        if isinstance(channel, discord.abc.Messageable):
            try:
                await channel.send(embed=embed)
            except discord.HTTPException:
                pass

    async def _check_grant_limits(self, ctx: commands.Context, amount: int) -> bool:
        if is_admin_access(ctx.author, ctx.guild):
            return True
        cfg = await self.bot.db.get_econ_config(ctx.guild.id)
        now = int(time.time())
        cooldown = int(cfg["grant_cooldown"] or 0)
        if cooldown:
            last = await self.bot.db.econ_actor_last_grant(ctx.guild.id, ctx.author.id)
            if last and now - last < cooldown:
                await ctx.reply(embed=embeds.warn(
                    f"Выдача на кулдауне. Осталось {human_time(cooldown - (now - last))}."),
                    mention_author=False)
                return False
        daily_limit = int(cfg["grant_daily_limit"] or 0)
        if daily_limit and amount > 0:
            since = now - 86400
            used = await self.bot.db.econ_actor_granted_since(ctx.guild.id, ctx.author.id, since)
            if used + amount > daily_limit:
                await ctx.reply(embed=embeds.warn(
                    f"Суточный лимит выдачи: {fmt_shop(daily_limit)} админ-валюты. Уже выдано {fmt_shop(used)}."),
                    mention_author=False)
                return False
        return True

    # ---- core: balance / daily / work ----------------------------------

    @commands.hybrid_command(name="balance", aliases=["bal", "баланс", "profile", "профиль"],
                             description="Показать баланс кошелька и банка")
    @app_commands.describe(user="Чей баланс (по умолчанию — ваш)")
    @commands.guild_only()
    @requires_hybrid("balance")
    async def balance(self, ctx: commands.Context, user: Optional[discord.Member] = None) -> None:
        if not await self._enabled(ctx):
            return
        member = user or ctx.author
        cfg = await self.bot.db.get_econ_config(ctx.guild.id)
        u = await self.bot.db.get_econ_user(ctx.guild.id, member.id)
        rows = await self.bot.db.econ_leaderboard(ctx.guild.id, 1000)
        rank = next((i + 1 for i, r in enumerate(rows) if r["user_id"] == member.id), len(rows) + 1)
        total = u["wallet"] + u["bank"]

        # Generated balance card (fall back to an embed on any failure).
        accent = member.color.to_rgb() if member.color.value else (88, 101, 242)
        try:
            av = await member.display_avatar.replace(size=128).read()
            data = await banners.balance(av, member.display_name, u["wallet"], u["bank"],
                                         rank, cfg["currency_name"], accent)
            await ctx.reply(file=discord.File(io.BytesIO(data), filename="balance.png"),
                            mention_author=False)
            return
        except Exception:
            pass

        embed = discord.Embed(color=member.color if member.color.value else config.EMBED_COLOR)
        embed.set_author(name=f"Профиль · {member.display_name}", icon_url=member.display_avatar.url)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="👛 Кошелёк", value=fmt(cfg, u["wallet"]), inline=True)
        embed.add_field(name="🏦 Банк", value=fmt(cfg, u["bank"]), inline=True)
        embed.add_field(name="🏅 Место", value=f"#{rank}", inline=True)
        embed.add_field(name="💎 Состояние", value=fmt(cfg, total), inline=False)
        embed.set_footer(text=f"{cfg['currency_name']} · сервер {ctx.guild.name}")
        await ctx.reply(embed=embed, mention_author=False)

    @commands.hybrid_command(name="daily", aliases=["дейли"], description="Получить ежедневный бонус")
    @commands.guild_only()
    @requires_hybrid("daily")
    async def daily(self, ctx: commands.Context) -> None:
        if not await self._enabled(ctx):
            return
        await self._periodic_bonus(ctx, "last_daily", "daily_amount", "daily_cooldown",
                                   "☀️ Ежедневный бонус", "daily")

    @commands.hybrid_command(name="work", aliases=["работа", "подработка"],
                             description="Подработать на случайной работе и заработать монет")
    @commands.guild_only()
    @requires_hybrid("work")
    async def work(self, ctx: commands.Context) -> None:
        if not await self._enabled(ctx):
            return
        cfg = await self.bot.db.get_econ_config(ctx.guild.id)
        u = await self.bot.db.get_econ_user(ctx.guild.id, ctx.author.id)
        now = int(time.time())
        left = cfg["work_cooldown"] - (now - u["last_work"])
        if left > 0:
            await ctx.reply(embed=embeds.warn(f"Ты устал. Отдохни ещё **{human_time(left)}**."),
                            mention_author=False)
            return
        emoji, job, mult = random.choice(JOBS)
        amount = apply_multiplier(cfg, int(random.randint(cfg["work_min"], cfg["work_max"]) * mult))
        new = await self.bot.db.econ_add_wallet(
            ctx.guild.id, ctx.author.id, amount, command="work", note=job
        )
        await self.bot.db.econ_update(ctx.guild.id, ctx.author.id, last_work=now)
        embed = embeds.success(f"Ты {job} и получил **+{fmt(cfg, amount)}**.")
        embed.title = f"{emoji} Подработка"
        embed.add_field(name="Кошелёк", value=fmt(cfg, new), inline=True)
        await ctx.reply(embed=embed, mention_author=False)

    @commands.hybrid_command(name="timely", aliases=["регулярный"], description="Получить регулярный бонус")
    @commands.guild_only()
    @requires_hybrid("timely")
    async def timely(self, ctx: commands.Context) -> None:
        if not await self._enabled(ctx):
            return
        await self._periodic_bonus(ctx, "last_timely", "timely_amount", "timely_cooldown",
                                   "⏰ Регулярный бонус", "timely")

    @commands.hybrid_command(name="weekly", aliases=["неделя"], description="Получить еженедельный бонус")
    @commands.guild_only()
    @requires_hybrid("weekly")
    async def weekly(self, ctx: commands.Context) -> None:
        if not await self._enabled(ctx):
            return
        await self._periodic_bonus(ctx, "last_weekly", "weekly_amount", "weekly_cooldown",
                                   "📅 Еженедельный бонус", "weekly")

    @commands.hybrid_command(name="monthly", aliases=["месяц"], description="Получить ежемесячный бонус")
    @commands.guild_only()
    @requires_hybrid("monthly")
    async def monthly(self, ctx: commands.Context) -> None:
        if not await self._enabled(ctx):
            return
        await self._periodic_bonus(ctx, "last_monthly", "monthly_amount", "monthly_cooldown",
                                   "🗓️ Ежемесячный бонус", "monthly")

    async def _periodic_bonus(self, ctx, last_field, amount_field, cd_field, title, command) -> None:
        cfg = await self.bot.db.get_econ_config(ctx.guild.id)
        u = await self.bot.db.get_econ_user(ctx.guild.id, ctx.author.id)
        now = int(time.time())
        left = cfg[cd_field] - (now - u[last_field])
        if left > 0:
            await ctx.reply(embed=embeds.warn(f"Уже получено. Возвращайся через **{human_time(left)}**."),
                            mention_author=False)
            return
        amount = apply_multiplier(cfg, cfg[amount_field])
        new = await self.bot.db.econ_add_wallet(ctx.guild.id, ctx.author.id, amount, command=command)
        await self.bot.db.econ_update(ctx.guild.id, ctx.author.id, **{last_field: now})
        embed = embeds.success(f"Бонус: **+{fmt(cfg, amount)}**\nКошелёк: {fmt(cfg, new)}")
        embed.title = title
        await ctx.reply(embed=embed, mention_author=False)

    @commands.hybrid_command(name="crime", aliases=["преступление", "дело"],
                             description="Рискнуть ради крупного куша (или штрафа)")
    @commands.guild_only()
    @requires_hybrid("crime")
    async def crime(self, ctx: commands.Context) -> None:
        if not await self._enabled(ctx):
            return
        cfg = await self.bot.db.get_econ_config(ctx.guild.id)
        u = await self.bot.db.get_econ_user(ctx.guild.id, ctx.author.id)
        now = int(time.time())
        left = cfg["work_cooldown"] - (now - u["last_work"])
        if left > 0:
            await ctx.reply(embed=embeds.warn(f"Слишком жарко. Подожди **{human_time(left)}**."),
                            mention_author=False)
            return
        await self.bot.db.econ_update(ctx.guild.id, ctx.author.id, last_work=now)
        emoji, story, success = random.choice(CRIMES)
        if success:
            reward = apply_multiplier(cfg, int(random.randint(cfg["work_min"], cfg["work_max"]) * 2.5))
            new = await self.bot.db.econ_add_wallet(ctx.guild.id, ctx.author.id, reward, command="work")
            embed = embeds.success(f"Ты {story} и сорвал **+{fmt(cfg, reward)}**!")
        else:
            fine = min(u["wallet"], random.randint(cfg["work_min"], cfg["work_max"]))
            new = await self.bot.db.econ_add_wallet(ctx.guild.id, ctx.author.id, -fine, command="work")
            embed = embeds.error(f"Ты {story} — штраф **-{fmt(cfg, fine)}**.")
        embed.title = f"{emoji} Тёмные дела"
        embed.add_field(name="Кошелёк", value=fmt(cfg, new), inline=True)
        await ctx.reply(embed=embed, mention_author=False)

    # ---- bank: deposit / withdraw --------------------------------------

    @commands.hybrid_command(name="deposit", aliases=["dep", "депозит"],
                             description="Положить деньги в банк")
    @app_commands.describe(amount="Сумма или 'all' / 'половина'")
    @commands.guild_only()
    @requires_hybrid("deposit")
    async def deposit(self, ctx: commands.Context, amount: str) -> None:
        if not await self._enabled(ctx):
            return
        cfg = await self.bot.db.get_econ_config(ctx.guild.id)
        u = await self.bot.db.get_econ_user(ctx.guild.id, ctx.author.id)
        n = parse_amount(amount, u["wallet"])
        if n is None or n <= 0:
            await ctx.reply(embed=embeds.error("Укажи сумму числом, `all` или `половина`."),
                            mention_author=False)
            return
        if n > u["wallet"]:
            await ctx.reply(embed=embeds.error("В кошельке недостаточно средств."), mention_author=False)
            return
        await self.bot.db.econ_update(ctx.guild.id, ctx.author.id,
                                      wallet=u["wallet"] - n, bank=u["bank"] + n)
        await self.bot.db.add_transaction(
            ctx.guild.id, "decrement", n, command="deposit",
            sender_id=ctx.author.id, receiver_id=ctx.author.id, note="Перевод в банк",
        )
        await ctx.reply(embed=embeds.success(f"В банк положено {fmt(cfg, n)}."), mention_author=False)

    @commands.hybrid_command(name="withdraw", aliases=["wd", "снять"],
                             description="Снять деньги из банка")
    @app_commands.describe(amount="Сумма или 'all' / 'половина'")
    @commands.guild_only()
    @requires_hybrid("withdraw")
    async def withdraw(self, ctx: commands.Context, amount: str) -> None:
        if not await self._enabled(ctx):
            return
        cfg = await self.bot.db.get_econ_config(ctx.guild.id)
        u = await self.bot.db.get_econ_user(ctx.guild.id, ctx.author.id)
        n = parse_amount(amount, u["bank"])
        if n is None or n <= 0:
            await ctx.reply(embed=embeds.error("Укажи сумму числом, `all` или `половина`."),
                            mention_author=False)
            return
        if n > u["bank"]:
            await ctx.reply(embed=embeds.error("В банке недостаточно средств."), mention_author=False)
            return
        await self.bot.db.econ_update(ctx.guild.id, ctx.author.id,
                                      wallet=u["wallet"] + n, bank=u["bank"] - n)
        await self.bot.db.add_transaction(
            ctx.guild.id, "increment", n, command="withdraw",
            sender_id=ctx.author.id, receiver_id=ctx.author.id, note="Снятие из банка",
        )
        await ctx.reply(embed=embeds.success(f"Из банка снято {fmt(cfg, n)}."), mention_author=False)

    # ---- pay / rob -----------------------------------------------------

    @commands.hybrid_command(name="pay", aliases=["give", "перевод", "заплатить"],
                             description="Перевести монеты другому участнику")
    @app_commands.describe(user="Кому перевести", amount="Сумма")
    @commands.guild_only()
    @requires_hybrid("pay")
    async def pay(self, ctx: commands.Context, user: discord.Member, amount: int) -> None:
        if not await self._enabled(ctx):
            return
        cfg = await self.bot.db.get_econ_config(ctx.guild.id)
        if user.bot or user.id == ctx.author.id:
            await ctx.reply(embed=embeds.error("Нельзя перевести себе или боту."), mention_author=False)
            return
        if amount <= 0:
            await ctx.reply(embed=embeds.error("Сумма должна быть больше нуля."), mention_author=False)
            return
        sender = await self.bot.db.get_econ_user(ctx.guild.id, ctx.author.id)
        commission = amount * cfg["commission_pct"] // 100
        total = amount + commission
        if total > sender["wallet"]:
            await ctx.reply(embed=embeds.error("В кошельке недостаточно средств."), mention_author=False)
            return
        await self.bot.db.econ_transfer(
            ctx.guild.id, ctx.author.id, user.id, amount, command="pay",
            note=f"Комиссия: {commission}" if commission else None,
        )
        if commission:
            await self.bot.db.econ_add_wallet(
                ctx.guild.id, ctx.author.id, -commission, command="pay",
                note="Комиссия перевода",
            )
        extra = f"\nКомиссия: {fmt(cfg, commission)}" if commission else ""
        await ctx.reply(embed=embeds.success(
            f"{ctx.author.mention} → {user.mention}: {fmt(cfg, amount)}{extra}"),
            mention_author=False)

    @commands.hybrid_command(name="rob", aliases=["ограбить"], description="Попытаться ограбить участника")
    @app_commands.describe(user="Кого ограбить")
    @commands.guild_only()
    @requires_hybrid("rob")
    async def rob(self, ctx: commands.Context, user: discord.Member) -> None:
        if not await self._enabled(ctx):
            return
        cfg = await self.bot.db.get_econ_config(ctx.guild.id)
        if user.bot or user.id == ctx.author.id:
            await ctx.reply(embed=embeds.error("Неудачная цель."), mention_author=False)
            return
        robber = await self.bot.db.get_econ_user(ctx.guild.id, ctx.author.id)
        now = int(time.time())
        left = cfg["rob_cooldown"] - (now - robber["last_rob"])
        if left > 0:
            await ctx.reply(embed=embeds.warn(f"Залечь на дно ещё на **{human_time(left)}**."),
                            mention_author=False)
            return
        victim = await self.bot.db.get_econ_user(ctx.guild.id, user.id)
        if victim["wallet"] < 50:
            await ctx.reply(embed=embeds.warn("У цели слишком мало в кошельке — не стоит риска."),
                            mention_author=False)
            return
        await self.bot.db.econ_update(ctx.guild.id, ctx.author.id, last_rob=now)
        if random.randint(1, 100) <= cfg["rob_success"]:
            stolen = random.randint(1, victim["wallet"] * cfg["rob_max_pct"] // 100 or 1)
            await self.bot.db.econ_transfer(
                ctx.guild.id, user.id, ctx.author.id, stolen, command="rob",
                note=f"Ограбление {user} пользователем {ctx.author}",
            )
            await ctx.reply(embed=embeds.success(
                f"🦹 Успех! Ты украл у {user.mention} {fmt(cfg, stolen)}."), mention_author=False)
        else:
            fine = min(robber["wallet"], random.randint(50, 250))
            await self.bot.db.econ_add_wallet(ctx.guild.id, ctx.author.id, -fine, command="rob")
            await ctx.reply(embed=embeds.error(
                f"🚨 Тебя поймали! Штраф {fmt(cfg, fine)}."), mention_author=False)

    @commands.hybrid_command(name="baltop", aliases=["leaderboard_money", "топбаланс", "богачи"],
                             description="Топ участников по балансу")
    @commands.guild_only()
    @requires_hybrid("baltop")
    async def baltop(self, ctx: commands.Context) -> None:
        if not await self._enabled(ctx):
            return
        cfg = await self.bot.db.get_econ_config(ctx.guild.id)
        rows = await self.bot.db.econ_leaderboard(ctx.guild.id, 10)
        if not rows:
            await ctx.reply(embed=embeds.base(description="Пока ни у кого нет монет."), mention_author=False)
            return
        cur = cfg["currency_name"]

        async def fetch(uid):
            m = ctx.guild.get_member(uid)
            name = m.display_name if m else f"Участник {uid}"
            try:
                av = await (m or await self.bot.fetch_user(uid)).display_avatar.replace(size=64).read()
            except Exception:
                av = None
            return name, av
        try:
            fetched = await asyncio.gather(*(fetch(r["user_id"]) for r in rows))
            entries = []
            for i, (r, (name, av)) in enumerate(zip(rows, fetched)):
                val = f"{r['wallet'] + r['bank']:,}".replace(",", " ") + f" {cur}"
                entries.append((i + 1, name, val, av))
            img = await banners.leaderboard("Богачи сервера", f"валюта: {cur}", entries, (240, 196, 90))
            await ctx.reply(file=discord.File(io.BytesIO(img), filename="baltop.png"),
                            mention_author=False)
            return
        except Exception:
            pass
        medals = ["🥇", "🥈", "🥉"]
        lines = [f"{medals[i] if i < 3 else f'**{i+1}.**'} <@{r['user_id']}> — "
                 f"{fmt(cfg, r['wallet'] + r['bank'])}" for i, r in enumerate(rows)]
        await ctx.reply(embed=embeds.base(title="💰 Богачи сервера", description="\n".join(lines)),
                        mention_author=False)

    # ---- shop ----------------------------------------------------------

    def _shop_home_embed(self, categories: list[str]) -> discord.Embed:
        embed = embeds.base(title="shop | YSW", description="━━━━━━━━━━━━━━━━━━━━")
        for key in categories:
            label, desc = CATEGORY_META.get(key, (key.title(), "Товары этой категории."))
            embed.add_field(name=label, value=desc, inline=False)
        embed.set_footer(text="Выберите категорию товаров...")
        return embed

    async def _shop_category_embed(self, guild_id: int, category: str) -> discord.Embed:
        items = await self.bot.db.shop_list(guild_id, category=category)
        label, _ = CATEGORY_META.get(category, (category.title(), ""))
        embed = embeds.base(title="shop | YSW", description="━━━━━━━━━━━━━━━━━━━━")
        if not items:
            embed.add_field(name=label, value="В этой категории пока нет товаров.", inline=False)
            return embed
        for item in items[:25]:
            embed.add_field(
                name=item["name"],
                value=(f"• Товар: {item['name']}\n"
                       f"• Цена: {item['price']:,}\n"
                       f"• Описание: {item['description'] or '—'}").replace(",", ","),
                inline=False,
            )
        return embed

    async def _shop_confirm_embed(self, guild_id: int, item_id: int) -> discord.Embed:
        item = await self.bot.db.shop_get(guild_id, item_id)
        if item is None:
            return embeds.error("Товар не найден.")
        return embeds.base(
            title=f"Подтверждение покупки: {item['name']}",
            description=f"{item['description'] or '—'}\n\n**Цена:** {item['price']}",
        )

    async def _purchase_shop_item(self, guild: discord.Guild, member: discord.Member, item_id: int) -> discord.Embed:
        cfg = await self.bot.db.get_econ_config(guild.id)
        item = await self.bot.db.shop_get(guild.id, item_id)
        if item is None:
            return embeds.error("Товар не найден.")
        if not item["active"]:
            return embeds.warn("Этот товар сейчас выключен.")
        delivery = item["delivery_type"] or item["item_type"]
        role = guild.get_role(item["role_id"]) if item["role_id"] else None
        role_item = delivery in ("role", "time_role", "worker")
        bot_member = guild.me or guild.get_member(self.bot.user.id)
        if role_item:
            if role is None:
                return embeds.error("Роль товара удалена. Сообщите админам.")
            if bot_member and role >= bot_member.top_role:
                return embeds.error("Бот не может выдать эту роль (она выше его роли).")
            if role in member.roles and delivery != "time_role":
                return embeds.warn("У тебя уже есть эта роль.")
        if item["purchase_limit"]:
            count = await self.bot.db.shop_purchase_count(guild.id, item["id"])
            if count >= item["purchase_limit"]:
                return embeds.warn("Лимит покупок этого товара уже исчерпан.")
        if item["stock"]:
            count = await self.bot.db.shop_purchase_count(guild.id, item["id"])
            if count >= item["stock"]:
                return embeds.warn("Товар закончился на складе.")
        u = await self.bot.db.get_econ_user(guild.id, member.id)
        if u["shop_wallet"] < item["price"]:
            return embeds.error(f"Не хватает {fmt_shop(item['price'] - u['shop_wallet'])} админ-валюты.")
        if role_item:
            try:
                await member.add_roles(role, reason=f"Покупка в магазине: {item['name']}")
            except discord.Forbidden:
                return embeds.error("Не удалось выдать роль (права бота).")
        await self.bot.db.econ_add_shop_wallet(
            guild.id, member.id, -item["price"], command="shop_buy", note=f"Покупка: {item['name']}"
        )
        expires_at = int(time.time()) + item["duration"] if role_item and item["duration"] else None
        if role_item and item["duration"]:
            await self.bot.db.temp_role_add(guild.id, member.id, role.id, expires_at)
        await self.bot.db.shop_record_purchase(
            guild.id, member.id, item["id"], role.id if role else 0, delivery, expires_at
        )
        request_id = None
        if delivery == "inventory":
            await self.bot.db.shop_inventory_add(
                guild.id, member.id, item["id"], delivery, item["name"], metadata=item["description"]
            )
        elif delivery == "manual":
            request_id = await self.bot.db.shop_request_create(
                guild.id, member.id, item["id"], item["name"], details=item["description"]
            )
            log = embeds.base(
                title=f"🛒 Заявка магазина #{request_id}",
                description=(f"Покупатель: {member.mention}\nТовар: **{item['name']}** (`#{item['id']}`)\n"
                             f"Цена: {fmt_shop(item['price'])}\nСтатус: `pending`"),
            )
            await self._send_econ_log(guild, cfg, log, shop=True)
            if cfg["shop_owner_id"]:
                owner = guild.get_member(cfg["shop_owner_id"]) or self.bot.get_user(cfg["shop_owner_id"])
                if owner:
                    try:
                        await owner.send(embed=log)
                    except discord.HTTPException:
                        pass
        suffix = "" if not item["duration"] else f" на {human_time(item['duration'])}"
        custom = render_shop_message(item["response_message"], user=member, role=role, item=item, cfg=cfg)
        if custom:
            text = custom
        elif delivery == "manual":
            text = (f"Куплено: **{item['name']}**. Заявка `#{request_id}` отправлена на ручную выдачу. "
                    f"Списано {fmt_shop(item['price'])} админ-валюты.")
        elif delivery == "inventory":
            text = f"Куплено: **{item['name']}**. Товар добавлен в `/inventory`. Списано {fmt_shop(item['price'])} админ-валюты."
        else:
            text = f"Куплено: **{item['name']}** → {role.mention}{suffix}. Списано {fmt_shop(item['price'])} админ-валюты."
        return embeds.success(text)

    @commands.hybrid_command(name="shop", aliases=["магазин"], description="Магазин товаров за валюту")
    @app_commands.describe(category="Категория товаров (если нужно отфильтровать)")
    @commands.guild_only()
    @requires_hybrid("shop")
    async def shop(self, ctx: commands.Context, category: Optional[str] = None) -> None:
        if not await self._enabled(ctx):
            return
        cfg = await self.bot.db.get_econ_config(ctx.guild.id)
        items = await self.bot.db.shop_list(ctx.guild.id, category=category)
        if not items:
            suffix = f" в категории `{category}`" if category else ""
            await ctx.reply(embed=embeds.base(description=f"Магазин{suffix} пуст. Админ добавляет товары через "
                            "`/economy additem`."), mention_author=False)
            return
        if category:
            await ctx.reply(
                embed=await self._shop_category_embed(ctx.guild.id, category),
                view=ShopItemsView(self, ctx.author.id, category, items),
                mention_author=False,
            )
            return
        categories = sorted({it["category"] for it in items if it["category"]})
        await ctx.reply(
            embed=self._shop_home_embed(categories),
            view=ShopCategoryView(self, ctx.author.id, categories),
            mention_author=False,
        )

    @commands.hybrid_command(name="buy", aliases=["купить"], description="Купить товар из магазина по id")
    @app_commands.describe(item_id="Номер товара из /shop")
    @commands.guild_only()
    @requires_hybrid("buy")
    async def buy(self, ctx: commands.Context, item_id: int) -> None:
        if not await self._enabled(ctx):
            return
        await ctx.reply(
            embed=await self._purchase_shop_item(ctx.guild, ctx.author, item_id),
            mention_author=False,
        )

    @commands.hybrid_command(name="shop_balance", aliases=["sbal"], description="Показать баланс админской валюты магазина")
    @app_commands.describe(user="Чей баланс показать")
    @commands.guild_only()
    @requires_hybrid("shop")
    async def shop_balance(self, ctx: commands.Context, user: Optional[discord.Member] = None) -> None:
        if not await self._enabled(ctx):
            return
        member = user or ctx.author
        row = await self.bot.db.get_econ_user(ctx.guild.id, member.id)
        await ctx.reply(embed=embeds.base(
            title=f"Баланс магазина · {member.display_name}",
            description=f"Админ-валюта: {fmt_shop(row['shop_wallet'])}",
        ), mention_author=False)

    @commands.hybrid_command(name="inventory", aliases=["инвентарь"], description="Показать купленные товары из инвентаря")
    @app_commands.describe(user="Чей инвентарь показать (админам)")
    @commands.guild_only()
    @requires_hybrid("shop")
    async def inventory(self, ctx: commands.Context, user: Optional[discord.Member] = None) -> None:
        if not await self._enabled(ctx):
            return
        member = user if user and await self._shop_staff_check(ctx.author) else ctx.author
        rows = await self.bot.db.shop_inventory_list(ctx.guild.id, member.id)
        if not rows:
            await ctx.reply(embed=embeds.base(description=f"У {member.mention} нет активных предметов."),
                            mention_author=False)
            return
        lines = [f"`#{r['id']}` **{r['name']}** · товар `#{r['item_id']}` · <t:{r['purchased_at']}:R>"
                 for r in rows[:20]]
        await ctx.reply(embed=embeds.base(title=f"🎒 Инвентарь · {member.display_name}",
                                          description="\n".join(lines)),
                        mention_author=False)

    # ---- games ---------------------------------------------------------

    async def _take_bet(self, ctx: commands.Context, bet: int):
        """Validate a bet; return (cfg, user_row) or (None, None) after replying with an error."""
        if not await self._enabled(ctx):
            return None, None
        cfg = await self.bot.db.get_econ_config(ctx.guild.id)
        u = await self.bot.db.get_econ_user(ctx.guild.id, ctx.author.id)
        if bet < cfg["bet_min"] or bet > cfg["bet_max"]:
            await ctx.reply(embed=embeds.error(
                f"Ставка должна быть от {fmt(cfg, cfg['bet_min'])} до {fmt(cfg, cfg['bet_max'])}."),
                mention_author=False)
            return None, None
        if bet > u["wallet"]:
            await ctx.reply(embed=embeds.error("В кошельке недостаточно средств."), mention_author=False)
            return None, None
        return cfg, u

    @commands.hybrid_command(name="coinflip", aliases=["cf", "монетка"],
                             description="Орёл или решка на ставку")
    @app_commands.describe(side="орёл / решка", bet="Ставка")
    @commands.guild_only()
    @requires_hybrid("coinflip")
    async def coinflip(self, ctx: commands.Context, side: str, bet: int) -> None:
        cfg, u = await self._take_bet(ctx, bet)
        if cfg is None:
            return
        s = side.strip().lower()
        if s not in ("орёл", "орел", "решка", "heads", "tails", "о", "р"):
            await ctx.reply(embed=embeds.error("Выбор: `орёл` или `решка`."), mention_author=False)
            return
        chosen_heads = s in ("орёл", "орел", "heads", "о")
        result_heads = random.random() < 0.5
        win = chosen_heads == result_heads
        delta = bet if win else -bet
        new = await self.bot.db.econ_add_wallet(ctx.guild.id, ctx.author.id, delta, command="coinflip")
        face = "🪙 Орёл" if result_heads else "🪙 Решка"
        emb = embeds.success if win else embeds.error
        await ctx.reply(embed=emb(f"{face}! {'Победа +' if win else 'Проигрыш -'}{fmt(cfg, bet)}\n"
                                  f"Кошелёк: {fmt(cfg, new)}"), mention_author=False)

    @commands.hybrid_command(name="dice", aliases=["кости", "кубик"], description="Кости против бота")
    @app_commands.describe(bet="Ставка")
    @commands.guild_only()
    @requires_hybrid("dice")
    async def dice(self, ctx: commands.Context, bet: int) -> None:
        cfg, u = await self._take_bet(ctx, bet)
        if cfg is None:
            return
        you, bot_roll = random.randint(1, 6), random.randint(1, 6)
        if you > bot_roll:
            new = await self.bot.db.econ_add_wallet(ctx.guild.id, ctx.author.id, bet, command="dice")
            text, emb = f"Победа +{fmt(cfg, bet)}", embeds.success
        elif you < bot_roll:
            new = await self.bot.db.econ_add_wallet(ctx.guild.id, ctx.author.id, -bet, command="dice")
            text, emb = f"Проигрыш -{fmt(cfg, bet)}", embeds.error
        else:
            new = u["wallet"]
            text, emb = "Ничья — ставка возвращена", embeds.warn
        await ctx.reply(embed=emb(f"🎲 Ты: **{you}** · Бот: **{bot_roll}**\n{text}\nКошелёк: {fmt(cfg, new)}"),
                        mention_author=False)

    @commands.hybrid_command(name="slots", aliases=["слоты", "казино"], description="Игровой автомат")
    @app_commands.describe(bet="Ставка")
    @commands.guild_only()
    @requires_hybrid("slots")
    async def slots(self, ctx: commands.Context, bet: int) -> None:
        cfg, u = await self._take_bet(ctx, bet)
        if cfg is None:
            return
        reels = [random.choice(SLOT_EMOJI) for _ in range(3)]

        def frame(stopped: int) -> str:
            cells = [reels[i] if i < stopped else random.choice(SLOT_EMOJI) for i in range(3)]
            return "꞉  " + "  ꞉  ".join(cells) + "  ꞉"

        msg = await ctx.reply(
            embed=embeds.base(title="🎰 Игровой автомат", description=frame(0)),
            mention_author=False)
        # Reels stop one by one.
        for stopped in range(1, 4):
            await asyncio.sleep(0.7)
            try:
                await msg.edit(embed=embeds.base(title="🎰 Игровой автомат", description=frame(stopped)))
            except discord.HTTPException:
                break

        if reels[0] == reels[1] == reels[2]:
            mult = 10 if reels[0] in ("💎", "7️⃣") else 5
        elif reels[0] == reels[1] or reels[1] == reels[2] or reels[0] == reels[2]:
            mult = 2
        else:
            mult = 0
        delta = bet * (mult - 1) if mult else -bet
        new = await self.bot.db.econ_add_wallet(ctx.guild.id, ctx.author.id, delta, command="slots")
        if mult >= 5:
            color, res = config.SUCCESS_COLOR, f"🎉 ДЖЕКПОТ x{mult}! +{fmt(cfg, bet * (mult - 1))}"
        elif mult == 2:
            color, res = config.SUCCESS_COLOR, f"Выигрыш x2! +{fmt(cfg, bet)}"
        else:
            color, res = config.ERROR_COLOR, f"Мимо. -{fmt(cfg, bet)}"
        final = discord.Embed(title="🎰 Игровой автомат",
                              description=f"꞉  {'  ꞉  '.join(reels)}  ꞉\n\n{res}\nКошелёк: {fmt(cfg, new)}",
                              color=color)
        try:
            await msg.edit(embed=final)
        except discord.HTTPException:
            await ctx.send(embed=final)

    @commands.hybrid_command(name="roulette", aliases=["рулетка"],
                             description="Рулетка: ставь на красное/чёрное/зелёное или число 0–36")
    @app_commands.describe(choice="красное / чёрное / зелёное / число 0-36", bet="Ставка")
    @commands.guild_only()
    @requires_hybrid("roulette")
    async def roulette(self, ctx: commands.Context, choice: str, bet: int) -> None:
        cfg, u = await self._take_bet(ctx, bet)
        if cfg is None:
            return
        c = choice.strip().lower()
        spin = random.randint(0, 36)
        spin_color = "зелёное" if spin == 0 else ("красное" if spin % 2 else "чёрное")
        payout = 0
        if c.isdigit():
            if not (0 <= int(c) <= 36):
                await ctx.reply(embed=embeds.error("Число от 0 до 36."), mention_author=False)
                return
            payout = bet * 35 if int(c) == spin else -bet
        elif c in ("красное", "красный", "red", "к"):
            payout = bet if spin_color == "красное" else -bet
        elif c in ("чёрное", "черное", "чёрный", "черный", "black", "ч"):
            payout = bet if spin_color == "чёрное" else -bet
        elif c in ("зелёное", "зеленое", "green", "з"):
            payout = bet * 14 if spin_color == "зелёное" else -bet
        else:
            await ctx.reply(embed=embeds.error("Ставь на `красное`, `чёрное`, `зелёное` или число 0–36."),
                            mention_author=False)
            return
        new = await self.bot.db.econ_add_wallet(ctx.guild.id, ctx.author.id, payout, command="roulette")
        emb = embeds.success if payout > 0 else embeds.error
        sign = "+" if payout > 0 else ""
        await ctx.reply(embed=emb(f"🎡 Выпало **{spin}** ({spin_color})\n"
                                  f"{sign}{fmt(cfg, payout)}\nКошелёк: {fmt(cfg, new)}"), mention_author=False)

    @commands.hybrid_command(name="blackjack", aliases=["bj", "блэкджек"],
                             description="Блэкджек против дилера на ставку")
    @app_commands.describe(bet="Ставка")
    @commands.guild_only()
    @requires_hybrid("blackjack")
    async def blackjack(self, ctx: commands.Context, bet: int) -> None:
        cfg, u = await self._take_bet(ctx, bet)
        if cfg is None:
            return
        # Reserve the bet up-front; settle on finish.
        await self.bot.db.econ_add_wallet(ctx.guild.id, ctx.author.id, -bet, command="blackjack")
        view = BlackjackView(self.bot, ctx.author.id, ctx.guild.id, bet, cfg)
        await view.start(ctx)

    @commands.hybrid_command(name="transactions", aliases=["tx", "транзакции"],
                             description="Показать последние начисления и списания")
    @app_commands.describe(user="Фильтр по участнику", limit="Сколько строк показать (1-25)")
    @commands.guild_only()
    @requires_hybrid("transactions")
    async def transactions(
        self, ctx: commands.Context,
        user: Optional[discord.Member] = None,
        limit: commands.Range[int, 1, 25] = 10,
    ) -> None:
        if not await self._enabled(ctx):
            return
        cfg = await self.bot.db.get_econ_config(ctx.guild.id)
        rows = await self.bot.db.list_transactions(
            ctx.guild.id, user_id=user.id if user else None, limit=limit
        )
        if not rows:
            await ctx.reply(embed=embeds.base(description="Транзакций пока нет."),
                            mention_author=False)
            return
        lines = []
        for tx in rows:
            icon = TX_ICONS.get(tx["command"] or "", "•")
            sign = "+" if tx["operation"] == "increment" else "-"
            if tx["operation"] == "transfer":
                sign = "→"
            amount = f"{sign} {fmt(cfg, tx['amount'])}"
            who = ""
            if tx["sender_id"] and tx["receiver_id"] and tx["sender_id"] != tx["receiver_id"]:
                who = f" <@{tx['sender_id']}> → <@{tx['receiver_id']}>"
            elif tx["receiver_id"]:
                who = f" <@{tx['receiver_id']}>"
            note = f" · {tx['note']}" if tx["note"] else ""
            when = f"<t:{tx['created_at']}:R>"
            lines.append(f"{icon} {amount}{who}{note} · {when}")
        title = f"📒 Транзакции {user.display_name}" if user else "📒 Последние транзакции"
        await ctx.reply(embed=embeds.base(title=title, description="\n".join(lines)),
                        mention_author=False)

    # ---- currency managers --------------------------------------------

    @commands.hybrid_group(name="currency", aliases=["валюта"], description="Выдача админской валюты магазина")
    @commands.guild_only()
    @currency_manager_check()
    async def currency(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.reply(embed=embeds.warn("Подкоманды админской валюты магазина: `give`, `take`, `set`."),
                            mention_author=False)

    @currency.command(name="give", description="Выдать админскую валюту магазина")
    @app_commands.describe(user="Кому выдать", amount="Сколько", reason="Причина")
    @currency_manager_check()
    async def currency_give(
        self,
        ctx: commands.Context,
        user: discord.Member,
        amount: commands.Range[int, 1, 1_000_000_000],
        *,
        reason: Optional[str] = None,
    ) -> None:
        if not await self._enabled(ctx):
            return
        if not await self._check_grant_limits(ctx, int(amount)):
            return
        cfg = await self.bot.db.get_econ_config(ctx.guild.id)
        new = await self.bot.db.econ_add_shop_wallet(
            ctx.guild.id, user.id, int(amount), command="shop_grant", actor_id=ctx.author.id,
            note=reason or "Выдача валюты",
        )
        await ctx.reply(embed=embeds.success(
            f"{user.mention} получил {fmt_shop(int(amount))} админ-валюты. Баланс магазина: {fmt_shop(new)}."),
            mention_author=False)
        await self._send_econ_log(ctx.guild, cfg, embeds.base(
            title="💸 Выдача админской валюты",
            description=(f"Менеджер: {ctx.author.mention}\nУчастник: {user.mention}\n"
                         f"Сумма: {fmt_shop(int(amount))}\nПричина: {reason or '—'}"),
        ))

    @currency.command(name="take", description="Забрать админскую валюту магазина")
    @app_commands.describe(user="У кого забрать", amount="Сколько", reason="Причина")
    @currency_manager_check()
    async def currency_take(
        self,
        ctx: commands.Context,
        user: discord.Member,
        amount: commands.Range[int, 1, 1_000_000_000],
        *,
        reason: Optional[str] = None,
    ) -> None:
        if not await self._enabled(ctx):
            return
        cfg = await self.bot.db.get_econ_config(ctx.guild.id)
        new = await self.bot.db.econ_add_shop_wallet(
            ctx.guild.id, user.id, -int(amount), command="shop_take", actor_id=ctx.author.id,
            note=reason or "Списание валюты",
        )
        await ctx.reply(embed=embeds.success(
            f"У {user.mention} списано {fmt_shop(int(amount))} админ-валюты. Баланс магазина: {fmt_shop(new)}."),
            mention_author=False)
        await self._send_econ_log(ctx.guild, cfg, embeds.base(
            title="🧾 Списание админской валюты",
            description=(f"Менеджер: {ctx.author.mention}\nУчастник: {user.mention}\n"
                         f"Сумма: {fmt_shop(int(amount))}\nПричина: {reason or '—'}"),
        ))

    @currency.command(name="set", description="Установить баланс админской валюты магазина")
    @app_commands.describe(user="Кому изменить баланс", balance="Новый баланс магазина", reason="Причина")
    @currency_manager_check()
    async def currency_set(
        self,
        ctx: commands.Context,
        user: discord.Member,
        balance: commands.Range[int, 0, 1_000_000_000],
        *,
        reason: Optional[str] = None,
    ) -> None:
        if not await self._enabled(ctx):
            return
        cfg = await self.bot.db.get_econ_config(ctx.guild.id)
        before = await self.bot.db.get_econ_user(ctx.guild.id, user.id)
        await self.bot.db.econ_update(ctx.guild.id, user.id, shop_wallet=int(balance))
        delta = int(balance) - before["shop_wallet"]
        await self.bot.db.add_transaction(
            ctx.guild.id,
            "increment" if delta >= 0 else "decrement",
            abs(delta),
            command="shop_set",
            sender_id=ctx.author.id,
            receiver_id=user.id,
            note=reason or "Установка баланса",
        )
        await ctx.reply(embed=embeds.success(
            f"Баланс магазина {user.mention} обновлён: {fmt_shop(int(balance))} админ-валюты."),
            mention_author=False)
        await self._send_econ_log(ctx.guild, cfg, embeds.base(
            title="⚙️ Установка админской валюты",
            description=(f"Менеджер: {ctx.author.mention}\nУчастник: {user.mention}\n"
                         f"Баланс: {fmt_shop(before['shop_wallet'])} → {fmt_shop(int(balance))}\n"
                         f"Причина: {reason or '—'}"),
        ))

    # ---- admin config (group) ------------------------------------------

    @commands.hybrid_group(name="economy", description="Настройка экономики (только админ)",
                           guild_only=True)
    @admin_check()
    async def economy(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.reply(embed=embeds.warn(
                "Подкоманды: `toggle`, `give`, `take`, `currency`, `rates`, "
                "`grantsettings`, `manager_*`, `shopsettings`, `additem`, `itemactive`, "
                "`requests`, `request_done`, `request_cancel`, `removeitem`."),
                mention_author=False)

    @economy.command(name="toggle", description="Включить или выключить экономику")
    @app_commands.describe(enabled="Включить экономику?")
    @admin_check()
    async def eco_toggle(self, ctx: commands.Context, enabled: bool) -> None:
        await self.bot.db.update_econ_config(ctx.guild.id, enabled=int(enabled))
        await ctx.reply(embed=embeds.success(
            f"Экономика {'включена' if enabled else 'выключена'}."), mention_author=False)

    @economy.command(name="give", description="Выдать монеты участнику")
    @app_commands.describe(user="Кому", amount="Сколько")
    @admin_check()
    async def eco_give(self, ctx: commands.Context, user: discord.Member, amount: int) -> None:
        cfg = await self.bot.db.get_econ_config(ctx.guild.id)
        await self.bot.db.econ_add_wallet(
            ctx.guild.id, user.id, amount, command="give", actor_id=ctx.author.id
        )
        await ctx.reply(embed=embeds.success(f"{user.mention} получил {fmt(cfg, amount)}."),
                        mention_author=False)

    @economy.command(name="take", description="Забрать монеты у участника")
    @app_commands.describe(user="У кого", amount="Сколько")
    @admin_check()
    async def eco_take(self, ctx: commands.Context, user: discord.Member, amount: int) -> None:
        cfg = await self.bot.db.get_econ_config(ctx.guild.id)
        await self.bot.db.econ_add_wallet(
            ctx.guild.id, user.id, -amount, command="take", actor_id=ctx.author.id
        )
        await ctx.reply(embed=embeds.success(f"У {user.mention} изъято {fmt(cfg, amount)}."),
                        mention_author=False)

    @economy.command(name="currency", description="Эмодзи и название валюты")
    @app_commands.describe(symbol="Эмодзи валюты", name="Название валюты")
    @admin_check()
    async def eco_currency(self, ctx: commands.Context, symbol: str,
                           name: Optional[str] = None) -> None:
        fields = {"symbol": symbol.strip()}
        if name:
            fields["currency_name"] = name.strip()
        await self.bot.db.update_econ_config(ctx.guild.id, **fields)
        await ctx.reply(embed=embeds.success(f"Валюта обновлена: {symbol}"), mention_author=False)

    @economy.command(name="rates", description="Суммы бонусов и лимиты ставок")
    @app_commands.describe(timely="Регулярный бонус", daily="Ежедневный бонус",
                           weekly="Еженедельный бонус", monthly="Ежемесячный бонус",
                           work_min="Работа: мин", work_max="Работа: макс",
                           bet_min="Ставка: мин", bet_max="Ставка: макс",
                           multiplier="Множитель валюты 1-5", commission="Комиссия переводов, %")
    @admin_check()
    async def eco_rates(self, ctx: commands.Context, timely: Optional[int] = None,
                        daily: Optional[int] = None,
                        weekly: Optional[int] = None, monthly: Optional[int] = None,
                        work_min: Optional[int] = None, work_max: Optional[int] = None,
                        bet_min: Optional[int] = None, bet_max: Optional[int] = None,
                        multiplier: Optional[float] = None,
                        commission: Optional[commands.Range[int, 0, 100]] = None) -> None:
        fields = {}
        for key, val in (("timely_amount", timely), ("daily_amount", daily), ("weekly_amount", weekly),
                         ("monthly_amount", monthly), ("work_min", work_min), ("work_max", work_max),
                         ("bet_min", bet_min), ("bet_max", bet_max)):
            if val is not None and val >= 0:
                fields[key] = val
        if multiplier is not None:
            fields["money_multiplier"] = min(5, max(1, multiplier))
        if commission is not None:
            fields["commission_pct"] = int(commission)
        if not fields:
            await ctx.reply(embed=embeds.warn("Укажи хотя бы один параметр."), mention_author=False)
            return
        await self.bot.db.update_econ_config(ctx.guild.id, **fields)
        await ctx.reply(embed=embeds.success("Параметры экономики обновлены."), mention_author=False)

    @economy.command(name="grantsettings", description="Настройки выдачи валюты менеджерами")
    @app_commands.describe(cooldown="Кулдаун между выдачами: 10m, 1h, 0", daily_limit="Суточный лимит выдачи",
                           log_channel="Канал логов выдачи")
    @admin_check()
    async def eco_grantsettings(
        self,
        ctx: commands.Context,
        cooldown: Optional[str] = None,
        daily_limit: Optional[commands.Range[int, 0, 1_000_000_000]] = None,
        log_channel: Optional[discord.TextChannel] = None,
    ) -> None:
        fields = {}
        if cooldown is not None:
            fields["grant_cooldown"] = 0 if cooldown.strip() == "0" else (parse_duration(cooldown) or 0)
        if daily_limit is not None:
            fields["grant_daily_limit"] = int(daily_limit)
        if log_channel is not None:
            fields["grant_log_channel_id"] = log_channel.id
        if not fields:
            cfg = await self.bot.db.get_econ_config(ctx.guild.id)
            grant_logs = f"<#{cfg['grant_log_channel_id']}>" if cfg["grant_log_channel_id"] else "`не задано`"
            await ctx.reply(embed=embeds.base(
                title="💸 Настройки выдачи",
                description=(f"Кулдаун: `{human_time(cfg['grant_cooldown'])}`\n"
                             f"Суточный лимит: {fmt_shop(cfg['grant_daily_limit']) + ' админ-валюты' if cfg['grant_daily_limit'] else '`без лимита`'}\n"
                             f"Логи: {grant_logs}"),
            ), mention_author=False)
            return
        await self.bot.db.update_econ_config(ctx.guild.id, **fields)
        await ctx.reply(embed=embeds.success("Настройки выдачи валюты обновлены."), mention_author=False)

    @economy.command(name="manager_add_role", description="Разрешить роли выдавать валюту через /currency")
    @app_commands.describe(role="Роль менеджеров валюты")
    @admin_check()
    async def eco_manager_add_role(self, ctx: commands.Context, role: discord.Role) -> None:
        await self.bot.db.econ_manager_set(ctx.guild.id, "role", role.id, True)
        await ctx.reply(embed=embeds.success(f"{role.mention} теперь может использовать `/currency`."),
                        mention_author=False)

    @economy.command(name="manager_remove_role", description="Убрать у роли доступ к /currency")
    @app_commands.describe(role="Роль менеджеров валюты")
    @admin_check()
    async def eco_manager_remove_role(self, ctx: commands.Context, role: discord.Role) -> None:
        await self.bot.db.econ_manager_set(ctx.guild.id, "role", role.id, False)
        await ctx.reply(embed=embeds.success(f"{role.mention} больше не может использовать `/currency`."),
                        mention_author=False)

    @economy.command(name="manager_add_user", description="Разрешить человеку выдавать валюту через /currency")
    @app_commands.describe(user="Менеджер валюты")
    @admin_check()
    async def eco_manager_add_user(self, ctx: commands.Context, user: discord.Member) -> None:
        await self.bot.db.econ_manager_set(ctx.guild.id, "user", user.id, True)
        await ctx.reply(embed=embeds.success(f"{user.mention} теперь может использовать `/currency`."),
                        mention_author=False)

    @economy.command(name="manager_remove_user", description="Убрать у человека доступ к /currency")
    @app_commands.describe(user="Менеджер валюты")
    @admin_check()
    async def eco_manager_remove_user(self, ctx: commands.Context, user: discord.Member) -> None:
        await self.bot.db.econ_manager_set(ctx.guild.id, "user", user.id, False)
        await ctx.reply(embed=embeds.success(f"{user.mention} больше не может использовать `/currency`."),
                        mention_author=False)

    @economy.command(name="manager_list", description="Показать менеджеров валюты")
    @admin_check()
    async def eco_manager_list(self, ctx: commands.Context) -> None:
        rows = await self.bot.db.econ_managers(ctx.guild.id)
        if not rows:
            await ctx.reply(embed=embeds.base(description="Менеджеры валюты не настроены."), mention_author=False)
            return
        lines = []
        for row in rows:
            if row["target_type"] == "role":
                lines.append(f"Роль: <@&{row['target_id']}>")
            else:
                lines.append(f"Пользователь: <@{row['target_id']}>")
        await ctx.reply(embed=embeds.base(title="💸 Менеджеры валюты", description="\n".join(lines)),
                        mention_author=False)

    @economy.command(name="shopsettings", description="Настройки админского магазина")
    @app_commands.describe(owner="Ответственный за ручную выдачу", log_channel="Канал логов магазина")
    @admin_check()
    async def eco_shopsettings(
        self,
        ctx: commands.Context,
        owner: Optional[discord.Member] = None,
        log_channel: Optional[discord.TextChannel] = None,
    ) -> None:
        fields = {}
        if owner is not None:
            fields["shop_owner_id"] = owner.id
        if log_channel is not None:
            fields["shop_log_channel_id"] = log_channel.id
        if fields:
            await self.bot.db.update_econ_config(ctx.guild.id, **fields)
            await ctx.reply(embed=embeds.success("Настройки магазина обновлены."), mention_author=False)
            return
        cfg = await self.bot.db.get_econ_config(ctx.guild.id)
        owner_text = f"<@{cfg['shop_owner_id']}>" if cfg["shop_owner_id"] else "`не задано`"
        log_text = f"<#{cfg['shop_log_channel_id']}>" if cfg["shop_log_channel_id"] else "`не задано`"
        await ctx.reply(embed=embeds.base(
            title="🛒 Настройки магазина",
            description=f"Ответственный: {owner_text}\nЛоги: {log_text}",
        ), mention_author=False)

    @economy.command(name="catalog_import", description="Добавить каталог магазина из старых файлов")
    @app_commands.describe(overwrite="Обновить уже импортированные товары ценами/описаниями из каталога?")
    @admin_check()
    async def eco_catalog_import(self, ctx: commands.Context, overwrite: bool = False) -> None:
        created = 0
        updated = 0
        skipped = 0
        for key, name, description, price, category, delivery in SHOP_CATALOG:
            _, was_created, was_updated = await self.bot.db.shop_catalog_upsert(
                ctx.guild.id,
                catalog_key=key,
                name=name,
                description=description,
                price=price,
                category=category,
                delivery_type=delivery,
                overwrite=overwrite,
            )
            if was_created:
                created += 1
            elif was_updated:
                updated += 1
            else:
                skipped += 1
        await ctx.reply(embed=embeds.success(
            f"Каталог магазина синхронизирован: добавлено `{created}`, обновлено `{updated}`, пропущено `{skipped}`.\n"
            "Категории: `kami`, `appearance`, `operations`."
        ), mention_author=False)

    @economy.command(name="additem", description="Добавить товар в магазин")
    @app_commands.describe(name="Название", price="Цена", role="Роль",
                           type="Тип: role/time_role/worker/manual/inventory",
                           duration="Срок: 7d6h, 1w, 30m (для time_role/worker)",
                           limit="Общий лимит покупок (0 = без лимита)",
                           stock="Склад товара (0 = без ограничения)",
                           category="Категория в /shop",
                           income="Доход рабочей роли", income_every="Период дохода: 2h, 1d",
                           response="Ответ после покупки. Переменные: {user}, {role}, {item}, {price}, {currency}",
                           description="Описание")
    @app_commands.choices(type=[
        app_commands.Choice(name="Постоянная роль", value="role"),
        app_commands.Choice(name="Временная роль", value="time_role"),
        app_commands.Choice(name="Рабочая роль", value="worker"),
        app_commands.Choice(name="Ручная выдача", value="manual"),
        app_commands.Choice(name="Инвентарь", value="inventory"),
    ])
    @admin_check()
    async def eco_additem(self, ctx: commands.Context, name: str, price: int,
                          role: Optional[discord.Role] = None,
                          type: str = "role", duration: Optional[str] = None,
                          limit: commands.Range[int, 0, 5000] = 0,
                          stock: commands.Range[int, 0, 5000] = 0,
                          category: str = "roles",
                          income: int = 0, income_every: Optional[str] = None,
                          response: Optional[str] = None,
                          description: Optional[str] = None) -> None:
        item_type = type.strip().lower()
        if item_type not in ("role", "time_role", "worker", "manual", "inventory"):
            await ctx.reply(embed=embeds.error("Тип товара: `role`, `time_role`, `worker`, `manual` или `inventory`."),
                            mention_author=False)
            return
        if item_type in ("role", "time_role", "worker") and role is None:
            await ctx.reply(embed=embeds.error("Для ролевого товара нужно указать `role`."),
                            mention_author=False)
            return
        if role is not None and role >= ctx.guild.me.top_role:
            await ctx.reply(embed=embeds.error("Роль выше роли бота — он не сможет её выдавать."),
                            mention_author=False)
            return
        duration_seconds = parse_duration(duration) if duration else 0
        if item_type == "time_role" and duration_seconds < 600:
            await ctx.reply(embed=embeds.error("Временная роль должна длиться минимум 10 минут."),
                            mention_author=False)
            return
        income_cooldown = parse_duration(income_every) if income_every else 0
        if item_type == "worker" and (income <= 0 or income_cooldown < 7200):
            await ctx.reply(embed=embeds.error(
                "Для рабочей роли нужен доход > 0 и период выплат минимум 2 часа."),
                mention_author=False)
            return
        item_id = await self.bot.db.shop_add(
            ctx.guild.id, name, description, max(1, price), role.id if role else 0, duration_seconds,
            item_type=item_type, purchase_limit=limit, response_message=response,
            income_amount=max(0, income), income_cooldown=income_cooldown,
            category=category.strip().lower()[:32] or "other", stock=stock,
            delivery_type=item_type,
        )
        await self.bot.db.add_transaction(
            ctx.guild.id, "role_listing_created", 0, command="buy",
            sender_id=ctx.author.id, note=f"Создан товар #{item_id}: {name}",
        )
        await ctx.reply(embed=embeds.success(f"Товар **{name}** добавлен (id `{item_id}`)."),
                        mention_author=False)

    @economy.command(name="removeitem", description="Удалить товар из магазина")
    @app_commands.describe(item_id="Номер товара")
    @admin_check()
    async def eco_removeitem(self, ctx: commands.Context, item_id: int) -> None:
        n = await self.bot.db.shop_remove(ctx.guild.id, item_id)
        await ctx.reply(embed=embeds.success("Товар удалён." if n else "Товар не найден."),
                        mention_author=False)

    @economy.command(name="itemactive", description="Включить или выключить товар магазина")
    @app_commands.describe(item_id="Номер товара", active="Показывать и разрешать покупку?")
    @admin_check()
    async def eco_itemactive(self, ctx: commands.Context, item_id: int, active: bool) -> None:
        n = await self.bot.db.shop_update_active(ctx.guild.id, item_id, active)
        await ctx.reply(embed=embeds.success(
            f"Товар {'включён' if active else 'выключен'}." if n else "Товар не найден."),
            mention_author=False)

    @economy.command(name="requests", description="Заявки ручной выдачи из магазина")
    @app_commands.describe(status="pending/done/cancelled/all", limit="Сколько показать")
    @admin_check()
    async def eco_requests(
        self,
        ctx: commands.Context,
        status: str = "pending",
        limit: commands.Range[int, 1, 25] = 10,
    ) -> None:
        normalized = status.strip().lower()
        if normalized == "all":
            normalized = None
        elif normalized not in ("pending", "done", "cancelled"):
            await ctx.reply(embed=embeds.error("Статус: `pending`, `done`, `cancelled` или `all`."),
                            mention_author=False)
            return
        rows = await self.bot.db.shop_requests_list(ctx.guild.id, status=normalized, limit=limit)
        if not rows:
            await ctx.reply(embed=embeds.base(description="Заявок нет."), mention_author=False)
            return
        lines = []
        for r in rows:
            closed = f" · закрыта <t:{r['closed_at']}:R>" if r["closed_at"] else ""
            lines.append(
                f"`#{r['id']}` `{r['status']}` · <@{r['user_id']}> · **{r['item_name']}** "
                f"(товар `#{r['item_id']}`) · <t:{r['created_at']}:R>{closed}"
            )
        await ctx.reply(embed=embeds.base(title="🛒 Заявки магазина", description="\n".join(lines)),
                        mention_author=False)

    @economy.command(name="request_done", description="Отметить заявку магазина выполненной")
    @app_commands.describe(request_id="Номер заявки")
    @admin_check()
    async def eco_request_done(self, ctx: commands.Context, request_id: int) -> None:
        n = await self.bot.db.shop_request_update(ctx.guild.id, request_id, "done", ctx.author.id)
        await ctx.reply(embed=embeds.success("Заявка закрыта как выполненная." if n else "Заявка не найдена."),
                        mention_author=False)

    @economy.command(name="request_cancel", description="Отменить заявку магазина")
    @app_commands.describe(request_id="Номер заявки")
    @admin_check()
    async def eco_request_cancel(self, ctx: commands.Context, request_id: int) -> None:
        n = await self.bot.db.shop_request_update(ctx.guild.id, request_id, "cancelled", ctx.author.id)
        await ctx.reply(embed=embeds.success("Заявка отменена." if n else "Заявка не найдена."),
                        mention_author=False)


# ---------------------------------------------------------------------------
# Shop views
# ---------------------------------------------------------------------------


class ShopBaseView(discord.ui.View):
    def __init__(self, cog: Economy, author_id: int, *, timeout: int = 180):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.author_id = author_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(embed=embeds.error("Это меню магазина открыто не для тебя."), ephemeral=True)
            return False
        return True


class ShopCategorySelect(discord.ui.Select):
    def __init__(self, categories: list[str]):
        options = []
        for key in categories[:25]:
            label, desc = CATEGORY_META.get(key, (key.title(), "Товары этой категории."))
            options.append(discord.SelectOption(label=label, value=key, description=desc[:100]))
        super().__init__(placeholder="Выберите категорию товаров...", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        view: ShopCategoryView = self.view  # type: ignore[assignment]
        category = self.values[0]
        items = await view.cog.bot.db.shop_list(interaction.guild_id, category=category)
        await interaction.response.edit_message(
            embed=await view.cog._shop_category_embed(interaction.guild_id, category),
            view=ShopItemsView(view.cog, view.author_id, category, items),
        )


class ShopCategoryView(ShopBaseView):
    def __init__(self, cog: Economy, author_id: int, categories: list[str]):
        super().__init__(cog, author_id)
        self.add_item(ShopCategorySelect(categories))


class ShopItemSelect(discord.ui.Select):
    def __init__(self, items):
        options = [
            discord.SelectOption(
                label=item["name"][:100],
                value=str(item["id"]),
                description=f"Цена: {item['price']}"[:100],
            )
            for item in items[:25]
        ]
        super().__init__(placeholder="Выберите товар для покупки...", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        view: ShopItemsView = self.view  # type: ignore[assignment]
        item_id = int(self.values[0])
        await interaction.response.edit_message(
            embed=await view.cog._shop_confirm_embed(interaction.guild_id, item_id),
            view=ShopConfirmView(view.cog, view.author_id, view.category, item_id),
        )


class ShopItemsView(ShopBaseView):
    def __init__(self, cog: Economy, author_id: int, category: str, items):
        super().__init__(cog, author_id)
        self.category = category
        if items:
            self.add_item(ShopItemSelect(items))

    @discord.ui.button(label="Назад", style=discord.ButtonStyle.secondary)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        items = await self.cog.bot.db.shop_list(interaction.guild_id)
        categories = sorted({it["category"] for it in items if it["category"]})
        await interaction.response.edit_message(
            embed=self.cog._shop_home_embed(categories),
            view=ShopCategoryView(self.cog, self.author_id, categories),
        )


class ShopConfirmView(ShopBaseView):
    def __init__(self, cog: Economy, author_id: int, category: str, item_id: int):
        super().__init__(cog, author_id)
        self.category = category
        self.item_id = item_id

    @discord.ui.button(label="Купить", style=discord.ButtonStyle.success)
    async def buy_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(embed=embeds.error("Покупка доступна только на сервере."), ephemeral=True)
            return
        embed = await self.cog._purchase_shop_item(interaction.guild, interaction.user, self.item_id)
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(embed=embed, view=self)
        self.stop()

    @discord.ui.button(label="Назад", style=discord.ButtonStyle.secondary)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        items = await self.cog.bot.db.shop_list(interaction.guild_id, category=self.category)
        await interaction.response.edit_message(
            embed=await self.cog._shop_category_embed(interaction.guild_id, self.category),
            view=ShopItemsView(self.cog, self.author_id, self.category, items),
        )


# ---------------------------------------------------------------------------
# Blackjack view
# ---------------------------------------------------------------------------


def _draw() -> int:
    return min(random.randint(1, 13), 10)


def _hand_value(cards: list[int]) -> int:
    total = sum(cards)
    aces = cards.count(1)
    while aces and total + 10 <= 21:
        total += 10
        aces -= 1
    return total


class BlackjackView(discord.ui.View):
    def __init__(self, bot, author_id: int, guild_id: int, bet: int, cfg):
        super().__init__(timeout=120)
        self.bot = bot
        self.author_id = author_id
        self.guild_id = guild_id
        self.bet = bet
        self.cfg = cfg
        self.player = [_draw(), _draw()]
        self.dealer = [_draw(), _draw()]
        self.message: discord.Message | None = None

    async def start(self, ctx: commands.Context) -> None:
        self.message = await ctx.reply(embed=self._embed(), view=self, mention_author=False)
        if _hand_value(self.player) == 21:
            await self._finish("stand")

    def _embed(self, reveal: bool = False) -> discord.Embed:
        dealer = (f"{self.dealer} = {_hand_value(self.dealer)}" if reveal
                  else f"[{self.dealer[0]}, ?]")
        embed = embeds.base(title="🃏 Блэкджек", description=f"Ставка: {fmt(self.cfg, self.bet)}")
        embed.add_field(name="Твои карты", value=f"{self.player} = {_hand_value(self.player)}", inline=False)
        embed.add_field(name="Дилер", value=dealer, inline=False)
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(embed=embeds.error("Это не твоя игра."), ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Ещё", style=discord.ButtonStyle.primary, emoji="🃏")
    async def hit(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.player.append(_draw())
        if _hand_value(self.player) >= 21:
            await self._finish("stand", interaction)
        else:
            await interaction.response.edit_message(embed=self._embed(), view=self)

    @discord.ui.button(label="Хватит", style=discord.ButtonStyle.success, emoji="✋")
    async def stand(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._finish("stand", interaction)

    async def _finish(self, _action: str, interaction: Optional[discord.Interaction] = None) -> None:
        while _hand_value(self.dealer) < 17:
            self.dealer.append(_draw())
        pv, dv = _hand_value(self.player), _hand_value(self.dealer)
        if pv > 21:
            result, payout = "Перебор — проигрыш", 0
        elif dv > 21 or pv > dv:
            result, payout = "Победа!", self.bet * 2
        elif pv == dv:
            result, payout = "Ничья", self.bet
        else:
            result, payout = "Дилер выиграл", 0
        if payout:
            await self.bot.db.econ_add_wallet(
                self.guild_id, self.author_id, payout, command="blackjack"
            )
        net = payout - self.bet
        sign = "+" if net > 0 else ""
        embed = self._embed(reveal=True)
        embed.add_field(name="Итог", value=f"{result} ({sign}{fmt(self.cfg, net)})", inline=False)
        for child in self.children:
            child.disabled = True
        if interaction is not None:
            await interaction.response.edit_message(embed=embed, view=self)
        elif self.message is not None:
            await self.message.edit(embed=embed, view=self)
        self.stop()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Economy(bot))
