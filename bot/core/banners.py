"""Generated images: welcome banner, balance card, leaderboard.

Reuses the font/shape helpers from :mod:`core.rankcard`. All rendering runs in
a worker thread (PIL is blocking). Callers wrap in try/except and fall back to
embeds on failure.
"""
from __future__ import annotations

import asyncio
import io
from typing import Optional

from PIL import Image, ImageDraw

from core.rankcard import (
    _FONT_BOLD_CANDIDATES,
    _FONT_CANDIDATES,
    _circle,
    _font,
    _rounded,
)

BG = (24, 26, 31)
CARD = (35, 38, 45)
TEXT = (236, 238, 242)
MUTED = (150, 156, 165)
TRACK = (54, 58, 66)
GOLD = (240, 196, 90)
SILVER = (190, 196, 204)
BRONZE = (205, 140, 95)


def _avatar_or_circle(draw, img, avatar_bytes, x, y, size, ring) -> None:
    draw.ellipse((x - 4, y - 4, x + size + 4, y + size + 4), fill=ring)
    if avatar_bytes:
        try:
            av = _circle(Image.open(io.BytesIO(avatar_bytes)), size)
            img.paste(av, (x, y), av)
            return
        except Exception:
            pass
    draw.ellipse((x, y, x + size, y + size), fill=TRACK)


# ---- welcome banner -------------------------------------------------------


def _welcome(avatar_bytes, name, member_no, guild_name, accent) -> bytes:
    W, H = 1000, 400
    img = Image.new("RGBA", (W, H), BG)
    draw = ImageDraw.Draw(img)
    # accent glow strip
    _rounded(draw, (0, 0, W, 8), 0, accent)
    _rounded(draw, (24, 24, W - 24, H - 24), 28, CARD)

    f_big = _font(_FONT_BOLD_CANDIDATES, 52)
    f_name = _font(_FONT_BOLD_CANDIDATES, 40)
    f_small = _font(_FONT_CANDIDATES, 28)

    av = 150
    ax, ay = (W - av) // 2, 34
    _avatar_or_circle(draw, img, avatar_bytes, ax, ay, av, accent)

    def center(text, font, y, fill):
        w = draw.textlength(text, font=font)
        draw.text(((W - w) / 2, y), text, font=font, fill=fill)

    center("Добро пожаловать!", f_big, ay + av + 22, TEXT)
    disp = name if len(name) <= 24 else name[:23] + "…"
    center(disp, f_name, ay + av + 86, accent)
    center(f"Ты {member_no}-й участник {guild_name}"[:60], f_small, ay + av + 140, MUTED)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "PNG")
    return buf.getvalue()


async def welcome(avatar_bytes, name, member_no, guild_name, accent) -> bytes:
    return await asyncio.to_thread(_welcome, avatar_bytes, name, member_no, guild_name, accent)


# ---- balance card ---------------------------------------------------------


def _num(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def _balance(avatar_bytes, name, wallet, bank, rank, currency, accent) -> bytes:
    W, H = 900, 300
    img = Image.new("RGBA", (W, H), BG)
    draw = ImageDraw.Draw(img)
    _rounded(draw, (20, 20, W - 20, H - 20), 26, CARD)

    f_name = _font(_FONT_BOLD_CANDIDATES, 40)
    f_lbl = _font(_FONT_CANDIDATES, 26)
    f_val = _font(_FONT_BOLD_CANDIDATES, 38)
    f_small = _font(_FONT_CANDIDATES, 24)

    av = 140
    ax, ay = 50, 50
    _avatar_or_circle(draw, img, avatar_bytes, ax, ay, av, accent)
    draw.text((ax, ay + av + 14), f"#{rank} в топе", font=f_small, fill=MUTED)

    tx = ax + av + 45
    disp = name if len(name) <= 18 else name[:17] + "…"
    draw.text((tx, 45), disp, font=f_name, fill=TEXT)

    def money(label, value, y, color):
        draw.text((tx, y), label, font=f_lbl, fill=MUTED)
        # gold coin glyph drawn as a circle (PIL can't render colour emoji)
        draw.text((tx, y + 30), _num(value), font=f_val, fill=color)
        vx = tx + draw.textlength(_num(value), font=f_val) + 14
        draw.ellipse((vx, y + 38, vx + 26, y + 64), fill=GOLD, outline=(150, 120, 40), width=2)

    money("Кошелёк", wallet, 115, accent)
    money("Банк", bank, 195, (120, 200, 140))
    total = wallet + bank
    tot = f"Всего: {_num(total)} {currency}"
    draw.text((W - 50 - draw.textlength(tot, font=f_small), 60), tot, font=f_small, fill=MUTED)

    buf = io.BytesIO()
    img.convert("RGB").save(buf, "PNG")
    return buf.getvalue()


async def balance(avatar_bytes, name, wallet, bank, rank, currency, accent) -> bytes:
    return await asyncio.to_thread(_balance, avatar_bytes, name, wallet, bank, rank, currency, accent)


# ---- leaderboard ----------------------------------------------------------


def _leaderboard(title, subtitle, entries, accent) -> bytes:
    """entries: list of (rank, name, value_str, avatar_bytes|None)."""
    rows = entries[:10]
    row_h = 76
    W = 900
    H = 130 + row_h * max(1, len(rows)) + 30
    img = Image.new("RGBA", (W, H), BG)
    draw = ImageDraw.Draw(img)

    f_title = _font(_FONT_BOLD_CANDIDATES, 42)
    f_sub = _font(_FONT_CANDIDATES, 26)
    f_rank = _font(_FONT_BOLD_CANDIDATES, 30)
    f_name = _font(_FONT_BOLD_CANDIDATES, 30)
    f_val = _font(_FONT_CANDIDATES, 28)

    draw.text((40, 36), title, font=f_title, fill=TEXT)
    if subtitle:
        draw.text((40, 86), subtitle, font=f_sub, fill=MUTED)

    medals = {1: GOLD, 2: SILVER, 3: BRONZE}
    y = 130
    for rank, name, value, av_bytes in rows:
        _rounded(draw, (30, y, W - 30, y + row_h - 12), 18, CARD)
        color = medals.get(rank, MUTED)
        draw.text((52, y + 18), f"#{rank}", font=f_rank, fill=color)
        asize = 48
        _avatar_or_circle(draw, img, av_bytes, 120, y + 7, asize, color)
        disp = name if len(name) <= 22 else name[:21] + "…"
        draw.text((190, y + 18), disp, font=f_name, fill=TEXT)
        draw.text((W - 60 - draw.textlength(value, font=f_val), y + 20), value, font=f_val, fill=accent)
        y += row_h

    buf = io.BytesIO()
    img.convert("RGB").save(buf, "PNG")
    return buf.getvalue()


async def leaderboard(title, subtitle, entries, accent) -> bytes:
    return await asyncio.to_thread(_leaderboard, title, subtitle, entries, accent)


# ---- profile banner (avatar over a background) ----------------------------


def _profile_banner(avatar_bytes, bg_bytes, accent) -> bytes:
    W, H = 900, 320
    img = Image.new("RGBA", (W, H), BG)
    if bg_bytes:
        try:
            bg = Image.open(io.BytesIO(bg_bytes)).convert("RGBA")
            # cover-fit
            scale = max(W / bg.width, H / bg.height)
            bg = bg.resize((int(bg.width * scale), int(bg.height * scale)))
            bg = bg.crop((0, 0, W, H))
            img.paste(bg, (0, 0))
            shade = Image.new("RGBA", (W, H), (0, 0, 0, 90))
            img = Image.alpha_composite(img, shade)
        except Exception:
            for y in range(H):
                t = y / H
                c = tuple(int(BG[i] + (accent[i] - BG[i]) * t * 0.35) for i in range(3))
                ImageDraw.Draw(img).line([(0, y), (W, y)], fill=c)
    else:
        for y in range(H):
            t = y / H
            c = tuple(int(BG[i] + (accent[i] - BG[i]) * t * 0.35) for i in range(3))
            ImageDraw.Draw(img).line([(0, y), (W, y)], fill=c)

    draw = ImageDraw.Draw(img)
    av = 190
    ax, ay = 60, (H - av) // 2
    _avatar_or_circle(draw, img, avatar_bytes, ax, ay, av, accent)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "PNG")
    return buf.getvalue()


async def profile_banner(avatar_bytes, bg_bytes, accent) -> bytes:
    return await asyncio.to_thread(_profile_banner, avatar_bytes, bg_bytes, accent)
