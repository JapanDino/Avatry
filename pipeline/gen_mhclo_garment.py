"""Аккуратная одежда из готового CC0-ассета MakeHuman (MHCLO) (этап 1/2).

Запуск (headless), после gen_body.py:
    blender --background --python pipeline/gen_mhclo_garment.py -- \
        --body artifacts/blend/body.blend \
        --mhclo artifacts/vendor/shirts01/clothes/elvs_crude_t-shirt_male/elvs_crude_t-shirt_male.mhclo \
        --name Top --color 0.12,0.13,0.16 \
        --out artifacts/blend/top_mhclo.blend

Зачем: вместо процедурной/симулированной оболочки берём готовую модель вещи с
правильной топологией (ворот, рукава, подол) — она аккуратна и сделана под
базовый меш MakeHuman. MPFB примеряет MHCLO на тело, затем мы привязываем вещь
к нашему чистому телу через Surface Deform и запекаем деформацию под каждый морф
в shape keys одежды — вещь следует за фигурой. Это целевой метод для каталога
(этап 2): один ассет → одетый параметрический аватар.
"""

from __future__ import annotations

import os
import sys

import bpy

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)
import morphs  # noqa: E402
from mathutils.kdtree import KDTree  # noqa: E402

from bl_ext.blender_org.mpfb.services.humanservice import HumanService  # noqa: E402


def _bake_morphs_nearest(cloth: bpy.types.Object, body: bpy.types.Object) -> None:
    """Запечь морфы тела в одежду через перенос дельт по ближайшей вершине.

    Для каждой вершины одежды находим ближайшую вершину тела (на нейтрали) и
    переносим дельту каждого морфа тела один-в-один. Так одежда смещается локально
    точно как тело — постоянный отступ сохраняется, протыкания нет. Предсказуемее
    Surface Deform, который сглаживает (недо-раздувает) деформацию.
    """
    bkeys = body.data.shape_keys.key_blocks
    bbasis = bkeys["Basis"].data
    nb = len(bbasis)

    kd = KDTree(nb)
    for i in range(nb):
        kd.insert(bbasis[i].co, i)
    kd.balance()

    cverts = cloth.data.vertices
    nc = len(cverts)
    nearest = [kd.find(cverts[i].co)[1] for i in range(nc)]

    cloth.shape_key_add(name="Basis", from_mix=False)
    cbasis = cloth.data.shape_keys.key_blocks["Basis"].data

    for morph in morphs.BODY_MORPHS_ACTIVE:
        mk = bkeys.get(morph)
        if mk is None:
            continue
        skb = cloth.shape_key_add(name=morph, from_mix=False)
        for i in range(nc):
            j = nearest[i]
            delta = mk.data[j].co - bbasis[j].co
            skb.data[i].co = cbasis[i].co + delta


def _args(argv: list[str]) -> dict:
    if "--" in argv:
        argv = argv[argv.index("--") + 1 :]
    else:
        argv = []
    out = {
        "body": "artifacts/blend/body.blend",
        "mhclo": "assets/clothes/elvs_crude_t-shirt_male/elvs_crude_t-shirt_male.mhclo",
        "name": "Top",
        "color": "0.12,0.13,0.16",
        "subdiv": "1",
        "out": "artifacts/blend/top_mhclo.blend",
    }
    for key in list(out):
        flag = f"--{key}"
        if flag in argv:
            out[key] = argv[argv.index(flag) + 1]
    out["body"] = os.path.abspath(out["body"])
    out["mhclo"] = os.path.abspath(out["mhclo"])
    out["out"] = os.path.abspath(out["out"])
    return out


def _fabric_material(name: str, rgb: tuple[float, float, float]):
    mat = bpy.data.materials.new(name=f"{name}Fabric")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
        bsdf.inputs["Roughness"].default_value = 0.9
    return mat


def main():
    a = _args(sys.argv)
    if not a["mhclo"] or not os.path.isfile(a["mhclo"]):
        raise SystemExit(f"mhclo not found: {a['mhclo']}")

    # наш чистый параметрический body (с 10 морфами, нейтраль)
    bpy.ops.wm.open_mainfile(filepath=a["body"])
    body = bpy.data.objects.get("Body")
    if body is None:
        raise SystemExit("object 'Body' not found in body blend")

    # отдельное MPFB-тело с helpers — нужно для примерки MHCLO
    fit_body = HumanService.create_human()
    cloth = HumanService.add_mhclo_asset(
        a["mhclo"], fit_body, asset_type="Clothes", subdiv_levels=0,
        material_type="MAKESKIN", set_up_rigging=False,
        interpolate_weights=True, import_subrig=False, import_weights=False,
    )
    if cloth is None:
        raise SystemExit("add_mhclo_asset returned None")

    # отвязать одежду от примерочного тела, сохранив мировые координаты
    mw = cloth.matrix_world.copy()
    cloth.parent = None
    cloth.matrix_world = mw

    # убрать примерочное тело — оно больше не нужно
    bpy.data.objects.remove(fit_body, do_unlink=True)

    cloth.name = a["name"]
    cloth.data.name = f"{a['name']}Mesh"

    # сглаживание: подразделить (пока нет shape keys — применяется чисто) + smooth shading
    subdiv = int(a.get("subdiv", 1))
    if subdiv > 0:
        mod = cloth.modifiers.new("Subsurf", "SUBSURF")
        mod.levels = subdiv
        mod.render_levels = subdiv
        with bpy.context.temp_override(object=cloth, active_object=cloth):
            bpy.ops.object.modifier_apply(modifier=mod.name)
    for poly in cloth.data.polygons:
        poly.use_smooth = True

    # привязать к нашему телу и запечь морфы в shape keys одежды
    _bake_morphs_nearest(cloth, body)

    # материал ткани
    rgb = tuple(float(x) for x in a["color"].split(","))
    cloth.data.materials.clear()
    cloth.data.materials.append(_fabric_material(a["name"], rgb))

    keys = [kb.name for kb in cloth.data.shape_keys.key_blocks]
    print("GARMENT", cloth.name, "VERTS", len(cloth.data.vertices), "MORPHS", len(keys) - 1)

    os.makedirs(os.path.dirname(a["out"]), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=a["out"])
    print("SAVED", a["out"])
    # короткая проверка, что все наши морфы на месте
    missing = [m for m in morphs.BODY_MORPHS_ACTIVE if m not in keys]
    print("MISSING_MORPHS", missing)


if __name__ == "__main__":
    main()
