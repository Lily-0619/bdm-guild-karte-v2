"""AI conversation commands backed by Ollama."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime, timezone
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from bot.services.ollama_service import (
    OllamaConnectionError,
    OllamaModelNotFoundError,
    chat_with_ollama,
)

logger = logging.getLogger(__name__)

DISCORD_REPLY_LIMIT = 1900
MAX_HISTORY_TURNS = 10
NORMAL_DAILY_LIMIT = 20
ADMIN_DAILY_LIMIT = 100
NORMAL_COOLDOWN_SECONDS = 15
OLLAMA_CONNECTION_ERROR_MESSAGE = "Ollamaに接続できません。Ollamaが起動しているか確認してください。"
OLLAMA_MODEL_ERROR_MESSAGE = "指定モデルが見つかりません。OLLAMA_MODELを確認してください。"
TALK_ERROR_MESSAGE = "AI会話コマンドの処理中にエラーが発生しました。"
DAILY_LIMIT_MESSAGE = "今日のAIトーク回数上限に達しました。また明日使ってください。"
TALK_MODE_CHAT = "chat"
TALK_MODE_TRANSLATE = "translate"
DEFAULT_LANGUAGE_CODE = "JP"
LANGUAGE_ALIASES = {"JA": "JP"}
MODE_CHOICES = [
    app_commands.Choice(name="chat", value=TALK_MODE_CHAT),
    app_commands.Choice(name="translate", value=TALK_MODE_TRANSLATE),
]
LANGUAGE_SETTINGS = {
    "JP": {"label": "日本語", "heading": "【JP】"},
    "EN": {"label": "English", "heading": "【English】"},
    "FR": {"label": "Français", "heading": "【Français】"},
    "ZH": {"label": "中文", "heading": "【中文】"},
    "RU": {"label": "Русский", "heading": "【Русский】"},
    "ES": {"label": "Español", "heading": "【Español】"},
    "AR": {"label": "العربية", "heading": "【العربية】"},
}
LANGUAGE_CHOICES = [
    app_commands.Choice(name=f"{code} {settings['label']}", value=code)
    for code, settings in LANGUAGE_SETTINGS.items()
]


class AITalkCog(commands.Cog):
    """Commands for AI conversation features."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.conversation_history: dict[int, list[dict[str, str]]] = {}
        self.conversation_summaries: dict[int, str] = {}
        self.daily_usage: dict[int, dict[str, Any]] = {}
        self.last_talk_at: dict[int, datetime] = {}

    @app_commands.command(name="talk", description="AIと会話するためのコマンドです。")
    @app_commands.describe(
        message="ユーザーがAIに話しかける内容",
        mode="chat: 会話 / translate: 日本語翻訳",
        language="chat modeで使う返答言語",
    )
    @app_commands.choices(mode=MODE_CHOICES, language=LANGUAGE_CHOICES)
    async def talk(
        self,
        interaction: discord.Interaction,
        message: str,
        mode: str = TALK_MODE_CHAT,
        language: str = DEFAULT_LANGUAGE_CODE,
    ) -> None:
        """Send a message to Ollama and return its response."""
        normalized_mode = normalize_mode(mode)
        normalized_language = normalize_language(language)
        logger.info(
            "/talk requested: user=%s mode=%s language=%s message=%s",
            interaction.user,
            normalized_mode,
            normalized_language,
            message,
        )
        user_id = interaction.user.id
        is_admin = is_administrator(interaction.user)

        limit_message = self.check_talk_limit(user_id, is_admin)
        if limit_message:
            await interaction.response.send_message(limit_message, ephemeral=True)
            return

        try:
            await interaction.response.defer(thinking=True)
            self.consume_talk_usage(user_id)
            history = self.build_prompt_history(user_id, normalized_mode, normalized_language)
            reply = await asyncio.to_thread(chat_with_ollama, message, history)
            if normalized_mode == TALK_MODE_TRANSLATE:
                compacted_reply = build_translation_reply(message, reply)
            else:
                compacted_reply = compact_reply(reply)
                self.remember_turn(user_id, message, compacted_reply)
            await interaction.followup.send(compacted_reply)
        except OllamaConnectionError:
            self.restore_talk_usage(user_id)
            logger.exception("Ollamaに接続できません")
            await send_talk_error(interaction, OLLAMA_CONNECTION_ERROR_MESSAGE)
        except OllamaModelNotFoundError:
            self.restore_talk_usage(user_id)
            logger.exception("Ollamaの指定モデルが見つかりません")
            await send_talk_error(interaction, OLLAMA_MODEL_ERROR_MESSAGE)
        except Exception:
            self.restore_talk_usage(user_id)
            logger.exception("/talk の処理中にエラーが発生しました")
            await send_talk_error(interaction, TALK_ERROR_MESSAGE)

    def check_talk_limit(self, user_id: int, is_admin: bool) -> str | None:
        """Return an ephemeral error message if the user cannot use /talk now."""
        today = date.today()
        usage = self.daily_usage.get(user_id)
        if usage is None or usage.get("date") != today:
            usage = {"date": today, "count": 0}
            self.daily_usage[user_id] = usage

        daily_limit = ADMIN_DAILY_LIMIT if is_admin else NORMAL_DAILY_LIMIT
        if usage["count"] >= daily_limit:
            return DAILY_LIMIT_MESSAGE

        if not is_admin:
            last_used_at = self.last_talk_at.get(user_id)
            if last_used_at is not None:
                elapsed_seconds = (datetime.now(timezone.utc) - last_used_at).total_seconds()
                remaining_seconds = int(NORMAL_COOLDOWN_SECONDS - elapsed_seconds)
                if remaining_seconds > 0:
                    return f"AIトークは少し間隔を空けて使ってください。あと{remaining_seconds}秒です。"

        return None

    def consume_talk_usage(self, user_id: int) -> None:
        """Consume one /talk use immediately before calling Ollama."""
        today = date.today()
        usage = self.daily_usage.get(user_id)
        if usage is None or usage.get("date") != today:
            usage = {"date": today, "count": 0}
            self.daily_usage[user_id] = usage

        usage["count"] += 1
        self.last_talk_at[user_id] = datetime.now(timezone.utc)

    def restore_talk_usage(self, user_id: int) -> None:
        """Best-effort rollback when Ollama fails after consuming a /talk use."""
        usage = self.daily_usage.get(user_id)
        if usage and usage.get("date") == date.today() and usage.get("count", 0) > 0:
            usage["count"] -= 1

    def build_ollama_history(self, user_id: int) -> list[dict[str, str]]:
        """Build short conversation context for Ollama."""
        messages: list[dict[str, str]] = []
        summary = self.conversation_summaries.get(user_id)
        if summary:
            messages.append(
                {
                    "role": "system",
                    "content": f"これまでの会話の要約です。必要な場合だけ参考にしてください。{summary}",
                }
            )
        messages.extend(self.conversation_history.get(user_id, []))
        return messages

    def build_prompt_history(self, user_id: int, mode: str, language: str) -> list[dict[str, str]]:
        """Build conversation context plus mode-specific system instructions."""
        history = self.build_ollama_history(user_id)
        if mode == TALK_MODE_TRANSLATE:
            history.append({"role": "system", "content": build_translation_instruction()})
        else:
            history.append({"role": "system", "content": build_language_chat_instruction(language)})
        return history

    def remember_turn(self, user_id: int, user_message: str, assistant_reply: str) -> None:
        """Store one user/assistant turn and compact old messages."""
        history = self.conversation_history.setdefault(user_id, [])
        history.append({"role": "user", "content": user_message})
        history.append({"role": "assistant", "content": assistant_reply})
        self.summarize_old_messages(user_id)

    def summarize_old_messages(self, user_id: int) -> None:
        """Keep only recent turns and preserve older context as a simple text summary."""
        history = self.conversation_history.get(user_id, [])
        max_messages = MAX_HISTORY_TURNS * 2
        if len(history) <= max_messages:
            return

        old_messages = history[:-max_messages]
        self.conversation_history[user_id] = history[-max_messages:]
        old_summary = self.conversation_summaries.get(user_id, "")
        additions = []
        for message in old_messages:
            role_label = "ユーザー" if message["role"] == "user" else "Mぼっと"
            additions.append(f"{role_label}: {message['content']}")
        combined = "\n".join(part for part in (old_summary, "\n".join(additions)) if part)
        self.conversation_summaries[user_id] = combined[-1000:]


def normalize_mode(mode: str | None) -> str:
    """Normalize a /talk mode option to a supported value."""
    if mode == TALK_MODE_TRANSLATE:
        return TALK_MODE_TRANSLATE
    return TALK_MODE_CHAT


def normalize_language(language: str | None) -> str:
    """Normalize a /talk language option to a supported language code."""
    if not language:
        return DEFAULT_LANGUAGE_CODE
    normalized = LANGUAGE_ALIASES.get(language.upper(), language.upper())
    if normalized in LANGUAGE_SETTINGS:
        return normalized
    return DEFAULT_LANGUAGE_CODE


def build_language_chat_instruction(language: str) -> str:
    """Build extra system instructions for chat mode language output."""
    language = normalize_language(language)
    if language == DEFAULT_LANGUAGE_CODE:
        return (
            "mode=chat、language=JPです。日本語だけで自然に返答してください。"
            "不要な英語混じりは避け、短めで親しみやすく返してください。"
        )

    settings = LANGUAGE_SETTINGS[language]
    return (
        f"mode=chat、language={language}です。languageは入力言語ではなくtarget response language（返答言語）です。"
        "ユーザー入力が日本語・英語・中国語など何語でも、指定された返答言語で会話として自然に返答してください。"
        "翻訳だけではなく、相手の発言に対する会話返答にしてください。\n\n"
        "必ず以下の3段構成だけで返答してください。\n\n"
        f"{settings['heading']}\n"
        f"{settings['label']}だけで、ユーザーの発言に対する自然な会話返答を書いてください。"
        "この欄には日本語・中国語・他の外国語・thinkingなど不要な語を混ぜないでください。\n\n"
        "【JP】\n"
        "上の指定言語返答の自然な日本語訳だけを書いてください。\n\n"
        "【補足】\n"
        "指定言語の表現・文法・ニュアンスを短く説明してください。"
        "不要なら「補足は特にありません。」と書いてください。\n\n"
        "例: ユーザー入力が「ちょこすきだ」で language=EN の場合\n"
        "【English】\n"
        "Oh, you like chocolate? That’s nice. Do you prefer milk chocolate or dark chocolate?\n\n"
        "【JP】\n"
        "チョコが好きなんだね。いいね。ミルクチョコとダークチョコなら、どっちが好き？\n\n"
        "【補足】\n"
        "“Do you prefer A or B?” は「AとBならどちらが好き？」という自然な聞き方です。"
    )


def build_translation_instruction() -> str:
    """Build extra system instructions for translate mode."""
    return (
        "mode=translateです。海外ギルドメンバーやゲームチャットの文章を日本語へ翻訳してください。"
        "元言語はAIが判断し、黒い砂漠モバイル、ギルド、拠点戦の文脈を考慮してください。"
        "固有名詞、ギルド名、キャラクター名、クラス名、CPM、FCP、スキル名、ゲーム用語は無理に翻訳しないでください。"
        "原文の意味を勝手に足さず、正確さと自然さを優先してください。"
        "Mぼっとの雑談口調は強く出しすぎず、冷たすぎない程度に柔らかくしてください。"
        "【OR】欄や原文の再掲は絶対に書かないでください。原文表示はPython側で追加します。\n\n"
        "必ず以下の形式だけで返答してください。\n\n"
        "【JP】\n"
        "自然な日本語訳。\n\n"
        "【説明】\n"
        "推定した元言語、文法・ニュアンス・ゲーム文脈の補足を短く書いてください。"
        "必要ない場合は「特に補足はありません。」と書いてください。"
    )


def build_translation_reply(source_message: str, ai_reply: str) -> str:
    """Compose the final translation reply with the original text controlled by Python."""
    jp_text = extract_marked_section(ai_reply, "JP")
    explanation = extract_marked_section(ai_reply, "説明")
    if not jp_text:
        jp_text = strip_marked_section(ai_reply, "OR").strip()
    if not jp_text:
        jp_text = "翻訳結果を取得できませんでした。"
    if not explanation:
        explanation = "特に補足はありません。"

    return compact_reply(
        f"【OR】\n{source_message}\n\n"
        f"【JP】\n{jp_text}\n\n"
        f"【説明】\n{explanation}"
    )


def extract_marked_section(text: str, heading: str) -> str:
    """Extract a Japanese bracket heading section from an AI reply."""
    pattern = rf"【{re.escape(heading)}】\s*(.*?)(?=\n\s*【[^】]+】|\Z)"
    match = re.search(pattern, text, flags=re.DOTALL)
    if not match:
        return ""
    return match.group(1).strip()


def strip_marked_section(text: str, heading: str) -> str:
    """Remove a Japanese bracket heading section from an AI reply."""
    pattern = rf"【{re.escape(heading)}】\s*.*?(?=\n\s*【[^】]+】|\Z)"
    return re.sub(pattern, "", text, flags=re.DOTALL).strip()


async def setup(bot: commands.Bot) -> None:
    """Load the AI talk cog."""
    await bot.add_cog(AITalkCog(bot))


def compact_reply(reply: str) -> str:
    """Keep an Ollama reply within a safe Discord message length."""
    if len(reply) <= DISCORD_REPLY_LIMIT:
        return reply

    suffix = "...（省略）"
    return reply[: DISCORD_REPLY_LIMIT - len(suffix)] + suffix


def truncate_reply(reply: str) -> str:
    """Backward-compatible alias for compact_reply."""
    return compact_reply(reply)


def is_administrator(user: discord.abc.User) -> bool:
    """Return whether an interaction user has Discord administrator permission."""
    guild_permissions = getattr(user, "guild_permissions", None)
    return bool(guild_permissions and guild_permissions.administrator)


async def send_talk_error(interaction: discord.Interaction, message: str) -> None:
    """Send a talk command error message whether or not the interaction was deferred."""
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
        return
    await interaction.response.send_message(message, ephemeral=True)
