"""Генерация per-size GLB одежды (этап 4, задача 4.3).

Для каждого размера из размерной таблицы запускает отдельную cloth-симуляцию с
припуском (inflate) = ease размера / 2π — получается РЕАЛЬНАЯ посадка S/M/L
(обхват зашит в геометрию), а не масштаб одного меша. Выход:
`web/public/models/top_{gender}_{size}.glb` (грузится во вьюере по активному размеру).

Запуск (обычный Python, сам зовёт Blender):
    python pipeline/gen_sizes.py --gender female
    python pipeline/gen_sizes.py --gender male

ease(size) = (обхват_груди_изделия[size] − нейтральный_обхват_тела) / 100 / (2π),
кламп [0.012, 0.10] м. Нейтраль берётся из web/public/calibration.json, таблица —
из web/public/garments/tee-{gender}.json.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BLENDER = os.environ.get(
    "BLENDER", r"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe"
)
MHCLO = {
    "female": "assets/clothes/joepal_crude_t-shirt_female/joepal_crude_t-shirt_female.mhclo",
    "male": "assets/clothes/elvs_crude_t-shirt_male/elvs_crude_t-shirt_male.mhclo",
}


def _ease(garment_chest: float, neutral_chest: float) -> float:
    return max(0.012, min(0.10, (garment_chest - neutral_chest) / 100 / (2 * math.pi)))


def _run(script: str, args: list[str]) -> None:
    cmd = [BLENDER, "--background", "--python", os.path.join(ROOT, "pipeline", script), "--", *args]
    subprocess.run(cmd, check=True, cwd=ROOT)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gender", choices=["female", "male"], required=True)
    g = ap.parse_args().gender

    cal = json.load(open(os.path.join(ROOT, "web/public/calibration.json"), encoding="utf-8"))
    neutral = cal[g]["neutral"]["chest"]
    tee = json.load(open(os.path.join(ROOT, f"web/public/garments/tee-{g}.json"), encoding="utf-8"))

    body = os.path.join(ROOT, f"artifacts/blend/body_{g}.blend")
    mhclo = os.path.join(ROOT, MHCLO[g])

    for s in tee["sizes"]:
        size = s["label"]
        ease = _ease(s["garment"]["chest"], neutral)
        blend = os.path.join(ROOT, f"artifacts/blend/top_{g}_{size}.blend")
        glb = os.path.join(ROOT, f"artifacts/glb/top_{g}_{size}.glb")
        web = os.path.join(ROOT, f"web/public/models/top_{g}_{size}.glb")
        print(f"=== {g} {size} ease={ease:.4f} ===")
        _run("sim_morph_garment.py", [
            "--body", body, "--mhclo", mhclo, "--gender", g,
            "--name", "Top", "--ease", f"{ease:.4f}", "--out", blend,
        ])
        _run("export_glb.py", ["--in", blend, "--obj", "Top", "--out", glb])
        os.makedirs(os.path.dirname(web), exist_ok=True)
        shutil.copyfile(glb, web)
        print(f"SAVED {web}")

    print(f"DONE {g}: {len(tee['sizes'])} sizes")


if __name__ == "__main__":
    main()
