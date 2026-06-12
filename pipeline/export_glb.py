"""Экспорт тела/одежды в GLB с морф-таргетами (этап 1, задача 1.4).

Запуск (headless):
    blender --background --python pipeline/export_glb.py -- \
        --in artifacts/blend/body.blend --out assets/glb/body.glb

Экспортирует объект (по умолчанию "Body") в GLB:
  - shape keys → morph targets (имена сохраняются в extras.targetNames,
    three.js читает их как morphTargetDictionary);
  - glTF +Y up (Blender Z-up конвертируется автоматически).

После экспорта печатает самопроверку: число морф-таргетов, их имена и размер
файла относительно бюджета (тело ≤ 4 МБ из docs/ARCHITECTURE.md).

При --draco включает сжатие геометрии Draco (в вебе тогда нужен DRACOLoader).
"""

from __future__ import annotations

import json
import os
import struct
import sys

import bpy

BUDGET_MB = 4.0  # бюджет тела из ARCHITECTURE.md


def _args(argv: list[str]) -> dict:
    if "--" in argv:
        argv = argv[argv.index("--") + 1 :]
    else:
        argv = []
    out = {
        "in": "artifacts/blend/body.blend",
        "out": "assets/glb/body.glb",
        "obj": "Body",
        "draco": "--draco" in argv,
    }
    for key in ("in", "out", "obj"):
        flag = f"--{key}"
        if flag in argv:
            out[key] = argv[argv.index(flag) + 1]
    out["in"] = os.path.abspath(out["in"])
    out["out"] = os.path.abspath(out["out"])
    return out


def _glb_morph_report(glb_path: str) -> tuple[int, list[str]]:
    """Прочитать JSON-чанк GLB и вернуть (число морф-таргетов, имена)."""
    with open(glb_path, "rb") as f:
        magic, _version, _length = struct.unpack("<4sII", f.read(12))
        if magic != b"glTF":
            raise ValueError("not a GLB file")
        chunk_len, chunk_type = struct.unpack("<I4s", f.read(8))
        if chunk_type != b"JSON":
            raise ValueError("first chunk is not JSON")
        gltf = json.loads(f.read(chunk_len).decode("utf-8"))

    names: list[str] = []
    target_count = 0
    for mesh in gltf.get("meshes", []):
        extras = mesh.get("extras", {})
        if "targetNames" in extras:
            names = list(extras["targetNames"])
        for prim in mesh.get("primitives", []):
            target_count = max(target_count, len(prim.get("targets", [])))
    return target_count, names


def main():
    a = _args(sys.argv)

    bpy.ops.wm.open_mainfile(filepath=a["in"])

    obj = bpy.data.objects.get(a["obj"])
    if obj is None:
        raise SystemExit(f"object '{a['obj']}' not found in {a['in']}")

    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

    os.makedirs(os.path.dirname(a["out"]), exist_ok=True)
    bpy.ops.export_scene.gltf(
        filepath=a["out"],
        export_format="GLB",
        use_selection=True,
        export_morph=True,
        export_morph_normal=False,  # морфы только по позициям — легче файл
        export_apply=False,         # не применять модификаторы (ломает shape keys)
        export_yup=True,
        export_draco_mesh_compression_enable=a["draco"],
    )

    size_mb = os.path.getsize(a["out"]) / (1024 * 1024)
    count, names = _glb_morph_report(a["out"])
    print("EXPORTED", a["out"])
    print("MORPH_TARGETS", count, names)
    print(f"SIZE_MB {size_mb:.3f} BUDGET {BUDGET_MB} OK={size_mb <= BUDGET_MB}")


if __name__ == "__main__":
    main()
