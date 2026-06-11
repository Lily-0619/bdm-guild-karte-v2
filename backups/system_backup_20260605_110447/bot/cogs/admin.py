"""Administrative slash commands for maintaining bot messages."""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger(__name__)

MIN_CLEAN_LIMIT = 1
MAX_CLEAN_LIMIT = 500
DEFAULT_CLEAN_LIMIT = 100


class AdminCog(commands.Cog):
    """Admin commands for cleaning up messages posted by this bot."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="clean_bot_logs",
        description="現在のチャンネル内でBot自身が投稿したメッセージだけを削除します。",
    )
    @app_commands.describe(limit="確認する直近メッセージ数（1〜500）")
    async def clean_bot_logs(self, interaction: discord.Interaction, limit: int = DEFAULT_CLEAN_LIMIT) -> None:
        """Delete this bot's own messages from the current channel history."""
        logger.info(
            "/clean_bot_logs requested: user=%s channel=%s limit=%s",
            interaction.user,
            interaction.channel_id,
            limit,
        )

        if limit < MIN_CLEAN_LIMIT or limit > MAX_CLEAN_LIMIT:
            await interaction.response.send_message(
                f"limit は {MIN_CLEAN_LIMIT} 〜 {MAX_CLEAN_LIMIT} の範囲で指定してください。",
                ephemeral=True,
            )
            return

        if interaction.channel is None or not hasattr(interaction.channel, "history"):
            await interaction.response.send_message(
                "このチャンネルではメッセージ履歴を取得できません。",
                ephemeral=True,
            )
            return

        if self.bot.user is None:
            await interaction.response.send_message(
                "Botユーザー情報を取得できませんでした。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        deleted_count = 0
        failed_count = 0
        permission_error = False

        try:
            async for message in interaction.channel.history(limit=limit):
                if message.author.id != self.bot.user.id:
                    continue

                try:
                    await message.delete()
                    deleted_count += 1
                except discord.Forbidden:
                    failed_count += 1
                    permission_error = True
                    logger.exception("Botメッセージの削除権限が不足しています: message_id=%s", message.id)
                    break
                except discord.HTTPException:
                    failed_count += 1
                    logger.exception("Botメッセージの削除に失敗しました: message_id=%s", message.id)
                    continue
        except discord.Forbidden:
            permission_error = True
            logger.exception("メッセージ履歴の取得権限が不足しています: channel=%s", interaction.channel_id)
        except discord.HTTPException:
            logger.exception("メッセージ履歴の取得中にHTTPエラーが発生しました: channel=%s", interaction.channel_id)

        if permission_error:
            await interaction.followup.send(
                "メッセージ削除権限が不足している可能性があります。"
                "Botに『メッセージを管理』権限を付与してください。",
                ephemeral=True,
            )
            return

        result = f"Mぼっとのメッセージを {deleted_count} 件削除しました。"
        if failed_count:
            result += f"\n削除できなかったメッセージ: {failed_count} 件"
        await interaction.followup.send(result, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    """Load the admin cog."""
    await bot.add_cog(AdminCog(bot))
