"""Реалистичная драпировка футболки через cloth-симуляцию (этап 1, эксперимент).

Запуск (headless), после gen_body.py:
    blender --background --python pipeline/sim_garment.py -- \
        --in artifacts/blend/body.blend --out artifacts/blend/top_sim.blend

Идея (физика вместо геометрии):
  1. Стартовая футболка — свободная оболочка от торса (gen_clothing._drape),
     приведённая к нейтрали (без shape keys).
  2. Приколоть верхний край (плечи) к фигуре и прогнать cloth-симуляцию с
     гравитацией и столкновением с телом → ткань сама провисает складками.
  3. Заморозить итоговую форму (запечь координаты симуляции в меш).
  4. (--morphs) привязать задрапированную футболку к телу через Surface Deform
     и запечь деформацию под каждый морф тела в shape keys одежды — тогда
     физически задрапированная вещь продолжает следовать фигуре.

Морфы добавляются вторым этапом; без --morphs выдаётся статичная (нейтральная)
задрапированная футболка для визуальной оценки качества драпировки.
"""

from __future__ import annotations

import os
import sys

import bpy

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)
import gen_clothing  # noqa: E402  (переиспользуем кройку + драпировку как старт)
import morphs  # noqa: E402

PIN_BAND = 0.045   # верхний поясок (м), приколотый к плечам
SIM_FRAMES = 80
PIN_GROUP = "pin"


def _args(argv: list[str]) -> dict:
    if "--" in argv:
        argv = argv[argv.index("--") + 1 :]
    else:
        argv = []
    out = {
        "in": "artifacts/blend/body.blend",
        "out": "artifacts/blend/top_sim.blend",
        "morphs": "--morphs" in argv,
    }
    for key in ("in", "out"):
        flag = f"--{key}"
        if flag in argv:
            out[key] = argv[argv.index(flag) + 1]
    out["in"] = os.path.abspath(out["in"])
    out["out"] = os.path.abspath(out["out"])
    return out


def _neutral_collision_body(body: bpy.types.Object) -> bpy.types.Object:
    col = body.copy()
    col.data = body.data.copy()
    bpy.context.collection.objects.link(col)
    if col.data.shape_keys is not None:
        col.shape_key_clear()  # нейтральная форма
    col.name = "CollisionBody"
    col.modifiers.new("Collision", "COLLISION")
    return col


def _make_start_top(body: bpy.types.Object) -> bpy.types.Object:
    # свободная футболка-оболочка (как в gen_clothing), затем сводим к нейтрали
    spec = next(g for g in gen_clothing.GARMENTS if g["name"] == "Top")
    g = gen_clothing.build_garment(body, spec)
    if g.data.shape_keys is not None:
        g.shape_key_clear()  # старт симуляции — одна нейтральная форма
    return g


def _add_pin_group(obj: bpy.types.Object) -> None:
    zs = [v.co.z for v in obj.data.vertices]
    zmax = max(zs)
    vg = obj.vertex_groups.new(name=PIN_GROUP)
    for i, v in enumerate(obj.data.vertices):
        if v.co.z > zmax - PIN_BAND:
            vg.add([i], 1.0, "REPLACE")


def _setup_cloth(obj: bpy.types.Object) -> bpy.types.Modifier:
    cm = obj.modifiers.new("Cloth", "CLOTH")
    s = cm.settings
    s.quality = 8
    s.mass = 0.3
    s.tension_stiffness = 15
    s.compression_stiffness = 15
    s.shear_stiffness = 5
    s.bending_stiffness = 0.5
    s.vertex_group_mass = PIN_GROUP
    s.pin_stiffness = 1.0
    cs = cm.collision_settings
    cs.distance_min = 0.006
    cs.use_self_collision = False
    return cm


def _bake_sim(obj: bpy.types.Object, cm: bpy.types.Modifier) -> None:
    scene = bpy.context.scene
    scene.frame_start = 1
    scene.frame_end = SIM_FRAMES
    for f in range(1, SIM_FRAMES + 1):
        scene.frame_set(f)
    # заморозить координаты симуляции в меш и убрать модификатор
    deg = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(deg)
    me = ev.to_mesh()
    coords = [v.co.copy() for v in me.vertices]
    ev.to_mesh_clear()
    obj.modifiers.remove(cm)
    for i, v in enumerate(obj.data.vertices):
        v.co = coords[i]
    obj.data.update()


def _bake_morphs(top: bpy.types.Object, body: bpy.types.Object) -> None:
    """Привязать задрапированную футболку к телу и запечь морфы тела в shape keys."""
    # Surface Deform: одежда следует за поверхностью тела
    sd = top.modifiers.new("SurfaceDeform", "SURFACE_DEFORM")
    sd.target = body
    with bpy.context.temp_override(object=top, active_object=top):
        bpy.ops.object.surfacedeform_bind(modifier=sd.name)

    def eval_coords():
        deg = bpy.context.evaluated_depsgraph_get()
        ev = top.evaluated_get(deg)
        me = ev.to_mesh()
        c = [v.co.copy() for v in me.vertices]
        ev.to_mesh_clear()
        return c

    body_keys = body.data.shape_keys.key_blocks
    # нейтраль (все морфы 0)
    for kb in body_keys:
        if kb.name != "Basis":
            kb.value = 0.0
    neutral = eval_coords()

    # Basis одежды = нейтральная задрапированная форма
    top.shape_key_add(name="Basis", from_mix=False)
    n = len(top.data.vertices)

    for morph in morphs.BODY_MORPHS_ACTIVE:
        kb = body_keys.get(morph)
        if kb is None:
            continue
        kb.value = 1.0
        deformed = eval_coords()
        kb.value = 0.0
        skb = top.shape_key_add(name=morph, from_mix=False)
        base = top.data.shape_keys.key_blocks["Basis"].data
        for i in range(n):
            skb.data[i].co = base[i].co + (deformed[i] - neutral[i])

    # убрать Surface Deform — деформация уже запечена в shape keys
    top.modifiers.remove(sd)


def main():
    a = _args(sys.argv)
    bpy.ops.wm.open_mainfile(filepath=a["in"])
    body = bpy.data.objects.get("Body")
    if body is None:
        raise SystemExit(f"object 'Body' not found in {a['in']}")

    col = _neutral_collision_body(body)
    top = _make_start_top(body)
    _add_pin_group(top)
    cm = _setup_cloth(top)
    _bake_sim(top, cm)
    print("SIM_DONE verts", len(top.data.vertices))

    if a["morphs"]:
        _bake_morphs(top, body)
        keys = [kb.name for kb in top.data.shape_keys.key_blocks]
        print("MORPHS_BAKED", keys)

    # убрать вспомогательное тело-коллайдер из файла
    bpy.data.objects.remove(col, do_unlink=True)

    os.makedirs(os.path.dirname(a["out"]), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=a["out"])
    print("SAVED", a["out"])


if __name__ == "__main__":
    main()
