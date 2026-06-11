"""Ogre timer Cog for Black Desert Mobile node wars."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger(__name__)

# =========================
# オーガタイマー設定
# =========================

# オーガ再出現までの秒数
# 倒されてから10分後に再出現
OGRE_RESPAWN_SECONDS = 600  # 10分

# オーガ討伐後バフ継続時間
# 倒した瞬間から5分
OGRE_BUFF_SECONDS = 300  # 5分

# パネル表示更新間隔
OGRE_DISPLAY_UPDATE_SECONDS = 5

# 読み上げBotに読ませる想定の通知文
# 左：タイマー開始から何秒後に通知するか
# 右：投稿するメッセージ
#
# 仕様：
# 0秒    開始ボタンを押す＝討伐した瞬間
# 300秒  バフ切れ
# 420秒  出現3分前
# 480秒  出現2分前
# 540秒  出現1分前
# 570秒  出現30秒前
# 585秒  出現15秒前
# 600秒  オーガ出現
OGRE_NOTIFICATIONS = (
    (OGRE_BUFF_SECONDS, "バフおわり"),
    (OGRE_RESPAWN_SECONDS - 599, "オーガ没"),
    (OGRE_RESPAWN_SECONDS - 180, "オーガ3分前"),
    (OGRE_RESPAWN_SECONDS - 120, "オーガ2分前"),
    (OGRE_RESPAWN_SECONDS - 60, "オーガ1分前"),
    (OGRE_RESPAWN_SECONDS - 30, "30秒"),
    (OGRE_RESPAWN_SECONDS - 15, "15秒"),
    (OGRE_RESPAWN_SECONDS, "オーガ"),
)

# このタイマーはループしない。
# 開始ボタンを押してから10分計測して終了。
TOTAL_TIMER_SECONDS = OGRE_RESPAWN_SECONDS


@dataclass
class OgreTimerSession:
    """Per-channel ogre timer session state."""

    channel_id: int
    channel: discord.abc.Messageable
    panel_message: Optional[discord.Message] = None
    started_at: Optional[datetime] = None
    task: Optional[asyncio.Task[None]] = None
    is_running: bool = False
    notified_offsets: set[int] = field(default_factory=set)


def format_seconds(seconds: int) -> str:
    """Format seconds as MM:SS."""
    seconds = max(0, int(seconds))
    minutes = seconds // 60
    rest = seconds % 60
    return f"{minutes:02d}:{rest:02d}"


def progress_bar(elapsed: int, total: int, length: int = 12) -> str:
    """Build a compact text progress bar."""
    if total <= 0:
        return "□" * length

    ratio = min(1.0, max(0.0, elapsed / total))
    filled = int(ratio * length)
    return "■" * filled + "□" * (length - filled)


class OgreTimerView(discord.ui.View):
    """Button view for controlling an ogre timer."""

    def __init__(self, cog: "OgreTimerCog") -> None:
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="開始", style=discord.ButtonStyle.success, emoji="▶️")
    async def start_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        """Start the timer for this channel."""
        await self.cog.start_timer(interaction)

    @discord.ui.button(label="停止", style=discord.ButtonStyle.danger, emoji="⏹️")
    async def stop_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        """Stop the timer for this channel."""
        await self.cog.stop_timer(interaction)

    @discord.ui.button(label="状態確認", style=discord.ButtonStyle.secondary, emoji="📌")
    async def status_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        """Send the current timer status."""
        await self.cog.send_status(interaction)


class OgreTimerCog(commands.Cog):
    """Cog for per-channel ogre respawn timers."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.sessions: dict[int, OgreTimerSession] = {}

    def cog_unload(self) -> None:
        """Cancel active timer tasks when unloading this Cog."""
        for session in self.sessions.values():
            if session.task and not session.task.done():
                session.task.cancel()

    @app_commands.command(name="ogre", description="オーガタイマーパネルを表示します。")
    async def ogre(self, interaction: discord.Interaction) -> None:
        """Display the ogre timer control panel."""
        channel_id = interaction.channel_id
        if channel_id is None or interaction.channel is None:
            await interaction.response.send_message("チャンネルを取得できませんでした。", ephemeral=True)
            return

        logger.info("/ogre が実行されました: user=%s channel=%s", interaction.user, channel_id)
        embed = self.build_embed(channel_id)
        view = OgreTimerView(self)

        await interaction.response.send_message(embed=embed, view=view)
        panel_message = await interaction.original_response()

        session = self.sessions.get(channel_id)
        if session:
            session.panel_message = panel_message
            session.channel = interaction.channel
        else:
            self.sessions[channel_id] = OgreTimerSession(
                channel_id=channel_id,
                channel=interaction.channel,
                panel_message=panel_message,
            )

    async def start_timer(self, interaction: discord.Interaction) -> None:
        """Start the ogre timer for the current channel."""
        await interaction.response.defer()
        channel_id = interaction.channel_id
        if channel_id is None or interaction.channel is None:
            return

        logger.info("オーガタイマー開始ボタン: channel=%s", channel_id)
        session = self.sessions.get(channel_id)

        if session and session.is_running:
            await self.update_panel(session)
            return

        if session is None:
            session = OgreTimerSession(
                channel_id=channel_id,
                channel=interaction.channel,
                panel_message=interaction.message,
            )
            self.sessions[channel_id] = session

        session.channel = interaction.channel
        session.panel_message = interaction.message
        session.started_at = datetime.now(timezone.utc)
        session.is_running = True
        session.notified_offsets.clear()

        if session.task and not session.task.done():
            session.task.cancel()
        session.task = asyncio.create_task(self.run_timer(session))

        await self.update_panel(session)

    async def stop_timer(self, interaction: discord.Interaction) -> None:
        """Stop the ogre timer for the current channel."""
        await interaction.response.defer()
        channel_id = interaction.channel_id
        if channel_id is None:
            return

        logger.info("オーガタイマー停止ボタン: channel=%s", channel_id)
        session = self.sessions.get(channel_id)

        if not session or not session.is_running:
            if session:
                await self.update_panel(session)
            return

        session.is_running = False

        if session.task and not session.task.done():
            session.task.cancel()

        await self.update_panel(session, stopped=True)

    async def send_status(self, interaction: discord.Interaction) -> None:
        """Acknowledge status button presses without sending ephemeral messages."""
        await interaction.response.defer()
        channel_id = interaction.channel_id
        if channel_id is None:
            return

        logger.info("オーガタイマー状態確認ボタン: channel=%s", channel_id)
        session = self.sessions.get(channel_id)
        if session:
            await self.update_panel(session)

    async def run_timer(self, session: OgreTimerSession) -> None:
        """Run notification and panel update loop until the 10-minute timer finishes."""
        try:
            last_panel_update = -1

            while session.is_running:
                elapsed = self.get_elapsed_seconds(session)

                # 通知判定
                for offset, message in OGRE_NOTIFICATIONS:
                    if elapsed >= offset and offset not in session.notified_offsets:
                        session.notified_offsets.add(offset)
                        await self.send_ogre_notification(session, message)

                # パネル更新
                if elapsed - last_panel_update >= OGRE_DISPLAY_UPDATE_SECONDS:
                    await self.update_panel(session)
                    last_panel_update = elapsed

                # 10分経過で終了。ループしない。
                if elapsed >= TOTAL_TIMER_SECONDS:
                    session.is_running = False
                    await self.update_panel(session, finished=True)
                    break

                await asyncio.sleep(1)

        except asyncio.CancelledError:
            logger.info("オーガタイマーがキャンセルされました: channel=%s", session.channel_id)
        except Exception as exc:
            logger.exception("オーガタイマーでエラーが発生しました: channel=%s", session.channel_id)
            session.is_running = False
            try:
                await session.channel.send(f"オーガタイマーでエラーが発生しました: {exc}")
            except Exception:
                logger.exception("オーガタイマーのエラー通知送信に失敗しました")

    async def send_ogre_notification(self, session: OgreTimerSession, message: str) -> None:
        """
        読み上げBotに読ませる想定の通常テキスト通知。
        Discord標準TTSではなく、既存の読み上げBot向け。
        """
        try:
            await session.channel.send(message)
        except Exception:
            logger.exception("オーガ通知の送信に失敗しました: channel=%s message=%s", session.channel_id, message)

    def get_elapsed_seconds(self, session: OgreTimerSession) -> int:
        """Return elapsed seconds from the timer start."""
        if not session.started_at:
            return 0

        now = datetime.now(timezone.utc)
        return int((now - session.started_at).total_seconds())

    def get_status_text(self, elapsed: int) -> str:
        """Return the current timer status text."""
        if elapsed >= TOTAL_TIMER_SECONDS:
            return "オーガタイマーは完了しています。"

        ogre_remaining = OGRE_RESPAWN_SECONDS - elapsed

        if elapsed < OGRE_BUFF_SECONDS:
            buff_remaining = OGRE_BUFF_SECONDS - elapsed
            return (
                f"次のオーガまで {format_seconds(ogre_remaining)} です。\n"
                f"オーガバフ終了まで {format_seconds(buff_remaining)} です。"
            )

        return (
            f"次のオーガまで {format_seconds(ogre_remaining)} です。\n"
            "オーガバフは終了済みです。"
        )

    async def update_panel(
        self,
        session: OgreTimerSession,
        stopped: bool = False,
        finished: bool = False,
    ) -> None:
        """Update the timer panel embed."""
        if not session.panel_message:
            return

        embed = self.build_embed(
            session.channel_id,
            stopped=stopped,
            finished=finished,
        )

        try:
            await session.panel_message.edit(embed=embed, view=OgreTimerView(self))
        except Exception:
            logger.exception("オーガタイマーパネルの更新に失敗しました: channel=%s", session.channel_id)

    def build_embed(
        self,
        channel_id: int,
        stopped: bool = False,
        finished: bool = False,
    ) -> discord.Embed:
        """Build the ogre timer panel embed."""
        session = self.sessions.get(channel_id)

        if stopped:
            embed = discord.Embed(
                title="オーガタイマー",
                description="停止中です。",
            )
            embed.add_field(name="状態", value="⏹️ 停止", inline=False)
            return embed

        if finished:
            embed = discord.Embed(
                title="オーガタイマー",
                description="オーガ出現時間になりました。タイマーは完了です。",
            )
            embed.add_field(name="状態", value="✅ 完了", inline=False)
            embed.add_field(name="次の操作", value="次に倒したら、もう一度「開始」を押してください。", inline=False)
            return embed

        if not session or not session.is_running or not session.started_at:
            embed = discord.Embed(
                title="オーガタイマー",
                description="オーガを倒したら開始ボタンを押してください。",
            )
            embed.add_field(name="状態", value="待機中", inline=False)
            embed.add_field(name="タイマー", value="倒してから10分後に再出現", inline=False)
            embed.add_field(name="バフ", value="討伐後5分でバフ切れ", inline=False)
            embed.add_field(
                name="通知",
                value=(
                    "バフ切れ / オーガ没 / 出現3分前 / 出現2分前 / 出現1分前\n"
                    "出現30秒前 / 出現15秒前 / オーガ出現"
                ),
                inline=False,
            )
            return embed

        elapsed = self.get_elapsed_seconds(session)
        remaining = max(0, OGRE_RESPAWN_SECONDS - elapsed)
        bar = progress_bar(elapsed, OGRE_RESPAWN_SECONDS)

        embed = discord.Embed(
            title="オーガタイマー",
            description="次のオーガ出現までカウント中です。",
        )

        embed.add_field(name="状態", value="▶️ カウント中", inline=False)
        embed.add_field(name="次のオーガまで", value=format_seconds(remaining), inline=True)
        embed.add_field(name="経過時間", value=format_seconds(elapsed), inline=True)

        if elapsed < OGRE_BUFF_SECONDS:
            buff_remaining = OGRE_BUFF_SECONDS - elapsed
            embed.add_field(name="バフ終了まで", value=format_seconds(buff_remaining), inline=True)
        else:
            embed.add_field(name="バフ", value="終了済み", inline=True)

        embed.add_field(name="進行", value=bar, inline=False)

        return embed


async def setup(bot: commands.Bot) -> None:
    """Load the ogre timer cog."""
    await bot.add_cog(OgreTimerCog(bot))
