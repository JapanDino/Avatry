# Текущее состояние проекта

> Обновляется в конце каждой рабочей сессии (3–6 строк). Это первое, что читает ИИ в новой сессии.

**Этап:** 1 (параметрическое тело) + досрочный заход в этап 3 (пол + мерки). **Стоим на Gate 1.**

**Два пола + ввод по меркам (сделано по просьбе пользователя):** мужское и женское тело (`gen_body.py --gender`), своя CC0-одежда на пол. UI — форма мерок (пол, рост, вес, обхваты груди/талии/бёдер), морфы считаются из мерок по `web/public/calibration.json` (`pipeline/calibrate.py`). Ползунки морфов убраны в свёрнутую «Тонкую настройку». GLB по полу: `body_{f,m}.glb`, `top_{f,m}.glb`, `shorts_{f,m}.glb`. Скриншоты `artifacts/review/stage3-*.png`.

**Сделано в последней сессии (2026-06-11):**
- Этап 0 целиком + git-регламент (`docs/GIT-WORKFLOW.md`), залито в `github.com/JapanDino/Avatry` (приватный, ветка main, 3 коммита, чистая история без чужого booml).
- Этап 1: Blender 5.1.2 + MPFB 2.0.15 (через extensions.blender.org, совместим). `pipeline/gen_body.py` печёт нейтральное тело + 5 морфов (height/weight/chest/waist/hips) через MPFB макросы и measure-таргеты, срезает helper-геометрию (чистый body-меш 13380 верт.). `pipeline/export_glb.py` → GLB 0.86 МБ с 5 морф-таргетами. web: `AvatarViewer` грузит GLB, `Sliders` рулят морфами по имени в реальном времени. Проверено в браузере: нейтраль и крайние значения корректны.

**Расширение по просьбе пользователя (в рамках этапа 1):** морфов теперь 10 (добавлены muscle, shoulders, neck, arm, thigh). Модель по умолчанию одета; переключатель «Одежда».
- **Футболка — физика ткани** (`pipeline/sim_morph_garment.py`): старт = CC0 MHCLO-ассет (`assets/clothes/elvs_crude_t-shirt_male`) + припуск → одна cloth-симуляция = свободная драпировка (Basis); морфы = линейный перенос по ближайшей вершине. Свободная посадка + складки + стабильно на комбинациях морфов. **Целевой метод для каталога (этап 2).**
- Шорты — геометрическая драпировка (`pipeline/gen_clothing.py`, `_drape`), припуск уменьшен (не мешковатые). Позже можно тоже прогнать через sim при наличии MHCLO-низа.
- Устарели: `sim_garment.py` (процедурный старт, рваный ворот), `gen_mhclo_garment.py` (морфы без симуляции — обтягивает). Оставлены в истории, актуален `sim_morph_garment.py`.
- **Конвейер:** gen_body → gen_clothing (shorts) → sim_morph_garment (top) → export_glb по объектам → web/public/models (body/top/shorts.glb). CC0-пак в `artifacts/vendor/` (gitignore), используемый ассет — в `assets/clothes/` (LFS).

**Следующий шаг:** дождаться оценки пользователя на Gate 1 (скриншоты `artifacts/review/stage1-dressed-*.png`). Если ок — закрыть Gate 1 и перейти к этапу 2 (одежда каталога на морфах). Незакоммичено: вся работа этапа 1 (коммит только по просьбе пользователя).

**Качество рендера (проход сделан):** студийное окружение (процедурное), N8AO + Bloom + SMAA + ACES (`@react-three/postprocessing`), контактные/мягкие тени, процедурная нормаль-карта ткани (плетение), subdivision футболки. См. `artifacts/review/stage1-quality-final.png`. Цвет ткани задаётся в просмотрщике (светло-серый), т.к. базовый цвет MHCLO тёмный.

**Известные мелочи на потом:** ~180 безвредных WebGL-warning (`glBlitFramebuffer` от N8AO, depth) — консольный шум, не влияет на рендер; морф `weight` даёт малую дельту (калибровка, этап 3); камера не авто-фитит высоких фигур; пол андрогинный; модель лысая/безликая (можно добавить CC0-причёску и кожу — следующий апгрейд качества, если нужно).

**Блокеры:** нет.

**Окружение:** Windows 11, PowerShell. Node 22.22, Python 3.13.5, git-lfs 3.7.
Blender: `C:\Program Files\Blender Foundation\Blender 5.1\blender.exe` (5.1.2, Python 3.13.9). MPFB-модуль: `bl_ext.blender_org.mpfb.*`.
**web dev-сервер:** `cd web && npm run dev -- --host 127.0.0.1` (IPv4 обязателен).
**Пайплайн тела:** `blender --background --python pipeline/gen_body.py -- --out artifacts/blend/body.blend`, затем `... export_glb.py -- --in artifacts/blend/body.blend --out artifacts/glb/body.glb`, GLB копируется в `web/public/models/`.
**git push:** через прокси `socks5h://127.0.0.1:10808` (держать включённым) либо обход `-c "http.https://github.com/.proxy="`.
