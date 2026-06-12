"""Калибровка «мерки → веса морфов» для формы ввода (этап 3, задача 3.3).

Запуск (headless), после gen_body.py для обоих полов:
    blender --background --python pipeline/calibrate.py -- \
        --female artifacts/blend/body_female.blend \
        --male artifacts/blend/body_male.blend \
        --out web/public/calibration.json

Для каждого пола измеряет рост (см) и обхваты груди/талии/бёдер (см) на
нейтральном теле и на теле с одним морфом = 1. Линейная инверсия даёт:
    influence = (мерка_пользователя − neutral) / perUnit
Клиент по введённым меркам считает веса морфов (web/src/measurements.ts).

Обхват ≈ периметр выпуклой оболочки горизонтального среза тела на уровне
ориентира (доля роста). Приближение, но монотонное и стабильное — достаточно
для расчёта посадки; точная антропометрия — поздняя полировка.
"""

from __future__ import annotations

import json
import os
import sys

import bpy

# Уровни замера обхватов как доли роста от ступней.
LANDMARKS = {"chest": 0.71, "waist": 0.63, "hips": 0.55}
BAND = 0.02  # полутолщина среза, м
TORSO_X = 0.20  # |x|-отсечка: выкинуть руки/плечи из среза
TORSO_Y = 0.30  # |y|-отсечка по глубине
# Морф, отвечающий за каждую мерку.
MEASURE_MORPH = {"chest": "chest", "waist": "waist", "hips": "hips"}


def _args(argv: list[str]) -> dict:
    if "--" in argv:
        argv = argv[argv.index("--") + 1 :]
    else:
        argv = []
    out = {
        "female": "artifacts/blend/body_female.blend",
        "male": "artifacts/blend/body_male.blend",
        "out": "web/public/calibration.json",
    }
    for k in list(out):
        if f"--{k}" in argv:
            out[k] = argv[argv.index(f"--{k}") + 1]
    for k in out:
        out[k] = os.path.abspath(out[k])
    return out


def _convex_hull_perimeter(pts: list[tuple[float, float]]) -> float:
    """Периметр 2D выпуклой оболочки (monotone chain)."""
    pts = sorted(set(pts))
    if len(pts) < 3:
        return 0.0

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    hull = lower[:-1] + upper[:-1]
    per = 0.0
    for i in range(len(hull)):
        a = hull[i]
        b = hull[(i + 1) % len(hull)]
        per += ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
    return per


def _eval_coords(obj: bpy.types.Object) -> list[tuple[float, float, float]]:
    deg = bpy.context.evaluated_depsgraph_get()
    me = obj.evaluated_get(deg).to_mesh()
    coords = [(v.co.x, v.co.y, v.co.z) for v in me.vertices]
    obj.evaluated_get(deg).to_mesh_clear()
    return coords


def _measure(obj: bpy.types.Object) -> dict:
    coords = _eval_coords(obj)
    zs = [c[2] for c in coords]
    zmin, zmax = min(zs), max(zs)
    stature_cm = (zmax - zmin) * 100.0
    out = {"height": stature_cm}
    for name, frac in LANDMARKS.items():
        zt = zmin + frac * (zmax - zmin)
        # только торс: срез по z, без рук/ног (отсечка по |x|,|y|)
        ring = [
            (c[0], c[1])
            for c in coords
            if abs(c[2] - zt) < BAND and abs(c[0]) < TORSO_X and abs(c[1]) < TORSO_Y
        ]
        out[name] = _convex_hull_perimeter(ring) * 100.0
    return out


def _set_morph(obj: bpy.types.Object, morph: str | None) -> None:
    for kb in obj.data.shape_keys.key_blocks:
        if kb.name != "Basis":
            kb.value = 1.0 if kb.name == morph else 0.0


def calibrate_gender(blend: str) -> dict:
    bpy.ops.wm.open_mainfile(filepath=blend)
    obj = bpy.data.objects["Body"]

    _set_morph(obj, None)
    neutral = _measure(obj)

    per_unit = {}
    # рост — морф height
    _set_morph(obj, "height")
    per_unit["height"] = _measure(obj)["height"] - neutral["height"]
    # обхваты — свои морфы
    for meas, morph in MEASURE_MORPH.items():
        _set_morph(obj, morph)
        per_unit[meas] = _measure(obj)[meas] - neutral[meas]
    _set_morph(obj, None)

    return {
        "neutral": {k: round(v, 1) for k, v in neutral.items()},
        "perUnit": {k: round(v, 2) for k, v in per_unit.items()},
    }


def main():
    a = _args(sys.argv)
    result = {
        "female": calibrate_gender(a["female"]),
        "male": calibrate_gender(a["male"]),
    }
    os.makedirs(os.path.dirname(a["out"]), exist_ok=True)
    with open(a["out"], "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print("CALIBRATION", json.dumps(result, ensure_ascii=False))
    print("SAVED", a["out"])


if __name__ == "__main__":
    main()
