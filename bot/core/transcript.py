"""Build an HTML transcript of a ticket channel's message history."""
from __future__ import annotations

import html
import io
from datetime import datetime, timezone

import discord

_HTML_HEAD = """<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8">
<title>Транскрипт — {title}</title>
<style>
  body {{ background:#313338; color:#dbdee1; font-family:'gg sans',Arial,sans-serif;
         margin:0; padding:24px; }}
  .header {{ border-bottom:1px solid #3f4147; padding-bottom:12px; margin-bottom:16px; }}
  .header h1 {{ font-size:20px; margin:0 0 4px; color:#fff; }}
  .meta {{ color:#949ba4; font-size:13px; }}
  .msg {{ display:flex; padding:8px 0; border-bottom:1px solid #2b2d31; }}
  .avatar {{ width:40px; height:40px; border-radius:50%; margin-right:14px;
            background:#5865f2; flex:0 0 40px; display:flex; align-items:center;
            justify-content:center; color:#fff; font-weight:600; }}
  .body {{ flex:1; min-width:0; }}
  .author {{ color:#fff; font-weight:600; }}
  .ts {{ color:#949ba4; font-size:12px; margin-left:8px; }}
  .content {{ white-space:pre-wrap; word-wrap:break-word; margin-top:2px; }}
  .att {{ color:#00a8fc; font-size:13px; margin-top:4px; }}
  .embed {{ border-left:4px solid #5865f2; background:#2b2d31; padding:6px 10px;
           margin-top:6px; border-radius:4px; font-size:13px; }}
  .system {{ color:#949ba4; font-style:italic; }}
</style></head><body>
<div class="header"><h1>{title}</h1>
<div class="meta">{meta}</div></div>
"""

_HTML_TAIL = "</body></html>"


def _fmt_ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _initial(name: str) -> str:
    return html.escape(name[:1].upper() or "?")


async def build_html(
    channel: discord.TextChannel,
    *,
    title: str,
    meta: str,
    limit: int = 2000,
) -> tuple[str, bytes]:
    """Render channel history (oldest→newest) into an HTML transcript.

    Returns ``(filename, data)`` so the caller can build as many
    ``discord.File`` objects as needed (each File is single-use).
    """
    parts: list[str] = [_HTML_HEAD.format(title=html.escape(title), meta=html.escape(meta))]

    async for message in channel.history(limit=limit, oldest_first=True):
        author = message.author
        name = html.escape(author.display_name)
        ts = _fmt_ts(message.created_at)
        content = html.escape(message.content) if message.content else ""

        parts.append('<div class="msg">')
        parts.append(f'<div class="avatar">{_initial(author.display_name)}</div>')
        parts.append('<div class="body">')
        parts.append(f'<span class="author">{name}</span><span class="ts">{ts}</span>')
        if content:
            parts.append(f'<div class="content">{content}</div>')
        for att in message.attachments:
            parts.append(
                f'<div class="att">📎 <a href="{html.escape(att.url)}">'
                f"{html.escape(att.filename)}</a></div>"
            )
        for embed in message.embeds:
            label = embed.title or embed.description or "вложение embed"
            parts.append(f'<div class="embed">{html.escape(str(label))[:300]}</div>')
        parts.append("</div></div>")

    parts.append(_HTML_TAIL)
    data = "".join(parts).encode("utf-8")
    filename = f"transcript-{channel.name}.html"
    return filename, data


def to_file(filename: str, data: bytes) -> discord.File:
    """Wrap transcript bytes in a fresh single-use ``discord.File``."""
    return discord.File(io.BytesIO(data), filename=filename)
