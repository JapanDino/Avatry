"""Генерация параметрического тела с морф-таргетами (этап 1, задача 1.3).

Запуск (headless):
    blender --background --python pipeline/gen_body.py -- --out assets/blend/body.blend

Что делает:
  1. Строит нейтральное тело MPFB (макросы по 0.5) и запекает его как базовый меш.
  2. Для каждого морфа из morphs.BODY_MORPHS_MVP строит вариант тела с этим
     параметром на максимуме, запекает, берёт по-вершинную дельту относительно
     нейтрали и добавляет её как shape key с именем морфа.
  3. Сохраняет .blend с одним объектом тела: Basis + 5 морфов
     (height, weight, chest, waist, hips), готовый к экспорту в GLB (задача 1.4).

Морфы испекаются как абсолютные shape keys (вес 0..1 = нейтраль → максимум).
Отрицательный вес на клиенте даёт фигуру «меньше нейтрали» линейной экстраполяцией.

Требует: Blender 5.1 + MPFB 2.0.15 (extensions.blender.org). Модуль MPFB живёт
в пространстве имён bl_ext.blender_org.mpfb.* (см. docs/STATUS.md).
"""

from __future__ import annotations

import os
import sys

import bmesh
import bpy
from mathutils import Vector

# Группы вспомогательной геометрии MakeHuman (привязка одежды/костей: «юбка»,
# суставные кубы, helper-вершины глаз/зубов). В аватар не идут — удаляем.
HELPER_GROUPS = {"HelperGeometry", "JointCubes"}

# --- импорт канонического словаря морфов из этого же каталога ---
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)
import morphs  # noqa: E402  (после правки sys.path)

# --- MPFB API (пространство имён расширения Blender) ---
from bl_ext.blender_org.mpfb.services.humanservice import HumanService  # noqa: E402
from bl_ext.blender_org.mpfb.services.targetservice import TargetService  # noqa: E402

# Базовые макросы MPFB (нейтральная середина). Пол/грудь задаются per-gender.
_BASE_MACRO: dict = {
    "gender": 0.5,
    "age": 0.5,
    "muscle": 0.5,
    "weight": 0.5,
    "proportions": 0.5,
    "height": 0.5,
    "cupsize": 0.5,
    "firmness": 0.5,
    "race": {"asian": 0.33, "caucasian": 0.33, "african": 0.33},
}


def neutral_macro(gender: str) -> dict:
    """Нейтральные макросы для пола. MakeHuman: gender 0=жен, 1=муж."""
    m = dict(_BASE_MACRO)
    m["race"] = dict(_BASE_MACRO["race"])
    if gender == "male":
        m["gender"] = 0.9
        m["cupsize"] = 0.0
    else:  # female
        m["gender"] = 0.1
        m["cupsize"] = 0.35  # скромнее/натуральнее при среднем обхвате; обхват груди увеличивает
        m["firmness"] = 0.45
    return m


# Совместимость со старым кодом (по умолчанию женское тело).
NEUTRAL_MACRO: dict = neutral_macro("female")

# Как сгенерировать каждый наш морф. Два механизма:
#   ("macro", key)   — сдвинуть макро-ось MPFB до 1.0;
#   ("measure", name)— подгрузить measure-таргет MPFB с весом 1.0.
MORPH_RECIPES: dict[str, tuple[str, str]] = {
    "height": ("macro", "height"),
    "weight": ("macro", "weight"),
    "muscle": ("macro", "muscle"),
    "chest": ("measure", "measure-bust-circ-incr"),
    "waist": ("measure", "measure-waist-circ-incr"),
    "hips": ("measure", "measure-hips-circ-incr"),
    "shoulders": ("measure", "measure-shoulder-dist-incr"),
    "neck": ("measure", "measure-neck-circ-incr"),
    "arm": ("measure", "measure-upperarm-circ-incr"),
    "thigh": ("measure", "measure-thigh-circ-incr"),
}


def _new_human(macro: dict):
    """Чистая сцена + новое тело MPFB без helper-геометрии."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    return HumanService.create_human(
        mask_helpers=False,
        detailed_helpers=False,
        extra_vertex_groups=False,
        macro_detail_dict=dict(macro),
    )


def _strip_helpers(obj: bpy.types.Object) -> None:
    """Удалить вспомогательную геометрию MakeHuman, оставив чистый body-меш.

    Делается через bmesh (без переключения режимов) и детерминированно: один и
    тот же набор групп → один и тот же остаточный порядок вершин во всех телах.
    Вызывать только после bake_targets (когда shape keys уже сплющены).
    """
    me = obj.data
    target_indices = {g.index for g in obj.vertex_groups if g.name in HELPER_GROUPS}
    if not target_indices:
        return
    bm = bmesh.new()
    bm.from_mesh(me)
    deform = bm.verts.layers.deform.active
    if deform is not None:
        doomed = [v for v in bm.verts if target_indices & set(v[deform].keys())]
        bmesh.ops.delete(bm, geom=doomed, context="VERTS")
    bm.to_mesh(me)
    bm.free()
    me.update()


def _baked_coords(macro: dict, measure: str | None = None) -> list[Vector]:
    """Построить тело, опц. применить measure-таргет, запечь, срезать helpers."""
    obj = _new_human(macro)
    if measure is not None:
        full = TargetService.target_full_path(measure)
        if not full or not os.path.isfile(full):
            raise FileNotFoundError(f"MPFB target not found: {measure}")
        TargetService.load_target(obj, full, weight=1.0, name=measure)
    TargetService.bake_targets(obj)
    _strip_helpers(obj)
    return [v.co.copy() for v in obj.data.vertices]


def _variant_coords(morph: str, base_macro: dict) -> list[Vector]:
    kind, ref = MORPH_RECIPES[morph]
    if kind == "macro":
        macro = dict(base_macro)
        macro[ref] = 1.0
        return _baked_coords(macro)
    if kind == "measure":
        return _baked_coords(base_macro, measure=ref)
    raise ValueError(f"unknown recipe kind: {kind}")


def build_body(gender: str = "female") -> bpy.types.Object:
    """Собрать тело пола `gender` с морфами. Возвращает объект тела."""
    base_macro = neutral_macro(gender)
    morph_names = list(morphs.BODY_MORPHS_ACTIVE)

    # 1. Варианты считаем первыми (каждый стирает сцену), запоминаем координаты.
    variants: dict[str, list[Vector]] = {
        m: _variant_coords(m, base_macro) for m in morph_names
    }

    # 2. Базовое нейтральное тело строим последним и оставляем как итоговый объект.
    base = _new_human(base_macro)
    TargetService.bake_targets(base)

    # bake мог оставить остаточные shape keys MPFB — очищаем под чистый Basis.
    if base.data.shape_keys is not None:
        base.shape_key_clear()

    # Срезаем helper-геометрию ровно так же, как у вариантов (порядок вершин совпадёт).
    _strip_helpers(base)

    neutral = [v.co.copy() for v in base.data.vertices]
    n = len(neutral)
    for m in morph_names:
        if len(variants[m]) != n:
            raise RuntimeError(
                f"vertex count mismatch for '{m}': {len(variants[m])} vs {n}"
            )

    # 3. Basis + по shape key на морф (абсолютные позиции вершин варианта).
    base.shape_key_add(name="Basis", from_mix=False)
    for m in morph_names:
        skb = base.shape_key_add(name=m, from_mix=False)
        var = variants[m]
        for i in range(n):
            skb.data[i].co = var[i]

    base.name = "Body"
    base.data.name = "BodyMesh"
    return base


def _parse_args(argv: list[str]) -> tuple[str, str]:
    # аргументы после "--" в командной строке Blender
    if "--" in argv:
        argv = argv[argv.index("--") + 1 :]
    else:
        argv = []
    out = "assets/blend/body.blend"
    gender = "female"
    if "--out" in argv:
        out = argv[argv.index("--out") + 1]
    if "--gender" in argv:
        gender = argv[argv.index("--gender") + 1]
    return os.path.abspath(out), gender


def main():
    out_path, gender = _parse_args(sys.argv)
    body = build_body(gender)

    keys = [kb.name for kb in body.data.shape_keys.key_blocks]
    print("BODY_BUILT", gender, body.name, "VERTS", len(body.data.vertices))
    print("SHAPEKEYS", keys)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=out_path)
    print("SAVED", out_path)


if __name__ == "__main__":
    main()
