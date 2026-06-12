"""Базовый комплект одежды «для приличия»: верх + шорты (этап 1, доп. задача).

Запуск (headless), после gen_body.py:
    blender --background --python pipeline/gen_clothing.py -- \
        --in artifacts/blend/body.blend --out artifacts/blend/avatar.blend

Идея: одежда строится из поверхности тела, но «висит», а не облегает. Берём
готовое тело с морфами, дублируем, оставляем вершины нужной области (Z-полоса
∩ |x|<cut, чтобы не зацепить руки) и драпируем: режем область на горизонтальные
слои и раздуваем каждый нижний слой минимум до ширины самого широкого слоя выше
плюс припуск (ease). Так ткань падает прямо от плеч/груди и не втягивается на
талии — силуэт обычной свободной футболки, а не комбинезона в облипку.

Поскольку вершины одежды — подмножество вершин тела, а драпировочный сдвиг
постоянен по вершине во всех shape keys, одежда наследует ВСЕ морфы тела
(грудь/бёдра/рост…) и деформируется вместе с фигурой. Это «заглушка для
приличия» и одновременно репетиция привязки одежды к морфам (этап 2).

Выход: один avatar.blend с объектами Body + Top + Shorts, готов к export_glb.py.
"""

from __future__ import annotations

import os
import sys

import bmesh
import bpy

# Кройки. ease — припуск (полуширина, м), на сколько ткань отстоит от тела;
# hem_flare — доп. расширение к нижнему краю (подол свободнее ворота);
# z_lo/z_hi — доли роста; x_cut — отсечка по |x|, чтобы не зацепить руки.
# Доли подобраны по ориентирам тела (см. landmarks): торс |x|<~0.2, руки |x|>0.25.
GARMENTS = [
    # свободная футболка: от бёдер до плеч, висит от груди
    {
        "name": "Top",
        "z_lo": 0.55,
        "z_hi": 0.86,
        "x_cut": 0.205,
        "ease": 0.020,
        "hem_flare": 0.0,
        "cinch": 0.6,
        "n_slices": 26,
        "color": (0.12, 0.13, 0.16, 1.0),  # тёмно-серый
    },
    # свободные шорты: от середины бедра до талии
    {
        "name": "Shorts",
        "z_lo": 0.33,
        "z_hi": 0.585,
        "x_cut": 0.27,
        "ease": 0.014,
        "hem_flare": 0.004,
        "cinch": 0.4,
        "n_slices": 18,
        "color": (0.18, 0.20, 0.28, 1.0),  # сине-серый
    },
]


def _args(argv: list[str]) -> dict:
    if "--" in argv:
        argv = argv[argv.index("--") + 1 :]
    else:
        argv = []
    out = {"in": "artifacts/blend/body.blend", "out": "artifacts/blend/avatar.blend"}
    for key in ("in", "out"):
        flag = f"--{key}"
        if flag in argv:
            out[key] = argv[argv.index(flag) + 1]
    out["in"] = os.path.abspath(out["in"])
    out["out"] = os.path.abspath(out["out"])
    return out


def _basis_bounds(mesh) -> tuple[float, float]:
    basis = mesh.shape_keys.key_blocks["Basis"].data
    zs = [basis[i].co.z for i in range(len(basis))]
    return min(zs), max(zs)


def _drape(
    mesh, n_slices: int, ease: float, hem_flare: float, cinch: float = 0.8
) -> None:
    """Сделать оболочку «висящей»: каждый нижний горизонтальный слой раздувается
    в сторону полуширины самого широкого слоя выше + ease (+ расширение к подолу).

    cinch (0..1) — насколько узкие слои подтягиваются к ширине груди: 1.0 = ровный
    столб от груди (бочка), 0.0 = по телу. Промежуточное даёт мягкую драпировку.
    Силуэт падает от плеч/груди, не втягиваясь на талии. Сдвиг постоянен по вершине
    и применяется ко всем shape keys, поэтому морфы сохраняются.
    """
    basis = mesh.shape_keys.key_blocks["Basis"].data
    n = len(mesh.vertices)
    if n == 0:
        return
    zs = [basis[i].co.z for i in range(n)]
    zmin, zmax = min(zs), max(zs)
    span = (zmax - zmin) or 1e-6

    slices: list[list[int]] = [[] for _ in range(n_slices)]
    for i in range(n):
        s = min(n_slices - 1, int((zs[i] - zmin) / span * n_slices))
        slices[s].append(i)

    offsets: list[tuple[float, float] | None] = [None] * n
    run_x = run_y = 0.0
    # сверху вниз: накапливаем максимальную полуширину
    for s in range(n_slices - 1, -1, -1):
        idxs = slices[s]
        if not idxs:
            continue
        cx = sum(basis[i].co.x for i in idxs) / len(idxs)
        cy = sum(basis[i].co.y for i in idxs) / len(idxs)
        max_x = max(abs(basis[i].co.x - cx) for i in idxs) or 1e-6
        max_y = max(abs(basis[i].co.y - cy) for i in idxs) or 1e-6
        run_x = max(run_x, max_x)
        run_y = max(run_y, max_y)
        frac_from_top = (n_slices - 1 - s) / max(1, n_slices - 1)
        # цель — между шириной слоя и накопленной шириной груди (cinch), плюс ease
        target_x = max_x + (run_x - max_x) * cinch + ease + hem_flare * frac_from_top
        target_y = max_y + (run_y - max_y) * cinch + ease + hem_flare * frac_from_top
        sx = target_x / max_x
        sy = target_y / max_y
        for i in idxs:
            nx = cx + (basis[i].co.x - cx) * sx
            ny = cy + (basis[i].co.y - cy) * sy
            offsets[i] = (nx - basis[i].co.x, ny - basis[i].co.y)

    for kb in mesh.shape_keys.key_blocks:
        for i in range(n):
            o = offsets[i]
            if o is not None:
                c = kb.data[i].co
                kb.data[i].co = (c.x + o[0], c.y + o[1], c.z)
    for i in range(n):
        mesh.vertices[i].co = basis[i].co
    mesh.update()


def build_garment(body: bpy.types.Object, spec: dict) -> bpy.types.Object:
    mesh = body.data.copy()
    g = bpy.data.objects.new(spec["name"], mesh)
    bpy.context.collection.objects.link(g)

    zmin, zmax = _basis_bounds(mesh)
    H = zmax - zmin
    z0 = zmin + spec["z_lo"] * H
    z1 = zmin + spec["z_hi"] * H
    x_cut = spec["x_cut"]

    basis = mesh.shape_keys.key_blocks["Basis"].data

    # вершины, которые ОСТАВЛЯЕМ (область одежды); остальное удалим
    keep = [
        i
        for i in range(len(basis))
        if z0 <= basis[i].co.z <= z1 and abs(basis[i].co.x) < x_cut
    ]
    keep_set = set(keep)

    # удаление через bmesh (shape keys сохраняются в слоях)
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.verts.ensure_lookup_table()
    doomed = [v for v in bm.verts if v.index not in keep_set]
    bmesh.ops.delete(bm, geom=doomed, context="VERTS")
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()

    # драпировка: раздуть слои так, чтобы ткань висела, а не облегала
    _drape(
        mesh,
        spec["n_slices"],
        spec["ease"],
        spec["hem_flare"],
        spec.get("cinch", 0.8),
    )

    # материал ткани
    mat = bpy.data.materials.new(name=f"{spec['name']}Fabric")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = spec["color"]
        bsdf.inputs["Roughness"].default_value = 0.9
    mesh.materials.clear()
    mesh.materials.append(mat)

    g.name = spec["name"]
    mesh.name = f"{spec['name']}Mesh"
    return g


def main():
    a = _args(sys.argv)
    bpy.ops.wm.open_mainfile(filepath=a["in"])
    body = bpy.data.objects.get("Body")
    if body is None:
        raise SystemExit(f"object 'Body' not found in {a['in']}")

    for spec in GARMENTS:
        g = build_garment(body, spec)
        keys = [kb.name for kb in g.data.shape_keys.key_blocks]
        print(f"GARMENT {g.name} VERTS {len(g.data.vertices)} MORPHS {len(keys) - 1}")

    # один комбинированный .blend: Body + Top + Shorts (экспорт по имени объекта)
    os.makedirs(os.path.dirname(a["out"]), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=a["out"])
    print("SAVED", a["out"])


if __name__ == "__main__":
    main()
