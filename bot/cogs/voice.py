"""Voice channel join/leave Cog.

読み上げBotがVCにいないBotのメッセージを読み上げない問題への対策として、
このBot自身を呼んだ人のVCに参加させるための機能。
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger(__name__)


class VoiceCog(commands.Cog):
    """Cog for joining/leaving voice channels on command."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="come",
        description="コマンドを打った人が今いるボイスチャンネルにBotを呼びます。",
    )
    async def come(self, interaction: discord.Interaction) -> None:
        """Join the voice channel the caller is currently in."""
        if interaction.guild is None:
            await interaction.response.send_message(
                "このコマンドはサーバー内で実行してください。", ephemeral=True
            )
            return

        member = interaction.user
        voice_state = getattr(member, "voice", None)
        if voice_state is None or voice_state.channel is None:
            await interaction.response.send_message(
                "先にボイスチャンネルに入ってから /come を実行してください。",
                ephemeral=True,
            )
            return

        target_channel = voice_state.channel
        logger.info(
            "/come が実行されました: user=%s channel=%s", member, target_channel
        )

        voice_client = interaction.guild.voice_client
        try:
            if voice_client is not None and voice_client.is_connected():
                if voice_client.channel.id == target_channel.id:
                    await interaction.response.send_message(
                        f"すでに **{target_channel.name}** にいます。",
                        ephemeral=True,
                    )
                    return
                await voice_client.move_to(target_channel)
                logger.info("VCを移動しました: channel=%s", target_channel)
            else:
                await target_channel.connect()
                logger.info("VCに接続しました: channel=%s", target_channel)
        except discord.ClientException as exc:
            logger.warning("VC接続に失敗しました: %s", exc)
            await interaction.response.send_message(
                f"ボイスチャンネルに接続できませんでした: {exc}", ephemeral=True
            )
            return
        except Exception as exc:  # noqa: BLE001
            logger.exception("VC接続中に予期しないエラーが発生しました")
            await interaction.response.send_message(
                f"ボイスチャンネル接続中にエラーが発生しました: {exc}",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"**{target_channel.name}** に参加しました。", ephemeral=True
        )

    @app_commands.command(
        name="bye",
        description="Botをボイスチャンネルから退出させます。",
    )
    async def bye(self, interaction: discord.Interaction) -> None:
        """Disconnect the bot from its voice channel."""
        if interaction.guild is None:
            await interaction.response.send_message(
                "このコマンドはサーバー内で実行してください。", ephemeral=True
            )
            return

        voice_client = interaction.guild.voice_client
        if voice_client is None or not voice_client.is_connected():
            await interaction.response.send_message(
                "Botはボイスチャンネルにいません。", ephemeral=True
            )
            return

        channel_name = getattr(voice_client.channel, "name", "ボイスチャンネル")
        logger.info("/bye が実行されました: user=%s channel=%s", interaction.user, channel_name)
        await voice_client.disconnect(force=True)
        await interaction.response.send_message(
            f"**{channel_name}** から退出しました。", ephemeral=True
        )


async def setup(bot: commands.Bot) -> None:
    """Load the voice cog."""
    await bot.add_cog(VoiceCog(bot))
