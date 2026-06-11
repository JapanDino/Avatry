"""Канонический словарь морфов (shape keys в Blender = morph targets в GLB = ключи в коде).

Единый источник правды по именам морфов для всего проекта. Никаких синонимов:
имя здесь == имя shape key в Blender == имя morph target в экспортированном GLB ==
ключ в коде web/. Если морф переименовывается — только здесь, и затем по всему пайплайну.

Имена в snake_case. См. docs/ARCHITECTURE.md → «Словарь морфов».
"""

from __future__ import annotations

# --- Морфы тела ---
# MVP: пять управляющих морфов, которыми мы строим фигуру по меркам.
BODY_MORPHS_MVP: tuple[str, ...] = (
    "height",   # рост
    "weight",   # общая полнота
    "chest",    # обхват груди
    "waist",    # обхват талии
    "hips",     # обхват бёдер
)

# Резерв на будущие этапы (детализация фигуры). Пока не экспортируются.
BODY_MORPHS_RESERVED: tuple[str, ...] = (
    "shoulders",  # ширина плеч
    "belly",      # живот
    "inseam",     # длина внутреннего шва ног
    "neck",       # обхват шеи
    "sleeve_ref", # референс длины руки
)

# --- Морфы одежды (крой) ---
# Применяются поверх морфов тела: одежда следует за фигурой, плюс собственный крой.
GARMENT_MORPHS: tuple[str, ...] = (
    "garment_length",  # длина изделия
    "garment_width",   # ширина/свобода по корпусу
    "sleeve_length",   # длина рукава
    "fit_ease",        # общая свобода кроя (oversize ↔ приталенный)
)

# Полный набор морфов тела, который реально кладётся в GLB на текущем этапе.
BODY_MORPHS_ACTIVE: tuple[str, ...] = BODY_MORPHS_MVP

ALL_KNOWN_MORPHS: frozenset[str] = frozenset(
    BODY_MORPHS_MVP + BODY_MORPHS_RESERVED + GARMENT_MORPHS
)


def is_known_morph(name: str) -> bool:
    """True, если имя морфа есть в каноническом словаре."""
    return name in ALL_KNOWN_MORPHS
