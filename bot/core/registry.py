"""Central registry of every permission-managed command.

The ``/config`` menu is built dynamically from this registry, so adding a new
manageable command only requires:
  1. registering it here, and
  2. guarding the command callback with ``@requires("command_key")``.

``key`` is a stable identifier stored in the database. ``label``/``description``
are shown in the config UI. Commands are grouped into ``CATEGORIES`` purely for
presentation.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ManagedCommand:
    key: str
    label: str
    description: str
    # Default state when a guild has never configured it.
    default_enabled: bool = True


@dataclass(frozen=True)
class Category:
    key: str
    label: str
    emoji: str
    commands: tuple[ManagedCommand, ...]


CATEGORIES: tuple[Category, ...] = (
    Category(
        key="userinfo",
        label="Информация о пользователях",
        emoji="🪪",
        commands=(
            ManagedCommand("avatar", "/avatar", "Показать аватар пользователя"),
            ManagedCommand("banner", "/banner", "Показать баннер профиля"),
            ManagedCommand("userinfo", "/userinfo", "Подробная информация о пользователе"),
            ManagedCommand("serverinfo", "/serverinfo", "Информация о сервере"),
        ),
    ),
    Category(
        key="moderation",
        label="Модерация",
        emoji="🛡️",
        commands=(
            ManagedCommand("ban", "/ban", "Забанить пользователя"),
            ManagedCommand("unban", "/unban", "Разбанить пользователя"),
            ManagedCommand("tempban", "/tempban", "Временный бан с авто-разбаном"),
            ManagedCommand("kick", "/kick", "Кикнуть пользователя"),
            ManagedCommand("timeout", "/timeout", "Выдать мут (тайм-аут)"),
            ManagedCommand("untimeout", "/untimeout", "Снять мут"),
            ManagedCommand("warn", "/warn", "Выдать предупреждение"),
            ManagedCommand("warnings", "/warnings", "Показать предупреждения"),
            ManagedCommand("clearwarns", "/clearwarns", "Снять предупреждения"),
            ManagedCommand("warnpunish", "/warnpunish", "Авто-наказания за предупреждения"),
            ManagedCommand("case", "/case", "Показать кейс по номеру"),
            ManagedCommand("history", "/history", "История наказаний пользователя"),
            ManagedCommand("note", "/note", "Приватная заметка о пользователе"),
            ManagedCommand("notes", "/notes", "Показать заметки о пользователе"),
            ManagedCommand("purge", "/purge", "Удалить пачку сообщений"),
            ManagedCommand("slowmode", "/slowmode", "Медленный режим в канале"),
            ManagedCommand("lock", "/lock", "Закрыть канал"),
            ManagedCommand("unlock", "/unlock", "Открыть канал"),
        ),
    ),
    Category(
        key="roles",
        label="Роли",
        emoji="🎭",
        commands=(
            ManagedCommand("role_add", "/role add", "Выдать роль пользователю"),
            ManagedCommand("role_remove", "/role remove", "Снять роль с пользователя"),
            ManagedCommand("selfroles", "/selfroles", "Панели само-выдаваемых ролей"),
        ),
    ),
    Category(
        key="engagement",
        label="Приветствия и авто-роль",
        emoji="👋",
        commands=(
            ManagedCommand("welcome", "/welcome", "Приветствие новичков"),
            ManagedCommand("goodbye", "/goodbye", "Прощание с ушедшими"),
            ManagedCommand("autorole", "/autorole", "Авто-роль при входе"),
        ),
    ),
    Category(
        key="levels",
        label="Уровни",
        emoji="📈",
        commands=(
            ManagedCommand("levels", "/levels", "Настройка системы уровней и наград"),
        ),
    ),
    Category(
        key="economy",
        label="Админ-магазин",
        emoji="💰",
        commands=(
            ManagedCommand("admin_profile", "/admin_profile", "Профиль администратора и кнопочный центр магазина"),
            ManagedCommand("shop", "/shop", "Магазин товаров"),
            ManagedCommand("buy", "/buy", "Купить товар из магазина"),
            ManagedCommand("shop_balance", "/shop_balance", "Баланс админской валюты магазина"),
            ManagedCommand("inventory", "/inventory", "Инвентарь купленных товаров"),
            ManagedCommand("safe", "/сейф", "Защищённый сейф от кражи"),
            ManagedCommand("steal", "/кража", "Попытаться украсть админ-валюту"),
            ManagedCommand("perk", "/perk", "Активировать перки из инвентаря"),
            ManagedCommand("currency", "/currency", "Выдача админской валюты магазина"),
        ),
    ),
    Category(
        key="tickets",
        label="Тикеты",
        emoji="🎫",
        commands=(
            ManagedCommand("ticket_panel", "/ticket panel", "Создать панель тикетов"),
            ManagedCommand("ticket_config", "/ticket setup/roles/questions/info", "Настроить структуру, роли, форму и FAQ тикетов"),
            ManagedCommand("ticket_close", "/ticket close", "Закрыть/вести тикет"),
            ManagedCommand("ticket_status", "/ticket status", "Менять рабочий статус тикета"),
            ManagedCommand("ticket_reopen", "/ticket reopen", "Переоткрыть закрытый тикет"),
        ),
    ),
    Category(
        key="social",
        label="Социальное",
        emoji="🤝",
        commands=(
            ManagedCommand("reputation", "/rep, +rep", "Поставить репутацию участнику"),
            ManagedCommand("reputation_profile", "/repprofile", "Профиль репутации участника"),
            ManagedCommand("reputation_top", "/reptop", "Топ участников по репутации"),
        ),
    ),
    Category(
        key="events",
        label="Ивенты",
        emoji="🎪",
        commands=(
            ManagedCommand("event_create", "/create_event", "Создать карточку мероприятия"),
            ManagedCommand("event_finish", "/finish_event", "Завершить активное мероприятие"),
            ManagedCommand("event_stats", "/event_stats", "Получить статистику мероприятия"),
            ManagedCommand("event_hosts", "/event_hosts", "Общая статистика ведущих"),
            ManagedCommand("event_hosts_reset", "/event_hosts_reset", "Обнулить статистику ведущего"),
        ),
    ),
    Category(
        key="giveaways",
        label="Розыгрыши",
        emoji="🎉",
        commands=(
            ManagedCommand("giveaway_start", "/giveaway start", "Запустить розыгрыш"),
            ManagedCommand("giveaway_end", "/giveaway end", "Завершить розыгрыш"),
            ManagedCommand("giveaway_reroll", "/giveaway reroll", "Перевыбрать победителей"),
        ),
    ),
    Category(
        key="analytics",
        label="Dashboard и аналитика",
        emoji="📊",
        commands=(
            ManagedCommand("dashboard", "/dashboard", "Общий dashboard систем бота"),
            ManagedCommand("analytics", "/analytics", "Статистика сервера и тикетов"),
        ),
    ),
    Category(
        key="starboard",
        label="Звёздная доска",
        emoji="⭐",
        commands=(
            ManagedCommand("starboard", "/starboard", "Настройка звёздной доски"),
        ),
    ),
    Category(
        key="applications",
        label="Заявки на должности",
        emoji="📋",
        commands=(
            ManagedCommand("apply", "/apply", "Настройка набора в команду"),
        ),
    ),
    Category(
        key="adminlog",
        label="Логирование администрации",
        emoji="🗒️",
        commands=(
            ManagedCommand("adminlog", "/adminlog", "Форма логирования снятий администрации"),
        ),
    ),
    Category(
        key="antiphishing",
        label="Анти-фишинг",
        emoji="🎣",
        commands=(
            ManagedCommand("antiphish", "/antiphish", "Настройка защиты от фишинга"),
        ),
    ),
)

# Flat lookup tables built once at import time.
COMMANDS_BY_KEY: dict[str, ManagedCommand] = {
    cmd.key: cmd for cat in CATEGORIES for cmd in cat.commands
}
CATEGORY_BY_KEY: dict[str, Category] = {cat.key: cat for cat in CATEGORIES}
CATEGORY_OF_COMMAND: dict[str, Category] = {
    cmd.key: cat for cat in CATEGORIES for cmd in cat.commands
}


def all_command_keys() -> list[str]:
    return list(COMMANDS_BY_KEY.keys())
