"""Render a simple rank card image (avatar + level + rank + XP bar).

PIL work is CPU-bound and blocking, so ``render`` offloads it to a thread.
Gracefully degrades: if fonts or the avatar can't be loaded, callers fall back
to a plain embed.
"""
from __future__ import annotations

import asyncio
import io
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

# Common Windows font paths (Arial covers Cyrillic). Falls back to PIL default.
_FONT_CANDIDATES = [
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]
_FONT_BOLD_CANDIDATES = [
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]

W, H = 900, 260
BG = (26, 28, 33)
CARD = (35, 38, 45)
TEXT = (235, 237, 240)
MUTED = (150, 156, 165)
TRACK = (54, 58, 66)


def _font(paths, size: int) -> ImageFont.FreeTypeFont:
    for p in paths:
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _circle(avatar: Image.Image, size: int) -> Image.Image:
    avatar = avatar.convert("RGBA").resize((size, size))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(avatar, (0, 0), mask)
    return out


def _rounded(draw: ImageDraw.ImageDraw, box, radius, fill) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill)


def _render(avatar_bytes: Optional[bytes], name: str, level: int, rank: int,
            into: int, need: int, total_xp: int, accent: tuple,
            bg: Optional[tuple] = None) -> bytes:
    bg_color = bg or BG
    card_color = tuple(max(0, min(255, int(c * 1.12))) for c in bg_color[:3])
    track_color = tuple(max(0, min(255, int(c * 1.55))) for c in bg_color[:3])

    img = Image.new("RGBA", (W, H), bg_color)
    draw = ImageDraw.Draw(img)
    _rounded(draw, (20, 20, W - 20, H - 20), 28, card_color)

    f_big = _font(_FONT_BOLD_CANDIDATES, 46)
    f_mid = _font(_FONT_BOLD_CANDIDATES, 32)
    f_small = _font(_FONT_CANDIDATES, 26)
    f_tiny = _font(_FONT_CANDIDATES, 22)

    # Avatar with accent ring.
    av_size = 150
    ax, ay = 55, (H - av_size) // 2
    draw.ellipse((ax - 5, ay - 5, ax + av_size + 5, ay + av_size + 5), fill=accent)
    if avatar_bytes:
        try:
            av = _circle(Image.open(io.BytesIO(avatar_bytes)), av_size)
            img.paste(av, (ax, ay), av)
        except Exception:
            draw.ellipse((ax, ay, ax + av_size, ay + av_size), fill=track_color)
    else:
        draw.ellipse((ax, ay, ax + av_size, ay + av_size), fill=track_color)

    tx = ax + av_size + 40
    # Name (truncate to fit).
    display = name
    while draw.textlength(display, font=f_big) > 420 and len(display) > 3:
        display = display[:-2]
    if display != name:
        display += "…"
    draw.text((tx, 55), display, font=f_big, fill=TEXT)

    # Rank / Level on the right.
    rl = f"LEVEL {level}"
    draw.text((W - 60 - draw.textlength(rl, font=f_mid), 60), rl, font=f_mid, fill=accent)
    rk = f"#{rank}"
    draw.text((W - 60 - draw.textlength(rk, font=f_small), 100), rk, font=f_small, fill=MUTED)

    # XP progress bar.
    bx0, bx1, by = tx, W - 60, 185
    bh = 26
    _rounded(draw, (bx0, by, bx1, by + bh), bh // 2, track_color)
    pct = (into / need) if need else 0
    fill_w = int((bx1 - bx0) * max(0.02, min(1.0, pct)))
    _rounded(draw, (bx0, by, bx0 + fill_w, by + bh), bh // 2, accent)
    draw.text((tx, by - 34), f"{into} / {need} XP", font=f_tiny, fill=MUTED)
    tot = f"Всего: {total_xp} XP"
    draw.text((bx1 - draw.textlength(tot, font=f_tiny), by - 34), tot, font=f_tiny, fill=MUTED)

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


async def render(avatar_bytes: Optional[bytes], name: str, level: int, rank: int,
                 into: int, need: int, total_xp: int, accent: tuple,
                 bg: Optional[tuple] = None) -> bytes:
    return await asyncio.to_thread(
        _render, avatar_bytes, name, level, rank, into, need, total_xp, accent, bg)
