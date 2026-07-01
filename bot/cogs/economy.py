"""Admin shop economy: catalog, purchases, inventory, requests, manager grants,
plus the reference shop gameplay — safe, jail/СИЗО, steal, and a profile card."""
from __future__ import annotations

import io
import random
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

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
    ("accessory_4_99", "Аксессуар до 4,99$", "Сертификат на один аксессуар: бейдж, аватарка или эффект профиля стоимостью до 4,99$.", 1500, "accessories", "manual"),
    ("accessory_5_99", "Аксессуар до 5,99$", "Сертификат на один аксессуар: бейдж, аватарка или эффект профиля стоимостью до 5,99$.", 2500, "accessories", "manual"),
    ("accessory_9_99", "Аксессуар до 9,99$", "Сертификат на один аксессуар: бейдж, аватарка или эффект профиля стоимостью до 9,99$.", 3500, "accessories", "manual"),
]

CATEGORY_META = {
    "kami": ("Дары ками", "Основные игровые предметы и перки."),
    "appearance": ("Облик самурая", "Кастомизация для вашего профиля."),
    "operations": ("Операции", "Расходуемые инструменты, повышающие шанс `/кража`."),
    "accessories": ("Аксессуары", "Сертификаты на бейджи, аватарки и эффекты профиля."),
}
CATEGORY_ORDER = ("kami", "appearance", "operations", "accessories")
OPERATION_TOOLS = {
    "crowbar": ("Лом", 10),
    "lockpicks": ("Отмычки", 10),
    "hacker_kit": ("Хакерский набор", 15),
    "jammer": ("Глушилки", 15),
}


def sort_categories(categories: list[str] | set[str]) -> list[str]:
    return sorted(categories, key=lambda key: (CATEGORY_ORDER.index(key) if key in CATEGORY_ORDER else 999, key))


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


async def extend_boost(db, guild_id: int, user_id: int, kind: str, duration: int) -> int:
    row = await db.get_econ_user(guild_id, user_id)
    until = max(int(row[kind] or 0), int(time.time())) + duration
    await db.econ_set_boost(guild_id, user_id, kind, until)
    return until


class Economy(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.temp_role_check.start()

    async def cog_unload(self) -> None:
        self.temp_role_check.cancel()

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

    async def _enabled(self, ctx: commands.Context) -> bool:
        return ctx.guild is not None

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
        role_item = delivery in ("role", "time_role")
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
                guild.id, member.id, item["id"], item["name"], price=int(item["price"]),
                details=item["description"],
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
        categories = sort_categories({it["category"] for it in items if it["category"]})
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
        view = None
        if member.id == ctx.author.id:
            owned = await self.bot.db.shop_owned_keys(ctx.guild.id, member.id)
            usable = [k for k in owned if k in USABLE_ITEMS]
            if usable:
                view = InventoryUseView(member.id, usable)
        await ctx.reply(embed=embeds.base(title=f"🎒 Инвентарь · {member.display_name}",
                                          description="\n".join(lines)),
                        view=view,
                        mention_author=False)

    # ---- safe (сейф) ----------------------------------------------------

    @commands.hybrid_group(name="сейф", aliases=["safe"],
                           description="Личный сейф (защищён от кражи)")
    @commands.guild_only()
    @requires_hybrid("shop")
    async def safe(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is not None:
            return
        if not await self._enabled(ctx):
            return
        row = await self.bot.db.get_econ_user(ctx.guild.id, ctx.author.id)
        await ctx.reply(embed=embeds.base(
            title="🏦 Сейф",
            description=(f"💼 Кошелёк: {fmt_shop(row['shop_wallet'])}\n"
                        f"🔒 Сейф: {fmt_shop(row['safe_balance'])}\n\n"
                        "`/сейф положить <сумма|all>` · `/сейф снять <сумма|all>`")),
            mention_author=False)

    @safe.command(name="положить", aliases=["deposit", "dep"], description="Положить деньги в сейф")
    @app_commands.describe(amount="Сумма или all")
    @requires_hybrid("shop")
    async def safe_deposit(self, ctx: commands.Context, amount: str) -> None:
        row = await self.bot.db.get_econ_user(ctx.guild.id, ctx.author.id)
        n = _parse_amt(amount, row["shop_wallet"])
        if n is None or n <= 0:
            await ctx.reply(embed=embeds.error("Укажи сумму числом или `all`."), mention_author=False)
            return
        if not await self.bot.db.econ_safe_move(ctx.guild.id, ctx.author.id, n, to_safe=True):
            await ctx.reply(embed=embeds.error("В кошельке недостаточно средств."), mention_author=False)
            return
        await ctx.reply(embed=embeds.success(f"В сейф положено {fmt_shop(n)}."), mention_author=False)

    @safe.command(name="снять", aliases=["withdraw", "wd"], description="Снять деньги из сейфа")
    @app_commands.describe(amount="Сумма или all")
    @requires_hybrid("shop")
    async def safe_withdraw(self, ctx: commands.Context, amount: str) -> None:
        row = await self.bot.db.get_econ_user(ctx.guild.id, ctx.author.id)
        n = _parse_amt(amount, row["safe_balance"])
        if n is None or n <= 0:
            await ctx.reply(embed=embeds.error("Укажи сумму числом или `all`."), mention_author=False)
            return
        if not await self.bot.db.econ_safe_move(ctx.guild.id, ctx.author.id, n, to_safe=False):
            await ctx.reply(embed=embeds.error("В сейфе недостаточно средств."), mention_author=False)
            return
        await ctx.reply(embed=embeds.success(f"Из сейфа снято {fmt_shop(n)}."), mention_author=False)

    # ---- steal (кража) --------------------------------------------------

    @commands.hybrid_command(name="кража", aliases=["steal", "украсть", "ограбить"],
                             description="Ограбить администратора (рискованно)")
    @app_commands.describe(user="Кого ограбить")
    @commands.guild_only()
    @requires_hybrid("shop")
    async def steal(self, ctx: commands.Context, user: discord.Member) -> None:
        if not await self._enabled(ctx):
            return
        if user.bot or user.id == ctx.author.id:
            await ctx.reply(embed=embeds.error("Неудачная цель."), mention_author=False)
            return
        if not is_admin_access(user, ctx.guild):
            await ctx.reply(embed=embeds.error("Кража доступна только против администратора сервера."),
                            mention_author=False)
            return
        cfg = await self.bot.db.get_econ_config(ctx.guild.id)
        now = int(time.time())
        jail = await self.bot.db.econ_jail_remaining(ctx.guild.id, ctx.author.id)
        if jail > 0:
            await ctx.reply(embed=embeds.warn(f"Ты в тюрьме ещё **{human_time(jail)}**."),
                            mention_author=False)
            return
        thief = await self.bot.db.get_econ_user(ctx.guild.id, ctx.author.id)
        left = cfg["steal_cooldown"] - (now - thief["last_steal"])
        if left > 0:
            await ctx.reply(embed=embeds.warn(f"Залечь на дно ещё на **{human_time(left)}**."),
                            mention_author=False)
            return
        victim = await self.bot.db.get_econ_user(ctx.guild.id, user.id)
        if victim["steal_insurance_until"] > now:
            await ctx.reply(embed=embeds.warn("У цели активна страховка от кражи."), mention_author=False)
            return
        if cfg["grant_protect_hours"] and victim["last_grant"] and \
                now - victim["last_grant"] < cfg["grant_protect_hours"] * 3600:
            await ctx.reply(embed=embeds.warn("Цель под защитой после недавней выдачи валюты."),
                            mention_author=False)
            return
        pot = victim["shop_wallet"]  # safe is protected
        if pot < cfg["steal_min_amount"]:
            await ctx.reply(embed=embeds.warn(
                f"У цели мало в кошельке (нужно ≥ {fmt_shop(cfg['steal_min_amount'])})."),
                mention_author=False)
            return
        tool_key = await self.bot.db.shop_inventory_use_first_by_keys(ctx.guild.id, ctx.author.id, OPERATION_TOOLS.keys())
        tool_bonus = OPERATION_TOOLS.get(tool_key, ("", 0))[1] if tool_key else 0
        chance = min(95, cfg["steal_chance"] + (20 if thief["steal_boost_until"] > now else 0) + tool_bonus)
        if random.randint(1, 100) <= chance:
            stolen = min(pot, random.randint(cfg["steal_min_amount"],
                                             max(cfg["steal_min_amount"], pot * 40 // 100)))
            await self.bot.db.econ_add_shop_wallet(ctx.guild.id, user.id, -stolen,
                                                   command="кража", note=f"Ограбил {ctx.author}")
            await self.bot.db.econ_add_shop_wallet(ctx.guild.id, ctx.author.id, stolen,
                                                   command="кража", note=f"Ограбил {user}")
            await self.bot.db.econ_record_steal(ctx.guild.id, ctx.author.id, True)
            tool_text = f"\nИнструмент: **{OPERATION_TOOLS[tool_key][0]}** (+{tool_bonus}%)." if tool_key else ""
            await ctx.reply(embed=embeds.success(
                f"🦹 Успех! Ты обчистил {user.mention} на {fmt_shop(stolen)}.{tool_text}"), mention_author=False)
        else:
            await self.bot.db.econ_record_steal(ctx.guild.id, ctx.author.id, False)
            mins = random.randint(cfg["jail_min_minutes"], cfg["jail_max_minutes"])
            await self.bot.db.econ_put_in_jail(ctx.guild.id, ctx.author.id, mins, "Тюрьма")
            tool_text = f"\nИнструмент **{OPERATION_TOOLS[tool_key][0]}** был израсходован." if tool_key else ""
            await ctx.reply(embed=embeds.error(
                f"🚨 Тебя поймали и отправили в тюрьму на **{mins}** минут.{tool_text}"), mention_author=False)

    # ---- perks (активация из инвентаря) --------------------------------

    @commands.hybrid_group(name="perk", aliases=["перк"], description="Активировать перки из инвентаря")
    @commands.guild_only()
    @requires_hybrid("shop")
    async def perk(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is not None:
            return
        owned = await self.bot.db.shop_owned_keys(ctx.guild.id, ctx.author.id)
        names = {"steal_insurance": "Страховка от кражи (`/perk страховка`)",
                 "steal_boost": "Повышенный шанс на кражу (`/perk буст`)",
                 "jail_free": "Карта выхода (`/perk выйти`)"}
        avail = [v for k, v in names.items() if k in owned]
        await ctx.reply(embed=embeds.base(
            title="🎁 Перки в инвентаре",
            description="\n".join(f"• {x}" for x in avail) or "Нет активируемых перков."),
            mention_author=False)

    @perk.command(name="страховка", aliases=["insurance"], description="Активировать страховку от кражи (5 суток)")
    @requires_hybrid("shop")
    async def perk_insurance(self, ctx: commands.Context) -> None:
        if not await self.bot.db.shop_inventory_use_by_key(ctx.guild.id, ctx.author.id, "steal_insurance"):
            await ctx.reply(embed=embeds.error("Нет «Страховки от кражи» в инвентаре."), mention_author=False)
            return
        await extend_boost(self.bot.db, ctx.guild.id, ctx.author.id, "steal_insurance_until", 5 * 86400)
        await ctx.reply(embed=embeds.success("🛡️ Страховка от кражи активирована на 5 суток."),
                        mention_author=False)

    @perk.command(name="буст", aliases=["boost"], description="Активировать повышенный шанс кражи (24 ч)")
    @requires_hybrid("shop")
    async def perk_boost(self, ctx: commands.Context) -> None:
        if not await self.bot.db.shop_inventory_use_by_key(ctx.guild.id, ctx.author.id, "steal_boost"):
            await ctx.reply(embed=embeds.error("Нет «Повышенного шанса кражи» в инвентаре."), mention_author=False)
            return
        await extend_boost(self.bot.db, ctx.guild.id, ctx.author.id, "steal_boost_until", 86400)
        await ctx.reply(embed=embeds.success("🦹 Повышенный шанс кражи активирован на 24 часа."),
                        mention_author=False)

    @perk.command(name="выйти", aliases=["free"], description="Выйти из тюрьмы по «Карте выхода»")
    @requires_hybrid("shop")
    async def perk_free(self, ctx: commands.Context) -> None:
        if await self.bot.db.econ_jail_remaining(ctx.guild.id, ctx.author.id) <= 0:
            await ctx.reply(embed=embeds.warn("Ты сейчас не в тюрьме."), mention_author=False)
            return
        if not await self.bot.db.shop_inventory_use_by_key(ctx.guild.id, ctx.author.id, "jail_free"):
            await ctx.reply(embed=embeds.error("Нет «Карты выхода» в инвентаре."), mention_author=False)
            return
        await self.bot.db.econ_release_jail(ctx.guild.id, ctx.author.id)
        await ctx.reply(embed=embeds.success("🔓 Ты досрочно вышел из тюрьмы."), mention_author=False)

    # ---- admin profile --------------------------------------------------

    @commands.hybrid_command(name="admin_profile", aliases=["профиль", "ap"],
                             description="Профиль администратора (баланс, ограбления, перки)")
    @app_commands.describe(user="Чей профиль показать")
    @commands.guild_only()
    @requires_hybrid("shop")
    async def admin_profile(self, ctx: commands.Context, user: Optional[discord.Member] = None) -> None:
        if not await self._enabled(ctx):
            return
        if ctx.interaction and not ctx.interaction.response.is_done():
            await ctx.defer()
        member = user or ctx.author
        await self.bot.db.econ_jail_remaining(ctx.guild.id, member.id)  # auto-release if expired
        row = await self.bot.db.get_econ_user(ctx.guild.id, member.id)
        embed, file = await build_profile(self.bot, member, row)
        view = AdminProfileView(ctx.author.id, member.id)
        if ctx.interaction:
            await ctx.send(embed=embed, file=file, view=view)
        else:
            await ctx.reply(embed=embed, file=file, view=view, mention_author=False)

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
        await self.bot.db.econ_set_grant_protection(ctx.guild.id, user.id)
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
        if delta > 0:
            await self.bot.db.econ_set_grant_protection(ctx.guild.id, user.id)
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
                "Подкоманды: `grantsettings`, `manager_add_role`, `manager_add_user`, "
                "`shopsettings`, `jailsettings`, `stealsettings`, `catalog_import`, "
                "`additem`, `itemactive`, `requests`, `request_done`, `request_cancel`, `removeitem`."),
                mention_author=False)

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

    @economy.command(name="jailsettings", description="Настройки тюрьмы/СИЗО (мин/макс срок)")
    @app_commands.describe(min_minutes="Мин. время в тюрьме (минуты)",
                           max_minutes="Макс. время в тюрьме (минуты)")
    @admin_check()
    async def eco_jailsettings(
        self, ctx: commands.Context,
        min_minutes: Optional[commands.Range[int, 0, 100000]] = None,
        max_minutes: Optional[commands.Range[int, 0, 100000]] = None,
    ) -> None:
        cfg = await self.bot.db.get_econ_config(ctx.guild.id)
        cur_min, cur_max = cfg["jail_min_minutes"], cfg["jail_max_minutes"]
        new_min = cur_min if min_minutes is None else int(min_minutes)
        new_max = cur_max if max_minutes is None else int(max_minutes)
        if min_minutes is None and max_minutes is None:
            await ctx.reply(embed=embeds.base(
                title="🚔 Настройки тюрьмы",
                description=(f"⏳ Мин. время: **{cur_min}** минут\n"
                             f"⌛ Макс. время: **{cur_max}** минут")),
                mention_author=False)
            return
        if new_max < new_min:
            await ctx.reply(embed=embeds.error(
                f"Макс. время ({new_max}) не может быть меньше минимального ({new_min})."),
                mention_author=False)
            return
        await self.bot.db.update_econ_config(ctx.guild.id, jail_min_minutes=new_min,
                                             jail_max_minutes=new_max)
        await ctx.reply(embed=embeds.success(
            f"Тюрьма обновлена: мин. **{new_min}** мин, макс. **{new_max}** мин."),
            mention_author=False)

    @economy.command(name="stealsettings", description="Настройки кражи (шанс, кулдаун, мин. сумма)")
    @app_commands.describe(chance="Шанс успешного ограбления, % (0–100)",
                           cooldown="Кулдаун кражи: 3h, 30m",
                           min_amount="Мин. сумма для кражи",
                           protect_hours="Защита после выдачи валюты, часов")
    @admin_check()
    async def eco_stealsettings(
        self, ctx: commands.Context,
        chance: Optional[commands.Range[int, 0, 100]] = None,
        cooldown: Optional[str] = None,
        min_amount: Optional[commands.Range[int, 0, 1_000_000_000]] = None,
        protect_hours: Optional[commands.Range[int, 0, 720]] = None,
    ) -> None:
        fields = {}
        if chance is not None:
            fields["steal_chance"] = int(chance)
        if cooldown is not None:
            secs = 0 if cooldown.strip() == "0" else parse_duration(cooldown)
            if secs is None:
                await ctx.reply(embed=embeds.error("Неверный кулдаун. Примеры: `3h`, `30m`, `0`."),
                                mention_author=False)
                return
            fields["steal_cooldown"] = secs
        if min_amount is not None:
            fields["steal_min_amount"] = int(min_amount)
        if protect_hours is not None:
            fields["grant_protect_hours"] = int(protect_hours)
        if not fields:
            cfg = await self.bot.db.get_econ_config(ctx.guild.id)
            await ctx.reply(embed=embeds.base(
                title="🦹 Настройки кражи",
                description=(f"🎯 Шанс ограбления: **{cfg['steal_chance']}%**\n"
                             f"⏱️ Кулдаун: `{human_time(cfg['steal_cooldown'])}`\n"
                             f"💰 Мин. сумма: **{cfg['steal_min_amount']}**\n"
                             f"🛡️ Защита после выдачи: **{cfg['grant_protect_hours']}** ч")),
                mention_author=False)
            return
        await self.bot.db.update_econ_config(ctx.guild.id, **fields)
        await ctx.reply(embed=embeds.success("Настройки кражи обновлены."), mention_author=False)

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
            "Категории: `kami`, `appearance`, `operations`, `accessories`."
        ), mention_author=False)

    @economy.command(name="additem", description="Добавить товар в магазин")
    @app_commands.describe(name="Название", price="Цена", role="Роль",
                           type="Тип: role/time_role/manual/inventory",
                           duration="Срок: 7d6h, 1w, 30m (для time_role)",
                           limit="Общий лимит покупок (0 = без лимита)",
                           stock="Склад товара (0 = без ограничения)",
                           category="Категория в /shop",
                           response="Ответ после покупки. Переменные: {user}, {role}, {item}, {price}, {currency}",
                           description="Описание")
    @app_commands.choices(type=[
        app_commands.Choice(name="Постоянная роль", value="role"),
        app_commands.Choice(name="Временная роль", value="time_role"),
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
                          response: Optional[str] = None,
                          description: Optional[str] = None) -> None:
        item_type = type.strip().lower()
        if item_type not in ("role", "time_role", "manual", "inventory"):
            await ctx.reply(embed=embeds.error("Тип товара: `role`, `time_role`, `manual` или `inventory`."),
                            mention_author=False)
            return
        if item_type in ("role", "time_role") and role is None:
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
        item_id = await self.bot.db.shop_add(
            ctx.guild.id, name, description, max(1, price), role.id if role else 0, duration_seconds,
            item_type=item_type, purchase_limit=limit, response_message=response,
            income_amount=0, income_cooldown=0,
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
            price = f" · {fmt_shop(r['price'])}" if r["price"] else ""
            lines.append(
                f"`#{r['id']}` `{r['status']}` · <@{r['user_id']}> · **{r['item_name']}** "
                f"(товар `#{r['item_id']}`){price} · <t:{r['created_at']}:R>{closed}"
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
    @app_commands.describe(request_id="Номер заявки", refund="Вернуть покупателю цену товара?")
    @admin_check()
    async def eco_request_cancel(self, ctx: commands.Context, request_id: int, refund: bool = True) -> None:
        req = await self.bot.db.shop_request_get(ctx.guild.id, request_id)
        if req is None:
            await ctx.reply(embed=embeds.success("Заявка не найдена."), mention_author=False)
            return
        if req["status"] != "pending":
            await ctx.reply(embed=embeds.warn("Возврат доступен только для pending-заявок."), mention_author=False)
            return
        refunded = 0
        if refund:
            refunded = int(req["price"] or 0)
            if not refunded:
                item = await self.bot.db.shop_get(ctx.guild.id, req["item_id"])
                refunded = int(item["price"]) if item is not None else 0
            if refunded:
                await self.bot.db.econ_add_shop_wallet(
                    ctx.guild.id, req["user_id"], refunded,
                    command="shop_refund", actor_id=ctx.author.id,
                    note=f"Возврат за отмену заявки #{request_id}: {req['item_name']}",
                )
        n = await self.bot.db.shop_request_update(ctx.guild.id, request_id, "cancelled", ctx.author.id)
        extra = f" Возврат: {fmt_shop(refunded)}." if refunded else ""
        await ctx.reply(embed=embeds.success(("Заявка отменена." + extra) if n else "Заявка не найдена."),
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








# ---------------------------------------------------------------------------
# Safe / steal / profile helpers
# ---------------------------------------------------------------------------

from pathlib import Path as _Path  # noqa: E402

_BANNER_DIR = _Path(__file__).resolve().parent.parent / "assets" / "banners"
APPEARANCE_NAMES = {
    "weapon_banner": "Оружейная комната",
    "auto_banner": "Ночной дрифт",
    "meow_banner": "Кошачья мята",
    "priroda_banner": "Дух природы",
    "katana_banner": "Клинок самурая",
}


def _parse_amt(text: Optional[str], maximum: int) -> Optional[int]:
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


def _banner_bytes(key: Optional[str]) -> Optional[bytes]:
    if not key:
        return None
    p = _BANNER_DIR / f"{key}.png"
    try:
        return p.read_bytes() if p.exists() else None
    except OSError:
        return None


async def build_profile(bot, member: discord.Member, row) -> tuple[discord.Embed, discord.File]:
    now = int(time.time())
    accent = member.color.to_rgb() if member.color.value else (88, 101, 242)
    total = row["shop_wallet"] + row["safe_balance"]
    attempts = row["steal_success"] + row["steal_fail"]

    embed = discord.Embed(color=member.color if member.color.value else None)
    embed.set_author(name=f"Profile · {member.display_name}", icon_url=member.display_avatar.url)
    embed.add_field(
        name="👛 Баланс",
        value=(f"Баланс: **{row['shop_wallet']:,}**\nСейф: **{row['safe_balance']:,}**\n"
               f"Общий баланс: **{total:,}**").replace(",", " "),
        inline=False)
    jailed = "🔒 в тюрьме" if row["jail_until"] and row["jail_until"] > now else ""
    embed.add_field(
        name="🦹 Ограбления",
        value=(f"Всего: **{attempts}**\nУспешные: **{row['steal_success']}**\n"
               f"Провальные: **{row['steal_fail']}**\n"
               f"Время в тюрьме: **{human_time(row['total_jail_time'] * 60)}** {jailed}"),
        inline=True)
    perks = []
    if row["steal_boost_until"] > now:
        perks.append("Повышенный шанс на кражу")
    if row["steal_insurance_until"] > now:
        perks.append("Страховка от ограблений")
    embed.add_field(name="🎁 Бонусные перки",
                    value="\n".join(f"• {p}" for p in perks) or "—", inline=True)

    try:
        av = await member.display_avatar.replace(size=128).read()
    except Exception:
        av = None
    data = await banners.profile_banner(av, _banner_bytes(row["active_banner"]), accent)
    file = discord.File(io.BytesIO(data), filename="profile.png")
    embed.set_image(url="attachment://profile.png")
    return embed, file


async def _economy_leaderboard_payload(bot: commands.Bot, guild: discord.Guild, kind: str):
    if kind == "robbery":
        rows = await bot.db.econ_robbery_leaderboard(guild.id, 10)  # type: ignore[attr-defined]
        data = [(r["user_id"], r["steal_success"]) for r in rows]
        title, subtitle = "Топ грабителей", "по успешным кражам"
        value = lambda amount: f"{amount} успешных"
        empty = "Пока никто не грабил."
    else:
        rows = await bot.db.econ_leaderboard(guild.id, 10)  # type: ignore[attr-defined]
        cfg = await bot.db.get_econ_config(guild.id)  # type: ignore[attr-defined]
        data = [(r["user_id"], r["shop_wallet"] + r["safe_balance"]) for r in rows]
        title, subtitle = "Топ админ-валюты", "кошелек + сейф"
        value = lambda amount: fmt(cfg, amount)
        empty = "Пока ни у кого нет админ-валюты."
    if not rows:
        return {"embed": embeds.base(title=f"🏆 {title}", description=empty)}

    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for index, (user_id, amount) in enumerate(data):
        place = medals[index] if index < 3 else f"**{index + 1}.**"
        member = guild.get_member(user_id)
        name = member.mention if member is not None else f"<@{user_id}>"
        lines.append(f"{place} {name} — {value(amount)}")
    embed = embeds.base(title=f"🏆 {title}", description="\n".join(lines))
    embed.set_footer(text=subtitle)
    return {"embed": embed}


class AdminProfileView(discord.ui.View):
    def __init__(self, author_id: int, member_id: int) -> None:
        super().__init__(timeout=180)
        self.author_id = author_id
        self.member_id = member_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                embed=embeds.error("Это не ваш профиль."), ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Магазин", emoji="🛒", style=discord.ButtonStyle.secondary)
    async def shop(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        cog = interaction.client.get_cog("Economy")
        items = await interaction.client.db.shop_list(interaction.guild_id)
        cats = sort_categories({it["category"] for it in items if it["category"]})
        if not cats:
            await interaction.response.send_message(
                embed=embeds.base(description="Магазин пуст. Админам: `/economy catalog_import`."),
                ephemeral=True)
            return
        await interaction.response.send_message(
            embed=cog._shop_home_embed(cats),
            view=ShopCategoryView(cog, interaction.user.id, cats), ephemeral=True)

    @discord.ui.button(label="Инвентарь", emoji="🎒", style=discord.ButtonStyle.secondary)
    async def inventory(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.member_id:
            await interaction.response.send_message(
                embed=embeds.error("Открыть инвентарь можно только в своём профиле."), ephemeral=True)
            return
        owned = await interaction.client.db.shop_owned_keys(interaction.guild_id, self.member_id)
        usable = [k for k in owned if k in USABLE_ITEMS]
        if not usable:
            await interaction.response.send_message(
                embed=embeds.base(description="В инвентаре нет используемых предметов.\n"
                                  "Фоны профиля устанавливаются кнопкой **🖼️ Фон**."),
                ephemeral=True)
            return
        await interaction.response.send_message(
            embed=embeds.base(title="🎒 Инвентарь", description="Выберите предмет, чтобы использовать:"),
            view=InventoryUseView(self.member_id, usable), ephemeral=True)

    @discord.ui.button(label="Лидерборд", emoji="🏆", style=discord.ButtonStyle.primary)
    async def leaderboard(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        guild = interaction.guild
        if guild is None:
            return
        payload = await _economy_leaderboard_payload(interaction.client, guild, "balance")
        await interaction.response.send_message(**payload, view=EconomyLeaderboardView())

    @discord.ui.button(label="Сменить фон", emoji="🖼️", style=discord.ButtonStyle.success)
    async def change_bg(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.member_id:
            await interaction.response.send_message(
                embed=embeds.error("Менять фон можно только в своём профиле."), ephemeral=True)
            return
        owned = await interaction.client.db.shop_owned_keys(interaction.guild_id, self.member_id)
        banner_keys = [k for k in owned if k in APPEARANCE_NAMES]
        if not banner_keys:
            await interaction.response.send_message(
                embed=embeds.warn("У вас нет купленных фонов (категория `appearance` в `/shop`)."),
                ephemeral=True)
            return
        await interaction.response.send_message(
            embed=embeds.base(description="Выберите фон профиля:"),
            view=_BannerSelectView(self.member_id, banner_keys), ephemeral=True)


class EconomyLeaderboardSelect(discord.ui.Select):
    def __init__(self) -> None:
        super().__init__(
            placeholder="Выберите критерий топа...",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label="Баланс", value="balance", emoji="💰", description="Кошелек + сейф"),
                discord.SelectOption(label="Кражи", value="robbery", emoji="🦹", description="Успешные ограбления"),
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or interaction.message is None:
            return
        payload = await _economy_leaderboard_payload(interaction.client, interaction.guild, self.values[0])
        for option in self.options:
            option.default = option.value == self.values[0]
        await interaction.response.defer()
        await interaction.message.edit(
            embed=payload.get("embed"),
            attachments=[payload["file"]] if "file" in payload else [],
            view=self.view,
        )


class EconomyLeaderboardView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=300)
        self.add_item(EconomyLeaderboardSelect())


class _BannerSelectView(discord.ui.View):
    def __init__(self, member_id: int, keys: list[str]) -> None:
        super().__init__(timeout=120)
        self.add_item(_BannerSelect(member_id, keys))


class _BannerSelect(discord.ui.Select):
    def __init__(self, member_id: int, keys: list[str]) -> None:
        self.member_id = member_id
        options = [discord.SelectOption(label=APPEARANCE_NAMES[k], value=k) for k in keys]
        options.append(discord.SelectOption(label="Стандартный фон", value="__default__"))
        super().__init__(placeholder="Фон профиля…", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        key = None if self.values[0] == "__default__" else self.values[0]
        await interaction.client.db.econ_update(interaction.guild_id, self.member_id, active_banner=key)
        label = "стандартный" if key is None else APPEARANCE_NAMES.get(key, key)
        await interaction.response.edit_message(
            embed=embeds.success(f"Фон профиля изменён на: **{label}**. Откройте `/admin_profile`."),
            view=None)


# ---------------------------------------------------------------------------
# Inventory usage (buttons): perks, jail coupon, black box
# ---------------------------------------------------------------------------

USABLE_ITEMS = {
    "steal_insurance": ("🛡️", "Страховка от кражи"),
    "steal_boost": ("🦹", "Повышенный шанс кражи"),
    "jail_free": ("🔓", "Карта выхода (выйти из тюрьмы)"),
    "jail_coupon": ("⚖️", "Купон в СИЗО (посадить админа)"),
    "black_box": ("🎁", "Чёрный ящик"),
}
JAIL_COUPON_MINUTES = 5 * 24 * 60  # СИЗО — 5 суток


async def _open_black_box(db, guild: discord.Guild, member_id: int) -> discord.Embed:
    now = int(time.time())
    r = random.random()
    if r < 0.30:
        amt = random.randint(1000, 20000)
        await db.econ_add_shop_wallet(guild.id, member_id, amt, command="black_box",
                                      note="Чёрный ящик")
        return embeds.success(f"🎁 Чёрный ящик: тебе повезло — **+{fmt_shop(amt)}**!")
    if r < 0.45:
        await extend_boost(db, guild.id, member_id, "steal_boost_until", 86400)
        return embeds.success("🎁 Из ящика выпал **буст кражи** на 24 часа!")
    if r < 0.55:
        await extend_boost(db, guild.id, member_id, "steal_insurance_until", 3 * 86400)
        return embeds.success("🎁 Из ящика выпала **страховка от кражи** на 3 суток!")
    if r < 0.75:
        u = await db.get_econ_user(guild.id, member_id)
        fine = min(u["shop_wallet"], random.randint(500, 5000))
        await db.econ_add_shop_wallet(guild.id, member_id, -fine, command="black_box",
                                      note="Чёрный ящик (штраф)")
        return embeds.error(f"🎁 Неудача — ящик с подвохом: **−{fmt_shop(fine)}**.")
    if r < 0.90:
        cfg = await db.get_econ_config(guild.id)
        mins = random.randint(cfg["jail_min_minutes"], cfg["jail_max_minutes"])
        await db.econ_put_in_jail(guild.id, member_id, mins, "Тюрьма")
        return embeds.error(f"🎁 Ловушка! Ты угодил в тюрьму на **{mins}** минут.")
    return embeds.warn("🎁 Чёрный ящик оказался пустым… не повезло.")


class InventoryUseView(discord.ui.View):
    def __init__(self, member_id: int, keys: list[str]) -> None:
        super().__init__(timeout=120)
        self.add_item(InventoryUseSelect(member_id, keys))


class InventoryUseSelect(discord.ui.Select):
    def __init__(self, member_id: int, keys: list[str]) -> None:
        self.member_id = member_id
        options = [discord.SelectOption(label=USABLE_ITEMS[k][1], value=k, emoji=USABLE_ITEMS[k][0])
                   for k in keys]
        super().__init__(placeholder="Что использовать…", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        key = self.values[0]
        db = interaction.client.db
        gid, mid = interaction.guild_id, self.member_id

        if key == "jail_coupon":
            await interaction.response.edit_message(
                embed=embeds.base(description="Выберите администратора, которого отправить в СИЗО на 5 суток:"),
                view=JailCouponView(mid))
            return

        if key == "jail_free" and await db.econ_jail_remaining(gid, mid) <= 0:
            await interaction.response.edit_message(
                embed=embeds.warn("Ты сейчас не в тюрьме — «Карта выхода» не нужна."), view=None)
            return

        if not await db.shop_inventory_use_by_key(gid, mid, key):
            await interaction.response.edit_message(
                embed=embeds.error("Предмет не найден или уже использован."), view=None)
            return

        if key == "steal_insurance":
            await extend_boost(db, gid, mid, "steal_insurance_until", 5 * 86400)
            res = embeds.success("🛡️ Страховка от кражи активирована на 5 суток.")
        elif key == "steal_boost":
            await extend_boost(db, gid, mid, "steal_boost_until", 86400)
            res = embeds.success("🦹 Повышенный шанс кражи активирован на 24 часа.")
        elif key == "jail_free":
            await db.econ_release_jail(gid, mid)
            res = embeds.success("🔓 Ты досрочно вышел из тюрьмы.")
        elif key == "black_box":
            res = await _open_black_box(db, interaction.guild, mid)
        else:
            res = embeds.error("Неизвестный предмет.")
        await interaction.response.edit_message(embed=res, view=None)


class JailCouponView(discord.ui.View):
    def __init__(self, member_id: int) -> None:
        super().__init__(timeout=120)
        self.add_item(JailCouponSelect(member_id))


class JailCouponSelect(discord.ui.UserSelect):
    def __init__(self, member_id: int) -> None:
        self.member_id = member_id
        super().__init__(placeholder="Кого в СИЗО…", min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        victim = self.values[0]
        db = interaction.client.db
        gid, mid = interaction.guild_id, self.member_id
        if victim.bot or victim.id == mid:
            await interaction.response.edit_message(embed=embeds.error("Неудачная цель."), view=None)
            return
        if not isinstance(victim, discord.Member) or not is_admin_access(victim, interaction.guild):
            await interaction.response.edit_message(
                embed=embeds.error("Купон СИЗО можно применить только к администратору сервера."),
                view=None,
            )
            return
        if not await db.shop_inventory_use_by_key(gid, mid, "jail_coupon"):
            await interaction.response.edit_message(
                embed=embeds.error("«Купон в СИЗО» не найден или уже использован."), view=None)
            return
        await db.econ_put_in_jail(gid, victim.id, JAIL_COUPON_MINUTES, "СИЗО")
        await interaction.response.edit_message(
            embed=embeds.success(f"⚖️ {victim.mention} отправлен в СИЗО на **5 суток**."), view=None)
        try:
            await victim.send(embed=embeds.warn(
                f"Вас отправили в СИЗО на сервере **{interaction.guild.name}** сроком на 5 суток."))
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Economy(bot))
