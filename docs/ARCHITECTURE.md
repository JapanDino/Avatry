# Архитектура

> Целевая картина. Что уже решено — см. DECISIONS.md; что и когда строим — см. PLAN.md.

## Общая схема

```
Сайт магазина
  └─ <script src=".../loader.js" data-shop-id> → кнопка «Примерить» → iframe-виджет
        │ postMessage (открыть товар / событие аналитики / «в корзину»)
        ▼
web/ (React + Three.js, исполняется у клиента)
  ├─ Аватар: GLB тела + морфы; мерки → веса морфов (lookup из калибровки)
  ├─ Одежда: GLB вещи (слой), морфы синхронны с телом; переключение размеров
  ├─ Посадка: ease-расчёт на клиенте (мгновенный отклик) + heatmap-оверлей
  └─ Состояние: localStorage (гость) / API (этап 5+)
        │ REST/JSON
        ▼
server/ (FastAPI)                      хранилище (MinIO/R2 + CDN)
  ├─ /avatars  CRUD + удаление данных    ├─ body GLB (м/ж)
  ├─ /garments каталог метаданных        ├─ garment GLB по размерам
  └─ /fit      источник правды посадки   └─ garment.json
        ▼
PostgreSQL (аватары, товары, размерные таблицы, события)

pipeline/ (офлайн, Python + headless Blender)
  ├─ gen_body.py      тело + shape keys
  ├─ measure.py       виртуальная лента, калибровка «морфы ↔ мерки»
  ├─ fit_garment.py   привязка одежды к морфам тела
  ├─ add_product.py   product.yaml + 2 фото → GLB по размерам + garment.json
  └─ export_glb.py    экспорт + Draco/KTX2 + валидатор бюджетов
```

## Ключевые потоки данных

**Создание аватара (всё на клиенте):**
пол/рост/вес/обхваты → валидация → регрессия недостающих мерок → lookup «мерки → веса морфов» (JSON из калибровки 3.3) → morph target influences на меше тела.

**Примерка:**
выбор товара → загрузка GLB размера + garment.json → веса морфов тела применяются к одежде → anti-clipping маска → рендер.

**Посадка:**
для каждой зоны (грудь, талия, бёдра, рукав, длина): `ease = мерка_изделия − мерка_тела`; порог с учётом эластичности ткани → класс {тесно | по фигуре | свободно} → цвет зоны + текст рекомендации. Рекомендуемый размер = минимальный размер без зон «тесно» (правила уточняются в спеке этапа 4).

## Модели данных (ядро)

```
Avatar:   id, gender, height_cm, weight_kg, measurements{chest,waist,hips,...},
          morph_weights{...}, appearance{skin_tone, hair_preset}, created_at
Garment:  id, product_ref, type (tshirt|hoodie|jeans|...), elasticity (0..1),
          sizes[{label, measurements{...}, glb_url}], thumbnail_url, status
FitResult: per_zone{zone → {ease_cm, class}}, recommended_size, explanation
```

## Словарь морфов (единый для тела и одежды)

Тело: `height, weight, chest, waist, hips` (MVP) + резерв `shoulders, belly, inseam, neck, sleeve_ref`.
Одежда (крой): `garment_length, garment_width, sleeve_length, fit_ease`.
Канонический список — `pipeline/morphs.py`; имена в Blender shape keys = имена morph targets в GLB = ключи в коде. Никаких синонимов.

## Бюджеты и ограничения

| Что | Лимит |
|---|---|
| GLB тела | ≤ 4 МБ (Draco) |
| GLB вещи (один размер) | ≤ 3 МБ |
| Текстуры | ≤ 2048px, KTX2 |
| FPS | 60 десктоп / 30 средний смартфон |
| Время до первого кадра аватара | ≤ 2 с на 4G |
| Браузеры | последние 2 версии Chrome/Safari/Firefox/Edge; iOS Safari обязателен |

## Принципы

- Клиент способен работать без сервера (демо-режим) — сервер добавляет персистентность и источник правды, но не является зависимостью рендера.
- Пайплайн детерминирован: один `product.yaml` → одни и те же GLB; всё перегенерируемо с нуля одной командой.
- Приватность: мерки храним только с согласия, удаление по кнопке, фото пользователя не принимаем вовсе.
