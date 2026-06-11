"""Slash commands for posting generated BDM Guild Karte PNG files."""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import CARDS_DIR
from bot.utils.file_finder import (
    find_guild_directory,
    find_latest_png,
    format_guild_list,
    get_guild_directories,
    guild_not_found_message,
)

logger = logging.getLogger(__name__)


class KarteCog(commands.Cog):
    """Commands for listing guilds and sending existing card PNG files."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="guilds", description="カルテPNGが作成済みのギルド一覧を表示します。")
    async def guilds(self, interaction: discord.Interaction) -> None:
        """List guild folders under output/cards."""
        logger.info("/guilds が実行されました: user=%s", interaction.user)

        if not CARDS_DIR.exists() or not CARDS_DIR.is_dir():
            await interaction.response.send_message("カルテ出力フォルダがありません。")
            return

        await interaction.response.send_message(format_guild_list(get_guild_directories()))

    async def send_latest_png(self, interaction: discord.Interaction, guild_name: str, prefix: str, label: str) -> None:
        """Send the newest matching PNG for a guild."""
        logger.info("/%s が実行されました: guild_name=%s user=%s", prefix, guild_name, interaction.user)

        guild_dir = find_guild_directory(guild_name)
        if guild_dir is None:
            await interaction.response.send_message(guild_not_found_message())
            return

        png_path = find_latest_png(guild_dir, prefix)
        if png_path is None:
            await interaction.response.send_message(f"このギルドの{label}PNGが見つかりません。")
            return

        await interaction.response.defer(thinking=True)
        await interaction.followup.send(
            content=f"{guild_name} の最新{label}PNGです。",
            file=discord.File(png_path),
        )

    async def send_both_pngs(self, interaction: discord.Interaction, guild_name: str) -> None:
        """Send the newest karte and members PNGs for a guild in one message."""
        logger.info("/both requested guild=%s user=%s", guild_name, interaction.user)

        guild_dir = find_guild_directory(guild_name)
        if guild_dir is None:
            await interaction.response.send_message(guild_not_found_message())
            return

        karte_path = find_latest_png(guild_dir, "karte")
        members_path = find_latest_png(guild_dir, "members")
        missing_messages = []
        if karte_path is None:
            missing_messages.append("karte PNG が見つかりません。")
        if members_path is None:
            missing_messages.append("members PNG が見つかりません。")

        if missing_messages:
            await interaction.response.send_message("\n".join(missing_messages))
            return

        logger.info("sending two files for guild=%s", guild_name)
        await interaction.response.defer(thinking=True)
        await interaction.followup.send(
            content=f"{guild_name} の最新カルテPNGと最新メンバー一覧PNGです。",
            files=[
                discord.File(karte_path),
                discord.File(members_path),
            ],
        )

    @app_commands.command(name="karte", description="指定ギルドの最新カルテPNGを投稿します。")
    @app_commands.describe(guild_name="output/cards 配下のギルドフォルダ名（完全一致）")
    async def karte(self, interaction: discord.Interaction, guild_name: str) -> None:
        """Post the newest karte PNG for an exact guild name."""
        await self.send_latest_png(interaction, guild_name, "karte", "カルテ")

    @app_commands.command(name="members", description="指定ギルドの最新メンバー一覧PNGを投稿します。")
    @app_commands.describe(guild_name="output/cards 配下のギルドフォルダ名（完全一致）")
    async def members(self, interaction: discord.Interaction, guild_name: str) -> None:
        """Post the newest members PNG for an exact guild name."""
        await self.send_latest_png(interaction, guild_name, "members", "メンバー一覧")

    @app_commands.command(name="both", description="指定ギルドの最新カルテPNGとメンバー一覧PNGをまとめて投稿します。")
    @app_commands.describe(guild_name="output/cards 配下のギルドフォルダ名（完全一致）")
    async def both(self, interaction: discord.Interaction, guild_name: str) -> None:
        """Post the newest karte and members PNGs for an exact guild name."""
        await self.send_both_pngs(interaction, guild_name)


async def setup(bot: commands.Bot) -> None:
    """Load the karte cog."""
    await bot.add_cog(KarteCog(bot))
