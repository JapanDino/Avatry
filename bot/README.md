# SAMURAI — Discord-бот

Модерация, информация о пользователях, выдача ролей, тикеты и защита от
фишинга — с гибкой настройкой доступа к каждой команде через интерактивное
меню `/config`.

> Это отдельный проект внутри папки `SAMURAI bot`. Он никак не связан с проектом
> «виртуальная примерочная» из корня репозитория.

## Модель доступа

- **Владелец сервера** — всегда полный доступ ко всем командам.
- **Супер-админ `JapanDino` (`694999466029088809`)** — всегда полный доступ на
  любом сервере (зашит в `config.py`, `SUPER_ADMIN_ID`).
- Все остальные по умолчанию **не имеют доступа**. Доступ выдаётся точечно —
  по ролям или конкретным пользователям — через `/config`.
- Любую команду можно полностью **выключить** на сервере.

Открыть `/config` могут только владелец сервера и супер-админ.

## Возможности

| Категория | Команды |
|-----------|---------|
| 🪪 Инфо    | `/avatar`, `/banner`, `/userinfo`, `/serverinfo` |
| 🛡️ Модерация | `/ban`, `/unban`, `/tempban`, `/kick`, `/timeout`, `/untimeout`, `/warn`, `/warnings`, `/clearwarns`, `/warnpunish`, `/case`, `/history`, `/note`, `/notes`, `/purge`, `/slowmode`, `/lock`, `/unlock` |
| 🎭 Роли    | `/role add`, `/role remove`, `/selfroles new\|addrole\|removerole` |
| 👋 Вовлечённость | `/welcome`, `/goodbye`, `/autorole` |
| 📈 Уровни  | `/rank`, `/leaderboard`, `/levels toggle\|channel\|reward` |
| 🎫 Тикеты  | `/ticket panel\|config\|close\|add\|remove`, `/ticket theme add\|remove\|list` (+ кнопки и темы) |
| ⭐ Доска   | `/starboard channel\|emoji\|threshold\|status` |
| 🎣 Анти-фишинг | `/antiphish status\|toggle\|action\|logchannel` |
| ⚙️ Настройка | `/config`, `/help` |

**Модерация**: каждое действие — нумерованный кейс (`/case`, `/history`).
Авто-наказания за N предупреждений (`/warnpunish`), истекающие варны,
`/tempban` с авто-разбаном по таймеру, приватные заметки модераторов.

**Уровни**: XP за активность, `/rank` с прогресс-баром, `/leaderboard`,
роли-награды за уровни. **Звёздная доска**: лучшие сообщения по реакции ⭐.
**Само-роли**: панель-меню, где участники сами берут роли.

**Анти-фишинг** сканирует сообщения на известные скам-домены и подделки под
бренды (`dlscord.com`, `discord-nitro.com` и т.п.). Действие настраивается:
удалить / предупредить / тайм-аут / кик / бан. Персонал (право «Управление
сообщениями») и привилегированные пользователи не сканируются.

**Тикеты**: `/ticket panel` публикует панель с кнопкой; нажатие создаёт приватный
канал, видимый автору и роли поддержки. В канале тикета — кнопки **🙋 Взять
тикет** (для поддержки) и **🔒 Закрыть тикет**. При закрытии формируется
**HTML-транскрипт** переписки: он отправляется в канал логов модерации и в ЛС
автору тикета, после чего канал удаляется. Все кнопки переживают перезапуск бота.

## Запуск (локально)

1. Создайте приложение и бота на <https://discord.com/developers/applications>.
2. На вкладке **Bot** включите три **Privileged Gateway Intents**:
   `SERVER MEMBERS`, `MESSAGE CONTENT`, и (для модерации) оставьте остальные по
   умолчанию. Скопируйте **токен**.
3. Пригласите бота на сервер с правами: *Manage Roles, Ban Members, Kick Members,
   Moderate Members, Manage Channels, Manage Messages, Read/Send Messages*
   (проще всего — scope `bot applications.commands`, permissions integer `1099780064310` или просто Administrator на время теста).
4. Настройте окружение:

   ```bash
   cd bot
   python -m venv .venv
   .venv/Scripts/python -m pip install -r requirements.txt   # Windows
   cp .env.example .env        # и впишите DISCORD_TOKEN
   ```

   Для мгновенного появления команд при разработке укажите в `.env`
   `DEV_GUILD_IDS=<id вашего сервера>` (глобальный синк может занять до часа).

5. Запустите:

   ```bash
   .venv/Scripts/python bot.py
   ```

## Структура

```
bot/
  bot.py              # точка входа, интенты, синк команд, обработка ошибок
  config.py           # .env, константы (в т.ч. SUPER_ADMIN_ID)
  core/
    database.py       # SQLite (aiosqlite): права, варны, тикеты, конфиг
    permissions.py    # проверка доступа + декоратор @requires
    registry.py       # реестр всех управляемых команд (категории)
    modlog.py         # логи модерации, парсер длительности, иерархия ролей
    transcript.py     # генерация HTML-транскрипта тикета
    embeds.py         # единый стиль embed'ов
  cogs/
    config_cog.py     # /config
    userinfo.py  moderation.py  roles.py  tickets.py  antiphishing.py
  views/
    config_views.py   # интерактивное меню /config
```

## Добавить новую управляемую команду

1. Зарегистрируйте её в `core/registry.py` (ключ + категория).
2. Повесьте на колбэк декоратор `@requires("ваш_ключ")`.

Меню `/config` подхватит команду автоматически.
