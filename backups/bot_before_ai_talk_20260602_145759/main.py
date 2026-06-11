"""Discord bot entry point for BDM Guild Karte Tool."""

from __future__ import annotations

import logging
import os
import sys

import discord
from discord.ext import commands
from dotenv import load_dotenv

from bot.config import CARDS_DIR, ENV_PATH, GUILD_ID_ENV_NAME, TOKEN_ENV_NAME

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

COG_EXTENSIONS = (
    "bot.cogs.karte",
    "bot.cogs.ogre_timer",
    "bot.cogs.admin",
    "bot.cogs.ai_talk",
)


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


class KarteDiscordBot(commands.Bot):
    """Discord bot that loads feature Cogs and synchronizes slash commands."""

    def __init__(self) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self) -> None:
        for extension in COG_EXTENSIONS:
            await self.load_extension(extension)
            logger.info("Cog を読み込みました: %s", extension)

        guild_id = os.getenv(GUILD_ID_ENV_NAME)
        if guild_id:
            guild = discord.Object(id=int(guild_id))
            self.tree.clear_commands(guild=guild)
            cleared = await self.tree.sync(guild=guild)
            logger.info(
                "ギルド %s の専用スラッシュコマンドをクリアしました: %s 件",
                guild_id,
                len(cleared),
            )
            logger.info("全サーバー対応のため、グローバルスラッシュコマンドとして同期します")

        synced = await self.tree.sync()
        logger.info("%s 件のグローバルスラッシュコマンドを同期しました", len(synced))

    async def on_ready(self) -> None:
        logger.info("ログインしました: %s", self.user)


def main() -> None:
    """Start the Discord bot."""
    try:
        token = load_bot_token()
    except RuntimeError as exc:
        logger.error(str(exc))
        sys.exit(1)

    logger.info("Discord Bot を起動します")
    logger.info("output/cards を参照します: %s", CARDS_DIR)
    bot = KarteDiscordBot()
    bot.run(token)


if __name__ == "__main__":
    main()
