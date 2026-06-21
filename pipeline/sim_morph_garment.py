"""Одежда с физикой: morph targets из настоящих cloth-симуляций (этап 1/2).

Запуск (headless), после gen_body.py:
    blender --background --python pipeline/sim_morph_garment.py -- \
        --body artifacts/blend/body.blend \
        --mhclo assets/clothes/elvs_crude_t-shirt_male/elvs_crude_t-shirt_male.mhclo \
        --name Top --ease 0.03 --out artifacts/blend/top_sim.blend

Идея (выбранный подход «Blender-сим → морфы»):
  1. Стартовая вещь — готовый MHCLO-ассет (аккуратная топология), раздутый на
     припуск `ease` (свободный крой, к которому стремится ткань).
  2. Для нейтрали и для каждого морфа тела:
       - сдвигаем старт по ближайшей вершине тела (чтобы вещь следовала фигуре и
         тело её не протыкало);
       - ставим телу-коллайдеру этот морф;
       - гоним cloth-симуляцию (пин на плечах, гравитация, столкновение) →
         ткань реалистично оседает складками.
  3. Запекаем: Basis = нейтральная драпировка; shape key морфа = (драпировка на
     морфе − нейтраль). В браузер уходят дешёвые morph targets, но каждый несёт
     физически корректную посадку — без runtime-нагрузки.
"""

from __future__ import annotations

import os
import sys

import bmesh
import bpy
from mathutils import Vector
from mathutils.kdtree import KDTree

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)
import gen_body  # noqa: E402  (neutral_macro по полу)
import morphs  # noqa: E402

from bl_ext.blender_org.mpfb.services.humanservice import HumanService  # noqa: E402

PIN_BAND = 0.04      # верхний поясок (м), приколотый к плечам
SIM_FRAMES = 95      # больше кадров — ткань успевает осесть складками
SUBDIV = 1           # плотность меша (subdiv 2 + толщина + 10 морфов превышают бюджет GLB)
THICKNESS = 0.006    # толщина ткани (Solidify), м


def _args(argv: list[str]) -> dict:
    if "--" in argv:
        argv = argv[argv.index("--") + 1 :]
    else:
        argv = []
    out = {
        "body": "artifacts/blend/body.blend",
        "mhclo": "assets/clothes/elvs_crude_t-shirt_male/elvs_crude_t-shirt_male.mhclo",
        "name": "Top",
        "gender": "female",
        "ease": "0.03",
        "color": "0.5,0.52,0.58",
        "out": "artifacts/blend/top_sim.blend",
    }
    for key in list(out):
        flag = f"--{key}"
        if flag in argv:
            out[key] = argv[argv.index(flag) + 1]
    out["body"] = os.path.abspath(out["body"])
    out["mhclo"] = os.path.abspath(out["mhclo"])
    out["out"] = os.path.abspath(out["out"])
    return out


def _load_garment_mesh(mhclo: str, gender: str) -> bpy.types.Object:
    """Примерить MHCLO на временное тело нужного пола, вернуть объект одежды."""
    fit_body = HumanService.create_human(
        macro_detail_dict=gen_body.neutral_macro(gender)
    )
    cloth = HumanService.add_mhclo_asset(
        mhclo, fit_body, asset_type="Clothes", subdiv_levels=0,
        material_type="MAKESKIN", set_up_rigging=False,
        interpolate_weights=True, import_subrig=False, import_weights=False,
    )
    if cloth is None:
        raise SystemExit("add_mhclo_asset returned None")
    mw = cloth.matrix_world.copy()
    cloth.parent = None
    cloth.matrix_world = mw
    bpy.data.objects.remove(fit_body, do_unlink=True)
    return cloth


def _inflate(obj: bpy.types.Object, ease: float) -> None:
    """Раздуть меш наружу по нормали на ease — припуск ткани.

    С вертикальным затуханием: у плеч/ворота 0 (вещь садится на тело), нарастает
    к низу. Иначе пин держит раздутый верх над плечами — футболка «парит».
    """
    obj.data.calc_loop_triangles()
    normals = [v.normal.copy() for v in obj.data.vertices]
    zs = [v.co.z for v in obj.data.vertices]
    zmax, zmin = max(zs), min(zs)
    span = (zmax - zmin) or 1e-6

    # плавный (smoothstep) переход от 0 у плеч/груди к полному раздуву ниже груди —
    # без резкой границы у пройм, которая давала сборку на плечах
    lo, hi = 0.18, 0.55  # t=(zmax-z)/span: <0.18 плечи+грудь по телу, >0.55 полный припуск
    for i, v in enumerate(obj.data.vertices):
        t = (zmax - v.co.z) / span
        u = max(0.0, min(1.0, (t - lo) / (hi - lo)))
        factor = u * u * (3.0 - 2.0 * u)  # smoothstep
        v.co = v.co + normals[i] * ease * factor
    obj.data.update()


def _apply_subdiv(obj: bpy.types.Object, levels: int) -> None:
    if levels <= 0:
        return
    mod = obj.modifiers.new("Subsurf", "SUBSURF")
    mod.levels = mod.render_levels = levels
    with bpy.context.temp_override(object=obj, active_object=obj):
        bpy.ops.object.modifier_apply(modifier=mod.name)


def _apply_solidify(obj: bpy.types.Object, thickness: float) -> None:
    """Придать ткани толщину (края подола/рукавов перестают быть бумажными).
    Применяется до запекания морфов (пока нет shape keys)."""
    mod = obj.modifiers.new("Solidify", "SOLIDIFY")
    mod.thickness = thickness
    mod.offset = -1.0  # наращиваем внутрь, видимая внешняя поверхность не меняется
    with bpy.context.temp_override(object=obj, active_object=obj):
        bpy.ops.object.modifier_apply(modifier=mod.name)


def _pin_group(obj: bpy.types.Object) -> str:
    zmax = max(v.co.z for v in obj.data.vertices)
    vg = obj.vertex_groups.new(name="pin")
    for i, v in enumerate(obj.data.vertices):
        if v.co.z > zmax - PIN_BAND:
            vg.add([i], 1.0, "REPLACE")
    return vg.name


def _body_morph_deltas(body: bpy.types.Object) -> tuple[list[Vector], dict[str, list[Vector]]]:
    keys = body.data.shape_keys.key_blocks
    basis = [keys["Basis"].data[i].co.copy() for i in range(len(keys["Basis"].data))]
    deltas: dict[str, list[Vector]] = {}
    for m in morphs.BODY_MORPHS_ACTIVE:
        kb = keys.get(m)
        if kb is None:
            continue
        deltas[m] = [kb.data[i].co - basis[i] for i in range(len(basis))]
    return basis, deltas


def _nearest_map(garment_start: list[Vector], body_basis: list[Vector]) -> list[int]:
    kd = KDTree(len(body_basis))
    for i, co in enumerate(body_basis):
        kd.insert(co, i)
    kd.balance()
    return [kd.find(co)[1] for co in garment_start]


def _set_body_morph(body: bpy.types.Object, morph: str | None) -> None:
    for kb in body.data.shape_keys.key_blocks:
        if kb.name != "Basis":
            kb.value = 1.0 if kb.name == morph else 0.0


def _simulate(garment: bpy.types.Object, start: list[Vector], pin: str) -> list[Vector]:
    """Сбросить меш в start, прогнать свежую cloth-симуляцию, вернуть результат."""
    for i, v in enumerate(garment.data.vertices):
        v.co = start[i]
    garment.data.update()

    for mod in [m for m in garment.modifiers if m.type == "CLOTH"]:
        garment.modifiers.remove(mod)
    cm = garment.modifiers.new("Cloth", "CLOTH")
    s = cm.settings
    s.quality = 12
    s.mass = 0.3
    s.air_damping = 1.6          # мягче падение, меньше «хлопков»
    s.tension_stiffness = 40     # держит форму, не растягивается в валики
    s.compression_stiffness = 40  # не сминается в складки-кольца
    s.shear_stiffness = 15
    s.bending_stiffness = 3.5    # почти гладкая драпировка, без локальных сборок
    s.vertex_group_mass = pin
    s.pin_stiffness = 1.0
    cs = cm.collision_settings
    # больший зазор: ткань мягко перекрывает вогнутости (поясница, под грудью),
    # а не собирается в них складками
    cs.distance_min = 0.008
    cs.use_self_collision = False

    scene = bpy.context.scene
    scene.frame_start = 1
    scene.frame_end = SIM_FRAMES
    for f in range(1, SIM_FRAMES + 1):
        scene.frame_set(f)

    deg = bpy.context.evaluated_depsgraph_get()
    ev = garment.evaluated_get(deg)
    me = ev.to_mesh()
    coords = [v.co.copy() for v in me.vertices]
    ev.to_mesh_clear()
    garment.modifiers.remove(cm)
    return coords


def main():
    a = _args(sys.argv)

    bpy.ops.wm.open_mainfile(filepath=a["body"])
    body = bpy.data.objects.get("Body")
    if body is None:
        raise SystemExit("object 'Body' not found")
    body.modifiers.new("Collision", "COLLISION")

    # стартовая вещь: MHCLO + припуск + сглаживание
    garment = _load_garment_mesh(a["mhclo"], a["gender"])
    garment.name = a["name"]
    garment.data.name = f"{a['name']}Mesh"
    _inflate(garment, float(a["ease"]))
    _apply_subdiv(garment, SUBDIV)
    pin = _pin_group(garment)

    base_start = [v.co.copy() for v in garment.data.vertices]

    # Basis = реальная нейтральная драпировка (одна cloth-симуляция → свободный крой).
    _set_body_morph(body, None)
    neutral = _simulate(garment, base_start, pin)
    print("SIM neutral done")
    for i, co in enumerate(neutral):
        garment.data.vertices[i].co = co
    garment.data.update()

    # Толщина ткани — пока нет shape keys (Solidify меняет число вершин).
    _apply_solidify(garment, THICKNESS)

    # Морфы — ЛИНЕЙНЫЙ перенос дельт тела по ближайшей вершине тела (чисто
    # блендится, без взрывов на комбинациях). Ближайшие считаем уже по
    # утолщённому мешу. Per-morph симуляция даёт нелинейные дельты, рвущие меш
    # при сложении — поэтому не используется (см. DECISIONS).
    body_basis, body_deltas = _body_morph_deltas(body)
    cloth_co = [v.co.copy() for v in garment.data.vertices]
    nearest = _nearest_map(cloth_co, body_basis)

    garment.shape_key_add(name="Basis", from_mix=False)
    gb = garment.data.shape_keys.key_blocks["Basis"].data
    for m in morphs.BODY_MORPHS_ACTIVE:
        if m not in body_deltas:
            continue
        d = body_deltas[m]
        skb = garment.shape_key_add(name=m, from_mix=False)
        for i in range(len(cloth_co)):
            skb.data[i].co = gb[i].co + d[nearest[i]]
        print(f"morph {m} baked (nearest)")

    # гладкое затенение + материал
    for poly in garment.data.polygons:
        poly.use_smooth = True
    rgb = tuple(float(x) for x in a["color"].split(","))
    mat = bpy.data.materials.new(name=f"{a['name']}Fabric")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
        bsdf.inputs["Roughness"].default_value = 0.9
    garment.data.materials.clear()
    garment.data.materials.append(mat)

    keys = [kb.name for kb in garment.data.shape_keys.key_blocks]
    print("GARMENT", garment.name, "VERTS", len(garment.data.vertices), "MORPHS", len(keys) - 1)
    os.makedirs(os.path.dirname(a["out"]), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=a["out"])
    print("SAVED", a["out"])


if __name__ == "__main__":
    main()
