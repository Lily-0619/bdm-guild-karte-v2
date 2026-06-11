"""Discord bot for posting already-generated BDM Guild Karte PNG files.

This bot intentionally does not scrape DBonk, analyze data, or generate cards.
It only looks for existing PNG files under output/cards and sends them to Discord.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import discord
from discord import app_commands
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
CARDS_DIR = PROJECT_ROOT / "output" / "cards"
TOKEN_ENV_NAME = "DISCORD_BOT_TOKEN"
GUILD_ID_ENV_NAME = "DISCORD_GUILD_ID"

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def load_bot_token() -> str:
    """Load the Discord bot token from .env or the process environment."""
    load_dotenv(ENV_PATH)
    token = os.getenv(TOKEN_ENV_NAME)
    if not token:
        raise RuntimeError(
            f"{TOKEN_ENV_NAME} が .env に設定されていません。"
            f"{ENV_PATH} に {TOKEN_ENV_NAME}=... を追加してください。"
        )
    return token


def get_guild_directories() -> list[Path]:
    """Return card output guild directories sorted by folder name."""
    if not CARDS_DIR.exists() or not CARDS_DIR.is_dir():
        return []
    return sorted(
        (path for path in CARDS_DIR.iterdir() if path.is_dir()),
        key=lambda path: path.name.casefold(),
    )


def format_guild_list(guild_dirs: list[Path]) -> str:
    """Format guild directory names for Discord replies."""
    if not guild_dirs:
        return "カルテ作成済みギルドはありません。"

    lines = ["カルテ作成済みギルド："]
    lines.extend(path.name for path in guild_dirs)
    message = "\n".join(lines)

    if len(message) <= 1900:
        return message

    shortened = message[:1850].rstrip()
    return f"{shortened}\n...\n（ギルド数が多いため一部のみ表示しています）"


def find_guild_directory(guild_name: str) -> Path | None:
    """Find a guild directory by exact folder-name match."""
    if not CARDS_DIR.exists() or not CARDS_DIR.is_dir():
        return None

    guild_dir = CARDS_DIR / guild_name
    if guild_dir.exists() and guild_dir.is_dir():
        return guild_dir
    return None


def find_latest_png(guild_dir: Path, prefix: str) -> Path | None:
    """Find the newest matching PNG by modification time.

    The guild name may contain characters that are special in glob patterns, so
    filenames are filtered directly instead of interpolating the name into glob.
    """
    guild_name = guild_dir.name
    expected_prefix = f"{prefix}_{guild_name}_"
    candidates = [
        path
        for path in guild_dir.iterdir()
        if path.is_file()
        and path.suffix.lower() == ".png"
        and path.name.startswith(expected_prefix)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def guild_not_found_message() -> str:
    """Build the not-found message with currently available guild candidates."""
    guild_dirs = get_guild_directories()
    candidates = format_guild_list(guild_dirs)
    return f"指定ギルドが見つかりません。\n\n{candidates}"


class KarteDiscordBot(discord.Client):
    """Discord client with slash-command synchronization on startup."""

    def __init__(self) -> None:
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        guild_id = os.getenv(GUILD_ID_ENV_NAME)
        if guild_id:
            guild = discord.Object(id=int(guild_id))
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            logger.info("%s 件のスラッシュコマンドをギルド %s に同期しました", len(synced), guild_id)
            return

        synced = await self.tree.sync()
        logger.info("%s 件のグローバルスラッシュコマンドを同期しました", len(synced))

    async def on_ready(self) -> None:
        logger.info("ログインしました: %s", self.user)


bot = KarteDiscordBot()


@bot.tree.command(name="guilds", description="カルテPNGが作成済みのギルド一覧を表示します。")
async def guilds(interaction: discord.Interaction) -> None:
    """List guild folders under output/cards."""
    logger.info("/guilds が実行されました: user=%s", interaction.user)

    if not CARDS_DIR.exists() or not CARDS_DIR.is_dir():
        await interaction.response.send_message("カルテ出力フォルダがありません。")
        return

    await interaction.response.send_message(format_guild_list(get_guild_directories()))


async def send_latest_png(interaction: discord.Interaction, guild_name: str, prefix: str, label: str) -> None:
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


async def send_both_pngs(interaction: discord.Interaction, guild_name: str) -> None:
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


@bot.tree.command(name="karte", description="指定ギルドの最新カルテPNGを投稿します。")
@app_commands.describe(guild_name="output/cards 配下のギルドフォルダ名（完全一致）")
async def karte(interaction: discord.Interaction, guild_name: str) -> None:
    """Post the newest karte PNG for an exact guild name."""
    await send_latest_png(interaction, guild_name, "karte", "カルテ")


@bot.tree.command(name="members", description="指定ギルドの最新メンバー一覧PNGを投稿します。")
@app_commands.describe(guild_name="output/cards 配下のギルドフォルダ名（完全一致）")
async def members(interaction: discord.Interaction, guild_name: str) -> None:
    """Post the newest members PNG for an exact guild name."""
    await send_latest_png(interaction, guild_name, "members", "メンバー一覧")


@bot.tree.command(name="both", description="指定ギルドの最新カルテPNGとメンバー一覧PNGをまとめて投稿します。")
@app_commands.describe(guild_name="output/cards 配下のギルドフォルダ名（完全一致）")
async def both(interaction: discord.Interaction, guild_name: str) -> None:
    """Post the newest karte and members PNGs for an exact guild name."""
    await send_both_pngs(interaction, guild_name)


def main() -> None:
    """Start the Discord bot."""
    try:
        token = load_bot_token()
    except RuntimeError as exc:
        logger.error(str(exc))
        sys.exit(1)

    logger.info("Discord Bot を起動します")
    logger.info("output/cards を参照します: %s", CARDS_DIR)
    bot.run(token)


if __name__ == "__main__":
    main()
