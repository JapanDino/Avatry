"""Giveaway system with persistent entry button and auto-ending."""
from __future__ import annotations

import random
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
from core import embeds
from core.permissions import requires


def parse_duration(text: str) -> Optional[int]:
    raw = text.strip().lower()
    if not raw:
        return None
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
    total = 0
    num = ""
    for ch in raw.replace(" ", ""):
        if ch.isdigit():
            num += ch
            continue
        if ch not in units or not num:
            return None
        total += int(num) * units[ch]
        num = ""
    if num:
        total += int(num)
    return total if total > 0 else None


def build_giveaway_embed(row, entries: int, *, ended: bool = False) -> discord.Embed:
    title = "🎉 Розыгрыш завершён" if ended else "🎉 Розыгрыш"
    embed = discord.Embed(
        title=title,
        description=f"**Приз:** {row['prize']}",
        color=config.SUCCESS_COLOR if not ended else config.EMBED_COLOR,
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Победителей", value=str(row["winners"]), inline=True)
    embed.add_field(name="Участников", value=str(entries), inline=True)
    embed.add_field(
        name="Завершение",
        value=f"<t:{row['ends_at']}:R>" if not ended else f"<t:{row['ends_at']}:f>",
        inline=True,
    )
    embed.add_field(name="Организатор", value=f"<@{row['host_id']}>", inline=True)
    embed.set_footer(text=f"giveaway_id:{row['id']} message_id:{row['message_id'] or 'pending'}")
    return embed


class GiveawayView(discord.ui.View):
    def __init__(self, *, disabled: bool = False) -> None:
        super().__init__(timeout=None)
        self.add_item(GiveawayJoinButton(disabled=disabled))


class GiveawayJoinButton(discord.ui.Button):
    def __init__(self, *, disabled: bool = False) -> None:
        super().__init__(
            label="Участвовать",
            emoji="🎉",
            style=discord.ButtonStyle.success,
            custom_id="giveaway:join",
            disabled=disabled,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.message is None or interaction.guild is None:
            return
        if interaction.user.bot:
            return
        row = await interaction.client.db.get_giveaway_by_message(interaction.message.id)  # type: ignore[attr-defined]
        if row is None:
            await interaction.response.send_message(embed=embeds.error("Розыгрыш не найден."), ephemeral=True)
            return
        if row["ended"] or row["ends_at"] <= int(time.time()):
            await interaction.response.send_message(embed=embeds.warn("Этот розыгрыш уже завершён."), ephemeral=True)
            return
        added = await interaction.client.db.add_giveaway_entry(interaction.message.id, interaction.user.id)  # type: ignore[attr-defined]
        if not added:
            await interaction.response.send_message(embed=embeds.warn("Вы уже участвуете."), ephemeral=True)
            return
        count = await interaction.client.db.giveaway_entry_count(interaction.message.id)  # type: ignore[attr-defined]
        await interaction.response.send_message(embed=embeds.success("Вы участвуете в розыгрыше."), ephemeral=True)
        try:
            await interaction.message.edit(embed=build_giveaway_embed(row, count), view=GiveawayView())
        except discord.HTTPException:
            pass


class Giveaways(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.giveaway_loop.start()

    def cog_unload(self) -> None:
        self.giveaway_loop.cancel()

    giveaway = app_commands.Group(name="giveaway", description="Розыгрыши", guild_only=True)

    @giveaway.command(name="start", description="Запустить розыгрыш")
    @app_commands.describe(
        duration="Срок: 10m, 2h, 1d, 1w",
        winners="Количество победителей",
        prize="Приз",
        channel="Канал розыгрыша, по умолчанию текущий",
    )
    @requires("giveaway_start")
    async def start(
        self,
        interaction: discord.Interaction,
        duration: str,
        winners: app_commands.Range[int, 1, 25],
        prize: str,
        channel: Optional[discord.TextChannel] = None,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            return
        seconds = parse_duration(duration)
        if seconds is None:
            await interaction.response.send_message(
                embed=embeds.error("Срок должен быть вроде `10m`, `2h`, `1d`, `1w`."),
                ephemeral=True,
            )
            return
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message(embed=embeds.error("Нужен текстовый канал."), ephemeral=True)
            return
        giveaway_id = await self.bot.db.create_giveaway(
            guild.id,
            target.id,
            prize[:300],
            int(winners),
            interaction.user.id,
            int(time.time()) + seconds,
        )
        row = await self.bot.db.get_giveaway(giveaway_id)
        msg = await target.send(embed=build_giveaway_embed(row, 0), view=GiveawayView())
        await self.bot.db.set_giveaway_message(giveaway_id, msg.id)
        row = await self.bot.db.get_giveaway(giveaway_id)
        try:
            await msg.edit(embed=build_giveaway_embed(row, 0), view=GiveawayView())
        except discord.HTTPException:
            pass
        await interaction.response.send_message(
            embed=embeds.success(f"Розыгрыш создан в {target.mention}."),
            ephemeral=True,
        )

    @giveaway.command(name="end", description="Завершить розыгрыш по ID сообщения")
    @app_commands.describe(message_id="ID сообщения розыгрыша")
    @requires("giveaway_end")
    async def end(self, interaction: discord.Interaction, message_id: str) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            msg_id = int(message_id)
        except ValueError:
            await interaction.followup.send(embed=embeds.error("ID сообщения должен быть числом."), ephemeral=True)
            return
        row = await self.bot.db.get_giveaway_by_message(msg_id)
        if row is None:
            await interaction.followup.send(embed=embeds.error("Розыгрыш не найден."), ephemeral=True)
            return
        await self._finish(row, forced_by=interaction.user.id)
        await interaction.followup.send(embed=embeds.success("Розыгрыш завершён."), ephemeral=True)

    @giveaway.command(name="reroll", description="Перевыбрать победителей завершённого розыгрыша")
    @app_commands.describe(message_id="ID сообщения розыгрыша")
    @requires("giveaway_reroll")
    async def reroll(self, interaction: discord.Interaction, message_id: str) -> None:
        try:
            msg_id = int(message_id)
        except ValueError:
            await interaction.response.send_message(embed=embeds.error("ID сообщения должен быть числом."), ephemeral=True)
            return
        row = await self.bot.db.get_giveaway_by_message(msg_id)
        if row is None:
            await interaction.response.send_message(embed=embeds.error("Розыгрыш не найден."), ephemeral=True)
            return
        if not row["ended"]:
            await interaction.response.send_message(embed=embeds.warn("Этот розыгрыш ещё не завершён."), ephemeral=True)
            return
        winners = await self._pick_winners(row)
        channel = interaction.guild.get_channel(row["channel_id"]) if interaction.guild else None
        if isinstance(channel, discord.TextChannel):
            await channel.send(
                embed=embeds.success(
                    f"Новые победители розыгрыша **{row['prize']}**: "
                    f"{', '.join(f'<@{uid}>' for uid in winners) if winners else 'нет участников'}"
                )
            )
        await interaction.response.send_message(embed=embeds.success("Reroll выполнен."), ephemeral=True)

    async def _pick_winners(self, row) -> list[int]:
        if row["message_id"] is None:
            return []
        entries = await self.bot.db.giveaway_entries(row["message_id"])
        if not entries:
            return []
        return random.sample(entries, min(int(row["winners"]), len(entries)))

    async def _finish(self, row, *, forced_by: Optional[int] = None) -> None:
        if row["ended"]:
            return
        await self.bot.db.mark_giveaway_ended(row["id"])
        winners = await self._pick_winners(row)
        guild = self.bot.get_guild(row["guild_id"])
        channel = guild.get_channel(row["channel_id"]) if guild else None
        entries_count = await self.bot.db.giveaway_entry_count(row["message_id"]) if row["message_id"] else 0
        if isinstance(channel, discord.TextChannel):
            try:
                message = await channel.fetch_message(row["message_id"])
                ended_row = await self.bot.db.get_giveaway(row["id"])
                await message.edit(
                    embed=build_giveaway_embed(ended_row, entries_count, ended=True),
                    view=GiveawayView(disabled=True),
                )
            except discord.HTTPException:
                pass
            text = (
                f"Победители розыгрыша **{row['prize']}**: "
                f"{', '.join(f'<@{uid}>' for uid in winners)}"
                if winners
                else f"Розыгрыш **{row['prize']}** завершён, но участников не было."
            )
            if forced_by:
                text += f"\nЗавершил: <@{forced_by}>"
            await channel.send(embed=embeds.success(text))

    @tasks.loop(seconds=30)
    async def giveaway_loop(self) -> None:
        await self.bot.wait_until_ready()
        for row in await self.bot.db.giveaways_due():
            await self._finish(row)

    @giveaway_loop.before_loop
    async def before_giveaway_loop(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Giveaways(bot))
