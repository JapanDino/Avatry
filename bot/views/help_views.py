"""Interactive /help menu with step-by-step docs and command navigation."""
from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

import discord

from core import embeds
from core.permissions import is_admin_access, member_can_use
from core.registry import CATEGORIES, CATEGORY_BY_KEY

if TYPE_CHECKING:
    from bot import SamuraiBot

TIMEOUT = 180.0


class GuidePage(TypedDict):
    label: str
    emoji: str
    title: str
    description: str
    fields: tuple[tuple[str, str], ...]


EXTRA_LABEL = "Общедоступные команды"
EXTRA_EMOJI = "✨"
EXTRA_COMMANDS = (
    ("/help", "Открыть эту справку."),
    ("/botinfo", "Посмотреть профиль бота и информацию о разработчике."),
    ("/rank", "Показать уровень, опыт и rank-карточку."),
    ("/leaderboard", "Показать топ участников по опыту или балансу."),
    ("/rankcard colors", "Настроить цвета своей rank-карточки."),
    ("/rankcard preview", "Посмотреть, как выглядит ваша rank-карточка."),
    ("/rankcard reset", "Сбросить цвета rank-карточки к стандартным."),
    ("/config", "Точная настройка доступа к командам. Для администрации."),
    ("/settings", "Настройка сфер команд и ролей доступа. Для администрации."),
)

GUIDE_PAGES: dict[str, GuidePage] = {
    "start": {
        "label": "Быстрый старт",
        "emoji": "🚀",
        "title": "🚀 Быстрый старт",
        "description": "Короткий маршрут, чтобы не потеряться в большом количестве команд.",
        "fields": (
            ("1. Откройте нужный раздел", "В первом меню выберите инструкцию, во втором — сферу команд."),
            ("2. Смотрите только доступное", "По умолчанию `/help` скрывает команды, к которым у вас нет прав."),
            ("3. Раскройте полный список", "Кнопка `Показать недоступные` покажет все команды с отметками `🔓` и `🔒`."),
            ("4. Запускайте через slash menu", "Основной формат команд — `/команда`. Некоторые hybrid-команды также работают через `+`, `k.` и `K.`."),
            ("5. Настройка прав", "Администраторы используют `/settings` для сфер и `/config` для отдельных команд."),
        ),
    },
    "access": {
        "label": "Права и сферы",
        "emoji": "🔐",
        "title": "🔐 Права и сферы",
        "description": "Как бот решает, кому какая команда доступна.",
        "fields": (
            ("1. Сфера команд", "Каждая команда относится к сфере: модерация, экономика, тикеты, ивенты, розыгрыши и так далее."),
            ("2. Включить или выключить сферу", "`/settings` управляет сферами и ролями, которым доступна вся сфера."),
            ("3. Выдать точный доступ", "`/config` выдает доступ конкретной роли или человеку к конкретной команде."),
            ("4. Полный доступ", "Владелец сервера, super admin и участники с `Administrator` видят и используют все."),
            ("5. Что значат значки", "`🔓` — доступно. `🔒` — нет прав или команда выключена на сервере."),
        ),
    },
    "server_setup": {
        "label": "Настроить сервер",
        "emoji": "⚙️",
        "title": "⚙️ Пошагово: базовая настройка сервера",
        "description": "Минимальный порядок настройки нового сервера под бота.",
        "fields": (
            ("1. Проверьте права бота", "Роль бота должна быть выше ролей, которые он выдает, снимает, продает или назначает за уровни."),
            ("2. Включите нужные сферы", "Откройте `/settings` и включите модули: модерация, тикеты, экономика, ивенты, розыгрыши и другие."),
            ("3. Выдайте доступ ролям", "В `/settings` назначьте роли на целые сферы, а в `/config` — точечные права на отдельные команды."),
            ("4. Настройте публичные системы", "Опубликуйте панели тикетов, заявок, selfroles, starboard и приветствия в нужные каналы."),
            ("5. Проверьте глазами участника", "Попросите пользователя без админки открыть `/help`: он должен видеть только доступные ему команды."),
        ),
    },
    "moderation": {
        "label": "Настроить модерацию",
        "emoji": "🛡️",
        "title": "🛡️ Пошагово: модерация",
        "description": "Как подготовить модераторские команды и рабочий процесс наказаний.",
        "fields": (
            ("1. Выдайте доступ", "Через `/settings` назначьте роль модераторов на сферу `Модерация` или выдайте отдельные команды через `/config`."),
            ("2. Проверьте иерархию ролей", "Роль бота должна быть выше ролей модераторов и участников, которых он будет банить, кикать или мутить."),
            ("3. Используйте базовые наказания", "`/warn`, `/timeout`, `/kick`, `/ban`, `/tempban`, `/unban` покрывают основные действия."),
            ("4. Ведите историю", "`/case`, `/history`, `/warnings`, `/clearwarns`, `/note`, `/notes` помогают разбирать ситуации после наказания."),
            ("5. Настройте авто-наказания", "`/warnpunish set` задает действие за N предупреждений, `/warnpunish list` показывает текущую лестницу."),
            ("6. Управляйте каналом", "`/purge`, `/slowmode`, `/lock`, `/unlock` помогают быстро навести порядок в конкретном канале."),
        ),
    },
    "roles": {
        "label": "Роли и selfroles",
        "emoji": "🎭",
        "title": "🎭 Пошагово: роли и selfroles",
        "description": "Как выдавать роли вручную и сделать панель само-ролей.",
        "fields": (
            ("1. Проверьте роль бота", "Бот может управлять только ролями ниже своей самой высокой роли."),
            ("2. Ручная выдача", "`/role add` выдает роль участнику, `/role remove` снимает ее."),
            ("3. Создайте selfroles-панель", "`/selfroles new` публикует панель в текущем канале."),
            ("4. Добавьте роли на панель", "`/selfroles addrole` добавляет роль в последнюю панель канала."),
            ("5. Уберите роль с панели", "`/selfroles removerole` удаляет роль из последней панели канала."),
        ),
    },
    "engagement": {
        "label": "Приветствия",
        "emoji": "👋",
        "title": "👋 Пошагово: приветствия и авто-роль",
        "description": "Как настроить сообщения при входе/выходе и автоматическую роль новичка.",
        "fields": (
            ("1. Приветствие", "`/welcome set channel message` включает приветствие. В тексте можно использовать `{user}`, `{name}`, `{server}`, `{count}`."),
            ("2. Проверка", "`/welcome test` отправляет пример приветствия, чтобы не ждать нового участника."),
            ("3. Отключение", "`/welcome off` выключает приветствия."),
            ("4. Прощание", "`/goodbye set channel message` включает сообщение при выходе. Доступны `{name}`, `{server}`, `{count}`."),
            ("5. Авто-роль", "`/autorole role` задает роль при входе, `/autorole` без роли выключает авто-роль."),
        ),
    },
    "levels": {
        "label": "Уровни",
        "emoji": "📈",
        "title": "📈 Пошагово: уровни",
        "description": "Как включить уровни, награды и канал уведомлений.",
        "fields": (
            ("1. Включите систему", "`/levels toggle enabled:true` включает начисление опыта за активность."),
            ("2. Канал уведомлений", "`/levels channel` задает канал для сообщений о повышении уровня. Без канала бот пишет там, где участник получил уровень."),
            ("3. Добавьте награды", "`/levels reward add level role` выдает роль за достижение уровня."),
            ("4. Проверьте награды", "`/levels reward list` показывает текущие награды, `/levels reward remove` удаляет награду."),
            ("5. Команды участников", "`/rank`, `/leaderboard`, `/rankcard colors/preview/reset` доступны пользователям для профиля и карточки."),
        ),
    },
    "tickets": {
        "label": "Как настроить тикеты",
        "emoji": "🎫",
        "title": "🎫 Пошагово: тикеты",
        "description": "Сценарий настройки и работы с обращениями.",
        "fields": (
            ("1. Создайте структуру", "`/ticket setup` автоматически создает категорию и служебные каналы тикетов."),
            ("2. Назначьте персонал", "`/ticket roles claim_role reviewer_role` задает роли, которые видят, берут и проверяют тикеты."),
            ("3. Настройте форму", "`/ticket questions add/list/clear` управляет вопросами формы. Лимит Discord — до 5 вопросов."),
            ("4. Добавьте FAQ", "`/ticket info add/clear` добавляет подсказки на панель тикетов."),
            ("5. Опубликуйте панель", "`/ticket panel` отправляет публичное сообщение с кнопкой создания тикета."),
            ("6. Ведите тикет", "`/ticket status` меняет статус, `/ticket close` закрывает, `/ticket reopen` возвращает закрытый тикет."),
            ("7. Соберите оценку", "После закрытия бот отправляет пользователю форму оценки в ЛС, если личные сообщения открыты."),
        ),
    },
    "events": {
        "label": "Как провести ивент",
        "emoji": "🎪",
        "title": "🎪 Пошагово: ивент",
        "description": "Сценарий от создания афиши до статистики ведущего.",
        "fields": (
            ("1. Создайте карточку", "В канале `create-events` ведущий с ролью Eventer использует `/create_event`."),
            ("2. Проверьте превью", "Бот отправит красивую карточку на утверждение в тот же канал."),
            ("3. Опубликуйте афишу", "Автор нажимает `Опубликовать`, после чего карточка уходит в канал афиши с пингом Event."),
            ("4. Завершите мероприятие", "После окончания используйте `/finish_event`. Если забыть, бот напомнит примерно через 1.5 часа."),
            ("5. Получите фидбек", "Участникам голосового канала придет форма оценки мероприятия и ведущего в ЛС."),
            ("6. Посмотрите статистику", "`/event_stats` покажет период, посещения и оценки. `/event_hosts` отправит публичный топ с выбором периода в меню."),
            ("7. Обнулите ведущего", "Роль полного доступа может использовать `/event_hosts_reset` для нового отсчёта статистики ведущего."),
        ),
    },
    "economy": {
        "label": "Админ-валюта и магазин",
        "emoji": "💰",
        "title": "💰 Пошагово: админская экономика",
        "description": "Экономика бота теперь используется только для админского магазина и ручной выдачи валюты.",
        "fields": (
            ("1. Откройте профиль", "`/admin_profile` — главный экран: магазин, инвентарь, лидерборд и фон профиля."),
            ("2. Купите товар", "`/shop` ведет через категории и кнопку покупки; `/buy item_id` покупает напрямую."),
            ("3. Используйте инвентарь", "`/inventory` открывает предметы; `/perk` активирует страховку, буст кражи и карту выхода."),
            ("4. Храните валюту", "`/сейф положить/снять` переносит админ-валюту в защищенный сейф."),
            ("5. Играйте через кражи", "`/кража @user` пытается украсть из кошелька цели; сейф защищён, инструменты операций повышают шанс."),
            ("6. Выдайте валюту", "`/currency give/take/set` управляет только админской валютой магазина."),
            ("7. Настройте систему", "`/economy grantsettings`, `manager_*`, `shopsettings`, `jailsettings`, `stealsettings`, `catalog_import`, `requests`."),
        ),
    },
    "social": {
        "label": "Репутация",
        "emoji": "🤝",
        "title": "🤝 Пошагово: репутация",
        "description": "Как пользователи могут благодарить друг друга и смотреть социальный рейтинг.",
        "fields": (
            ("1. Поставить репутацию", "Используйте `/rep user reason` или сообщение `+rep @user` с коротким комментарием."),
            ("2. Посмотреть профиль", "`/repprofile user` показывает репутацию конкретного участника."),
            ("3. Посмотреть топ", "`/reptop` показывает лидеров по репутации на сервере."),
            ("4. Ограничения", "Нельзя ставить репутацию самому себе; антиспам и кулдауны обрабатываются ботом."),
        ),
    },
    "giveaways": {
        "label": "Как сделать розыгрыш",
        "emoji": "🎉",
        "title": "🎉 Пошагово: розыгрыш",
        "description": "Создание giveaway с кнопкой участия и автоматическим выбором победителей.",
        "fields": (
            ("1. Запустите розыгрыш", "`/giveaway start duration winners prize channel` создает сообщение с кнопкой."),
            ("2. Укажите длительность", "Форматы: `30s`, `10m`, `2h`, `1d`, `1w`. Пример: `duration: 2h`."),
            ("3. Участники нажимают кнопку", "Повторное нажатие не создает дубль участия."),
            ("4. Дождитесь конца", "Бот сам завершит розыгрыш и объявит победителей."),
            ("5. Завершите вручную", "`/giveaway end message_id` завершает досрочно."),
            ("6. Перевыберите победителей", "`/giveaway reroll message_id` делает новый выбор по тем же участникам."),
        ),
    },
    "rankcard": {
        "label": "Rank-карточка",
        "emoji": "🖼️",
        "title": "🖼️ Пошагово: rank-карточка",
        "description": "Как настроить внешний вид своей карточки уровня.",
        "fields": (
            ("1. Посмотрите текущий вид", "`/rank` показывает вашу карточку, `/rankcard preview` делает быстрый предпросмотр."),
            ("2. Выберите цвета", "Цвета задаются в HEX: `#7C5CFF`, `7C5CFF`, `#111827`."),
            ("3. Настройте карточку", "`/rankcard colors accent background` меняет акцент и фон."),
            ("4. Сбросьте оформление", "`/rankcard reset` возвращает стандартные цвета."),
        ),
    },
    "analytics": {
        "label": "Аналитика",
        "emoji": "📊",
        "title": "📊 Пошагово: аналитика и dashboard",
        "description": "Как смотреть состояние сервера и систем бота.",
        "fields": (
            ("1. Общая сводка", "`/dashboard` показывает состояние модулей, команд, экономики, тикетов и активности."),
            ("2. Активность сервера", "`/analytics server days` показывает сообщения, участников, команды и активность за период 1-30 дней."),
            ("3. Тикеты", "`/analytics tickets` показывает статистику обращений и работы поддержки."),
            ("4. Ивенты", "`/event_stats` и `/event_hosts` находятся в системе ивентов и показывают статистику мероприятий."),
        ),
    },
    "starboard": {
        "label": "Звездная доска",
        "emoji": "⭐",
        "title": "⭐ Пошагово: звездная доска",
        "description": "Как настроить канал лучших сообщений по реакциям.",
        "fields": (
            ("1. Выберите канал", "`/starboard channel` задает канал, куда бот будет отправлять популярные сообщения."),
            ("2. Выберите реакцию", "`/starboard emoji` меняет реакцию, которая считается голосом. По умолчанию используется ⭐."),
            ("3. Задайте порог", "`/starboard threshold count` задает, сколько реакций нужно для публикации."),
            ("4. Проверьте настройки", "`/starboard status` показывает текущую конфигурацию."),
            ("5. Отключение", "`/starboard channel` без канала выключает звездную доску."),
        ),
    },
    "applications": {
        "label": "Заявки в команду",
        "emoji": "📋",
        "title": "📋 Пошагово: заявки в команду",
        "description": "Как собрать анкеты на должности и отправлять их рекрутерам.",
        "fields": (
            ("1. Создайте канал проверки", "`/apply setup` создает закрытый канал для рассмотрения заявок."),
            ("2. Назначьте рекрутеров", "`/apply roles role` дает роли доступ к рассмотрению заявок."),
            ("3. Добавьте должность", "`/apply position add key label role description emoji` создает вариант в панели."),
            ("4. Настройте анкету", "`/apply question add position_key label` добавляет вопросы. До 5 вопросов на должность."),
            ("5. Проверьте список", "`/apply position list` показывает должности и количество вопросов."),
            ("6. Опубликуйте панель", "`/apply panel channel title text image` публикует набор в нужный канал."),
        ),
    },
    "antiphish": {
        "label": "Анти-фишинг",
        "emoji": "🎣",
        "title": "🎣 Пошагово: анти-фишинг",
        "description": "Как включить защиту от фишинговых ссылок и выбрать реакцию бота.",
        "fields": (
            ("1. Включите защиту", "`/antiphish toggle enabled:true` включает проверку ссылок."),
            ("2. Выберите действие", "`/antiphish action` задает реакцию: удалить, предупредить, timeout, kick или ban."),
            ("3. Настройте логи", "`/antiphish logchannel channel` задает канал логов; без канала сбрасывает логирование."),
            ("4. Проверьте статус", "`/antiphish status` показывает текущие настройки."),
            ("5. Проверьте права", "Для timeout/kick/ban роль бота должна быть выше роли нарушителя."),
        ),
    },
    "adminlog": {
        "label": "Логи администрации",
        "emoji": "🗒️",
        "title": "🗒️ Пошагово: логи администрации",
        "description": "Как вести форму снятий или кадровых записей администрации.",
        "fields": (
            ("1. Задайте канал", "`/adminlog channel` выбирает канал, куда будут отправляться записи."),
            ("2. Опубликуйте панель", "`/adminlog panel` отправляет кнопку/форму для заполнения записи."),
            ("3. Заполняйте форму", "Пользователь нажимает кнопку и заполняет поля, после чего запись уходит в настроенный канал."),
        ),
    },
    "userinfo": {
        "label": "Информация",
        "emoji": "🪪",
        "title": "🪪 Пошагово: информация",
        "description": "Быстрые команды для профилей пользователей и сервера.",
        "fields": (
            ("1. Аватар", "`/avatar user` показывает аватар пользователя. Если не указать user, покажет ваш."),
            ("2. Баннер", "`/banner user` показывает баннер профиля, если он доступен."),
            ("3. Профиль пользователя", "`/userinfo user` показывает дату входа, роли и базовую информацию."),
            ("4. Сервер", "`/serverinfo` показывает информацию о сервере."),
        ),
    },
}

COMMAND_DETAILS = {
    "/avatar": "Параметр `user` необязательный: без него бот покажет ваш аватар.",
    "/banner": "Если у пользователя нет баннера или он недоступен API Discord, бот сообщит об этом.",
    "/userinfo": "Параметр `user` необязательный: без него бот покажет вашу карточку.",
    "/serverinfo": "Показывает базовую сводку по серверу.",
    "/ban": "Можно указать причину и сколько дней сообщений удалить.",
    "/unban": "Нужен ID пользователя, даже если его уже нет на сервере.",
    "/kick": "Кикает участника с сервера; роль бота должна быть выше роли цели.",
    "/timeout": "Формат времени: `30s`, `10m`, `2h`, `1d`; лимит Discord — до 28 дней.",
    "/untimeout": "Снимает активный timeout с участника.",
    "/tempban": "Формат времени: `1h`, `1d`, `7d`; по окончании бот попробует разбанить автоматически.",
    "/warn": "Добавляет предупреждение в историю участника.",
    "/warnings": "Показывает активные предупреждения участника.",
    "/clearwarns": "Снимает предупреждения с участника.",
    "/purge": "Удаляет 1-100 сообщений. Можно указать пользователя, чтобы чистить только его сообщения.",
    "/warnpunish": "Подкоманды: `set`, `remove`, `list`, `expiry`.",
    "/case": "Показывает модераторский кейс по номеру.",
    "/history": "Показывает историю наказаний участника.",
    "/note": "Добавляет приватную заметку модерации по участнику.",
    "/notes": "Показывает приватные заметки по участнику.",
    "/slowmode": "`seconds: 0` выключает медленный режим.",
    "/lock": "Закрывает текущий канал для `@everyone`.",
    "/unlock": "Открывает текущий канал обратно.",
    "/role add": "Роль бота должна быть выше выдаваемой роли.",
    "/role remove": "Роль бота должна быть выше снимаемой роли.",
    "/selfroles": "Подкоманды: `new`, `addrole`, `removerole`.",
    "/welcome": "Подкоманды: `set`, `test`, `off`. Переменные: `{user}`, `{name}`, `{server}`, `{count}`.",
    "/goodbye": "Подкоманды: `set`, `off`. Переменные: `{name}`, `{server}`, `{count}`.",
    "/autorole": "Укажите роль для включения; вызов без роли выключает авто-роль.",
    "/economy": "Админ-группа магазина: каталог, заявки, менеджеры админской валюты и настройки выдачи.",
    "/currency": "Менеджерская группа админской валюты магазина: `give`, `take`, `set`. Не влияет на обычный кошелек экономики.",
    "/admin_profile": "Главный профиль админской экономики: баланс, сейф, кражи, тюрьма, кнопки магазина/инвентаря/фона.",
    "/shop": "Показывает товары магазина. Можно указать `category` для фильтра.",
    "/buy": "Нужен `item_id` из `/shop`; поддерживает роли, временные роли, инвентарь и ручную выдачу.",
    "/shop_balance": "Показывает отдельный баланс админской валюты магазина.",
    "/inventory": "Показывает купленные инвентарные товары.",
    "/сейф": "Подкоманды `положить` и `снять`; сейф защищает валюту от `/кража`.",
    "/кража": "Рискованная попытка украсть админ-валюту из кошелька цели.",
    "/perk": "Активирует предметы из инвентаря: страховку, буст кражи, карту выхода.",
    "/levels": "Настройки уровней: `toggle`, `channel`, `reward add/remove/list`.",
    "/rankcard colors": "HEX можно писать с `#` или без: `#7C5CFF`, `7C5CFF`.",
    "/leaderboard": "Публичный топ; критерий `Опыт` или `Баланс` выбирается в выпадающем меню под сообщением.",
    "/ticket panel": "Публикует публичную панель создания тикетов.",
    "/ticket config": "В этой версии настройка разбита на `/ticket setup`, `/ticket roles`, `/ticket questions`, `/ticket info`.",
    "/ticket close": "Закрывает текущий тикет и запускает сбор оценки в ЛС.",
    "/ticket status": "Меняет рабочий статус тикета.",
    "/ticket reopen": "Переоткрывает закрытый тикет.",
    "/rep, +rep": "Можно использовать slash-команду или написать `+rep @user`.",
    "/repprofile": "Показывает профиль репутации участника.",
    "/reptop": "Показывает топ участников по репутации.",
    "/giveaway start": "Пример: `duration: 2h`, `winners: 1`, `prize: Nitro`.",
    "/giveaway end": "Нужен ID сообщения розыгрыша. Его можно скопировать через Developer Mode в Discord.",
    "/giveaway reroll": "Работает по завершенному розыгрышу и отправляет новых победителей в канал.",
    "/create_event": "Используется в `create-events`; нужна роль Eventer или роль полного управления ивентами.",
    "/finish_event": "Автор завершает свой ивент; роль полного управления может завершать чужие.",
    "/event_stats": "Показывает период, длительность, посещения и оценки ивента.",
    "/event_hosts": "Публичная сводка по ведущим. Период выбирается в меню: всё время, текущая неделя, прошлая неделя.",
    "/event_hosts_reset": "Доступно роли полного управления ивентами; обнуляет ведущего в общей сводке без удаления старых ивентов.",
    "/dashboard": "Общая сводка состояния систем бота.",
    "/analytics": "Подкоманды: `server` и `tickets`.",
    "/starboard": "Подкоманды: `channel`, `emoji`, `threshold`, `status`.",
    "/apply": "Подкоманды: `setup`, `roles`, `panel`, `position add/remove/list`, `question add/clear`.",
    "/adminlog": "Подкоманды: `channel`, `panel`.",
    "/antiphish": "Подкоманды: `status`, `toggle`, `action`, `logchannel`.",
    "/config": "Открывает меню точечных прав: команда -> роли/пользователи -> включено/выключено.",
    "/settings": "Открывает меню сфер: включение модулей и роли доступа к целым категориям.",
}


def _access_icon(allowed: bool) -> str:
    return "🔓" if allowed else "🔒"


def _with_detail(label: str, description: str) -> str:
    detail = COMMAND_DETAILS.get(label)
    if detail is None:
        return description
    return f"{description}\n{detail}"


class HelpView(discord.ui.View):
    def __init__(self, bot: "SamuraiBot", member: discord.Member):
        super().__init__(timeout=TIMEOUT)
        self.bot = bot
        self.member = member
        self.show_locked = False
        self.message: discord.Message | None = None
        self.add_item(GuideSelect())
        self.add_item(CommandCategorySelect())
        self.add_item(ToggleLockedButton())
        self.add_item(HomeButton())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.member.id:
            await interaction.response.send_message(
                embed=embeds.error("Это меню открыто другим пользователем. Введите свой `/help`."),
                ephemeral=True,
            )
            return False
        return True

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True  # type: ignore[attr-defined]
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    def _brand(self, embed: discord.Embed) -> discord.Embed:
        user = self.bot.user
        if user is not None:
            embed.set_author(name=user.display_name, icon_url=user.display_avatar.url)
            embed.set_thumbnail(url=user.display_avatar.url)
        return embed

    async def home_embed(self) -> discord.Embed:
        total = 0
        visible_total = 0
        lines: list[str] = []
        for cat in CATEGORIES:
            total += len(cat.commands)
            visible = 0
            for cmd in cat.commands:
                if await member_can_use(self.bot, self.member, cmd.key):
                    visible += 1
            visible_total += visible
            lines.append(f"{cat.emoji} **{cat.label}**: {visible}/{len(cat.commands)}")

        embed = embeds.base(
            title="📖 Помощь K2-SO",
            description=(
                "Это справка с двумя типами навигации:\n"
                "1. **Пошаговые инструкции** — как настроить или провести конкретный сценарий.\n"
                "2. **Команды по сферам** — полный список команд с учетом ваших прав.\n\n"
                "По умолчанию скрыты команды, которые вам недоступны."
            ),
        )
        embed.add_field(
            name="🧭 Навигация",
            value=(
                "`Выберите инструкцию` — сценарии: тикеты, ивенты, экономика, розыгрыши, rank-карточка.\n"
                "`Выберите сферу команд` — список команд конкретной сферы."
            ),
            inline=False,
        )
        embed.add_field(
            name="🔐 Значки доступа",
            value="`🔓` можно использовать. `🔒` команда есть, но нет прав или она выключена.",
            inline=False,
        )
        embed.add_field(
            name="📊 Доступные команды",
            value="\n".join(lines),
            inline=False,
        )
        mode = "показаны все команды" if self.show_locked else "недоступные скрыты"
        embed.set_footer(
            text=f"Доступно: {visible_total + len(EXTRA_COMMANDS)}/{total + len(EXTRA_COMMANDS)} • {mode}"
        )
        return self._brand(embed)

    async def guide_embed(self, guide_key: str) -> discord.Embed:
        page = GUIDE_PAGES[guide_key]
        embed = embeds.base(title=page["title"], description=page["description"])
        for name, value in page["fields"]:
            embed.add_field(name=name, value=value, inline=False)
        embed.set_footer(text="Для списка команд откройте второе меню: Выберите сферу команд.")
        return self._brand(embed)

    async def category_embed(self, category_key: str) -> discord.Embed:
        guild = self.member.guild
        if category_key == "__extra__":
            embed = embeds.base(
                title=f"{EXTRA_EMOJI} {EXTRA_LABEL}",
                description="Команды, которые не привязаны к отдельной сфере доступа.",
            )
            for label, desc in EXTRA_COMMANDS:
                allowed = is_admin_access(self.member, guild) if label in ("/config", "/settings") else True
                if not allowed and not self.show_locked:
                    continue
                embed.add_field(
                    name=f"{_access_icon(allowed)} `{label}`",
                    value=_with_detail(label, desc),
                    inline=False,
                )
            return self._brand(embed)

        category = CATEGORY_BY_KEY[category_key]
        embed = embeds.base(
            title=f"{category.emoji} Команды: {category.label}",
            description=(
                "Ниже команды этой сферы. Если нужной команды нет, нажмите "
                "`Показать недоступные`, чтобы увидеть полный список."
            ),
        )
        shown = 0
        for cmd in category.commands:
            allowed = await member_can_use(self.bot, self.member, cmd.key)
            if not allowed and not self.show_locked:
                continue
            shown += 1
            embed.add_field(
                name=f"{_access_icon(allowed)} `{cmd.label}`",
                value=_with_detail(cmd.label, cmd.description),
                inline=False,
            )
        if shown == 0:
            embed.description = (
                "В этой сфере у вас пока нет доступных команд. "
                "Нажмите `Показать недоступные`, чтобы увидеть, что можно запросить у администрации."
            )
        return self._brand(embed)


class GuideSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label=page["label"],
                value=key,
                emoji=page["emoji"],
                description="Пошаговая инструкция",
            )
            for key, page in GUIDE_PAGES.items()
        ]
        super().__init__(
            placeholder="📘 Выберите инструкцию...",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view: HelpView = self.view  # type: ignore[assignment]
        embed = await view.guide_embed(self.values[0])
        await interaction.response.edit_message(embed=embed, view=view)


class CommandCategorySelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label=f"Команды: {cat.label}",
                value=cat.key,
                emoji=cat.emoji,
                description=f"{len(cat.commands)} команд в сфере",
            )
            for cat in CATEGORIES
        ]
        options.append(
            discord.SelectOption(
                label=EXTRA_LABEL,
                value="__extra__",
                emoji=EXTRA_EMOJI,
                description="Общие команды и личные настройки",
            )
        )
        super().__init__(
            placeholder="📂 Выберите сферу команд...",
            min_values=1,
            max_values=1,
            options=options,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view: HelpView = self.view  # type: ignore[assignment]
        embed = await view.category_embed(self.values[0])
        await interaction.response.edit_message(embed=embed, view=view)


class ToggleLockedButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Показать недоступные",
            emoji="🔒",
            style=discord.ButtonStyle.secondary,
            row=2,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view: HelpView = self.view  # type: ignore[assignment]
        view.show_locked = not view.show_locked
        self.label = "Скрыть недоступные" if view.show_locked else "Показать недоступные"
        self.emoji = "🔓" if view.show_locked else "🔒"
        embed = await view.home_embed()
        await interaction.response.edit_message(embed=embed, view=view)


class HomeButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="На главную",
            emoji="🏠",
            style=discord.ButtonStyle.secondary,
            row=2,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view: HelpView = self.view  # type: ignore[assignment]
        embed = await view.home_embed()
        await interaction.response.edit_message(embed=embed, view=view)
