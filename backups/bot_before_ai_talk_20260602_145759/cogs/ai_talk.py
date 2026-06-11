"""AI conversation commands backed by Ollama."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict, deque
from typing import DefaultDict, Deque, Dict, Tuple

import discord
from discord import app_commands
from discord.ext import commands

from bot.services.ollama_service import (
    OllamaConnectionError,
    OllamaModelNotFoundError,
    OllamaResponseError,
    chat_with_ollama,
)

logger = logging.getLogger(__name__)

DISCORD_REPLY_LIMIT = 1900
SUMMARY_REPLY_LIMIT = 1200
MAX_HISTORY_TURNS = 5
MAX_HISTORY_MESSAGES = MAX_HISTORY_TURNS * 2
OLLAMA_CONNECTION_ERROR_MESSAGE = "Ollamaに接続できません。Ollamaが起動しているか確認してください。"
OLLAMA_MODEL_ERROR_MESSAGE = "指定モデルが見つかりません。OLLAMA_MODELを確認してください。"
TALK_ERROR_MESSAGE = "AI会話コマンドの処理中にエラーが発生しました。少し時間を置いて再試行してください。"

HistoryMessage = Dict[str, str]
HistoryKey = Tuple[int, int]


class AITalkCog(commands.Cog):
    """Commands for AI conversation features."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.conversation_history: DefaultDict[HistoryKey, Deque[HistoryMessage]] = defaultdict(
            lambda: deque(maxlen=MAX_HISTORY_MESSAGES)
        )

    @app_commands.command(name="talk", description="AIと会話します")
    @app_commands.describe(message="AIに話しかける内容")
    async def talk(self, interaction: discord.Interaction, message: str) -> None:
        """Send a message to Ollama and return its response."""
        logger.info(
            "/talk requested: user_id=%s channel_id=%s message_length=%s",
            interaction.user.id,
            interaction.channel_id,
            len(message),
        )
        try:
            await interaction.response.defer(thinking=True)
            history_key = self.get_history_key(interaction)
            history = list(self.conversation_history[history_key])
            reply = await asyncio.to_thread(chat_with_ollama, message, history)
            safe_reply = compact_reply(reply)
            self.remember_turn(history_key, message, safe_reply)
            await interaction.followup.send(safe_reply)
        except OllamaConnectionError:
            logger.exception("Ollamaに接続できません")
            await send_talk_error(interaction, OLLAMA_CONNECTION_ERROR_MESSAGE)
        except OllamaModelNotFoundError:
            logger.exception("Ollamaの指定モデルが見つかりません")
            await send_talk_error(interaction, OLLAMA_MODEL_ERROR_MESSAGE)
        except OllamaResponseError:
            logger.exception("Ollamaがエラーを返しました")
            await send_talk_error(interaction, TALK_ERROR_MESSAGE)
        except Exception:
            logger.exception("/talk の処理中にエラーが発生しました")
            await send_talk_error(interaction, TALK_ERROR_MESSAGE)

    def get_history_key(self, interaction: discord.Interaction) -> HistoryKey:
        """Return a per-user and per-channel key for in-memory conversation history."""
        channel_id = interaction.channel_id or 0
        return interaction.user.id, channel_id

    def remember_turn(self, history_key: HistoryKey, user_message: str, assistant_reply: str) -> None:
        """Store the latest user/assistant turn and automatically evict old turns."""
        history = self.conversation_history[history_key]
        history.append({"role": "user", "content": user_message})
        history.append({"role": "assistant", "content": assistant_reply})


async def setup(bot: commands.Bot) -> None:
    """Load the AI talk cog."""
    await bot.add_cog(AITalkCog(bot))


def compact_reply(reply: str) -> str:
    """Keep an Ollama reply short enough for Discord by locally summarizing long output."""
    normalized_reply = "\n".join(line.rstrip() for line in reply.strip().splitlines()).strip()
    if len(normalized_reply) <= DISCORD_REPLY_LIMIT:
        return normalized_reply

    target_length = min(SUMMARY_REPLY_LIMIT, DISCORD_REPLY_LIMIT)
    suffix = "\n\n（長すぎたので要点だけに短縮しました。続きが必要なら『続き』と聞いてください。）"
    shortened = normalized_reply[: target_length - len(suffix)].rstrip()
    return shortened + suffix


async def send_talk_error(interaction: discord.Interaction, message: str) -> None:
    """Send a talk command error message whether or not the interaction was deferred."""
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
        return
    await interaction.response.send_message(message, ephemeral=True)
