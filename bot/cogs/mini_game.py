"""Mini game Cog for BDM guild members (turn-based monster battles)."""

from __future__ import annotations

import asyncio
import copy
import logging
import random
from typing import Optional

import discord
from discord.ext import commands

logger = logging.getLogger(__name__)

# =========================
# ミニゲーム設定
# =========================

# Embedカラー (BDMカラー)
COLOR_BATTLE = 0x2C3E6B
COLOR_WIN = 0x2ECC71
COLOR_LOSE = 0xE74C3C
COLOR_ESCAPE = 0xF39C12

# プレイヤー初期ステータス
PLAYER_MAX_HP = 100
PLAYER_MAX_MP = 60
PLAYER_ATK = 20

# スキル設定
SKILL_MULTIPLIER = 1.8
SKILL_MP_COST = 30

# 逃走成功率
RUN_SUCCESS_RATE = 0.60

# モンスター特殊行動の発動率
SPECIAL_ACTION_RATE = 0.30

# ダメージ乱数幅 (±20%)
DAMAGE_RAND_RANGE = 0.20

# モンスター選択のタイムアウト (秒)
HUNT_SELECT_TIMEOUT = 30

# バトル中のコマンドタイムアウト (秒)
BATTLE_TIMEOUT = 60

# モンスター定義。バトル開始時に deep copy して使用する。
MONSTERS = (
    {
        "name": "フォレストウルフ",
        "emoji": "🐺",
        "hp": 80,
        "atk": 12,
        "special": None,
        "special_label": "なし",
        "reward": 150,
    },
    {
        "name": "デザートゴーレム",
        "emoji": "🗿",
        "hp": 150,
        "atk": 18,
        "special": "guard",
        "special_label": "防御強化",
        "reward": 300,
    },
    {
        "name": "影のアサシン",
        "emoji": "🗡️",
        "hp": 60,
        "atk": 28,
        "special": "double",
        "special_label": "2連撃",
        "reward": 250,
    },
    {
        "name": "古代マジシャン",
        "emoji": "🧙",
        "hp": 100,
        "atk": 15,
        "special": "magic",
        "special_label": "全体魔法",
        "reward": 350,
    },
    {
        "name": "黒砂の悪魔",
        "emoji": "👿",
        "hp": 200,
        "atk": 22,
        "special": "drain",
        "special_label": "HP吸収",
        "reward": 500,
    },
)

# バトルセッション { discord_user_id: player_state }
sessions: dict[int, dict] = {}


def _new_player_state(monster: dict) -> dict:
    """Create a fresh player state for a new battle."""
    return {
        "hp": PLAYER_MAX_HP,
        "max_hp": PLAYER_MAX_HP,
        "mp": PLAYER_MAX_MP,
        "max_mp": PLAYER_MAX_MP,
        "atk": PLAYER_ATK,
        "in_battle": True,
        "monster": monster,
        "turn": 1,
        "timeout_task": None,
        "busy": False,
    }


def _calc_damage(atk: float, rand_range: float = DAMAGE_RAND_RANGE) -> int:
    """Calculate damage as ATK ± rand_range (default ±20%)."""
    factor = random.uniform(1.0 - rand_range, 1.0 + rand_range)
    return max(1, int(atk * factor))


class MiniGame(commands.Cog):
    """Turn-based command battle mini game (!hunt / !attack / !skill / !run)."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # モンスター選択待ち { user_id: {"monsters": [...], "task": Task} }
        self.pending_hunts: dict[int, dict] = {}

    def cog_unload(self) -> None:
        """Cancel timers and clear all sessions when unloading this Cog."""
        for pending in self.pending_hunts.values():
            task = pending.get("task")
            if task and not task.done():
                task.cancel()
        self.pending_hunts.clear()

        for session in sessions.values():
            task = session.get("timeout_task")
            if task and not task.done():
                task.cancel()
        sessions.clear()

    # =========================
    # コマンド
    # =========================

    @commands.command(name="hunt")
    async def hunt(self, ctx: commands.Context, number: Optional[str] = None) -> None:
        """モンスター一覧を表示、または番号指定でバトルを開始する。"""
        user_id = ctx.author.id

        if user_id in sessions:
            await ctx.reply("バトル中です。先に決着をつけてください。")
            return

        if number is None:
            await self._show_monster_list(ctx)
            return

        await self._start_battle(ctx, number)

    @commands.command(name="attack")
    async def attack(self, ctx: commands.Context) -> None:
        """通常攻撃 (ATK基準ダメージ)。"""
        session = await self._get_battle_session(ctx)
        if session is None:
            return

        session["busy"] = True
        try:
            damage = _calc_damage(session["atk"])
            log = await self._apply_player_attack(session, damage, f"⚔️ 通常攻撃！ {damage} ダメージ！")
            await self._process_turn(ctx, session, log)
        finally:
            session["busy"] = False

    @commands.command(name="skill")
    async def skill(self, ctx: commands.Context) -> None:
        """スキル攻撃 (ATK×1.8 / 消費MP30)。"""
        session = await self._get_battle_session(ctx)
        if session is None:
            return

        if session["mp"] < SKILL_MP_COST:
            await ctx.reply(f"MPが足りません！(現在: {session['mp']} MP)")
            return

        session["busy"] = True
        try:
            session["mp"] -= SKILL_MP_COST
            damage = _calc_damage(session["atk"] * SKILL_MULTIPLIER)
            log = await self._apply_player_attack(session, damage, f"✨ スキル発動！ {damage} ダメージ！ (MP -{SKILL_MP_COST})")
            await self._process_turn(ctx, session, log)
        finally:
            session["busy"] = False

    @commands.command(name="run")
    async def run(self, ctx: commands.Context) -> None:
        """逃走を試みる (成功率60%)。"""
        session = await self._get_battle_session(ctx)
        if session is None:
            return

        session["busy"] = True
        try:
            if random.random() < RUN_SUCCESS_RATE:
                monster = session["monster"]
                self._end_session(ctx.author.id)
                embed = discord.Embed(
                    title="💨 逃走成功",
                    description=f"{monster['emoji']} {monster['name']} から逃げ切った！",
                    color=COLOR_ESCAPE,
                )
                await ctx.reply(embed=embed)
                return

            log = ["💨 逃走失敗！ 回り込まれてしまった！"]
            log.extend(self._monster_turn(session))

            if await self._check_battle_end(ctx, session, log):
                return

            session["turn"] += 1
            self._reset_battle_timeout(ctx, session)
            await ctx.reply("\n".join(log), embed=self._make_battle_embed(ctx, session))
        finally:
            session["busy"] = False

    @commands.command(name="status")
    async def status(self, ctx: commands.Context) -> None:
        """現在の自分のステータスを確認する。"""
        session = sessions.get(ctx.author.id)

        if session is None:
            embed = discord.Embed(
                title="📋 ステータス",
                description="バトルしていません。`!hunt` でモンスターを選んでください。",
                color=COLOR_BATTLE,
            )
            embed.add_field(
                name=f"👤 {ctx.author.display_name}",
                value=(
                    f"❤️ HP: {PLAYER_MAX_HP}/{PLAYER_MAX_HP}　"
                    f"💧 MP: {PLAYER_MAX_MP}/{PLAYER_MAX_MP}\n"
                    f"⚔️ ATK: {PLAYER_ATK}"
                ),
                inline=False,
            )
            await ctx.reply(embed=embed)
            return

        await ctx.reply(embed=self._make_battle_embed(ctx, session))

    # =========================
    # モンスター選択
    # =========================

    async def _show_monster_list(self, ctx: commands.Context) -> None:
        """ランダムな3体のモンスター一覧を表示する。"""
        user_id = ctx.author.id
        self._cancel_pending_hunt(user_id)

        candidates = random.sample(MONSTERS, 3)

        embed = discord.Embed(
            title="🗡️ モンスターを選択",
            description=f"`!hunt <番号>` でバトル開始！ ({HUNT_SELECT_TIMEOUT}秒以内)",
            color=COLOR_BATTLE,
        )
        for index, monster in enumerate(candidates, start=1):
            embed.add_field(
                name=f"{index}. {monster['emoji']} {monster['name']}",
                value=(
                    f"❤️ HP: {monster['hp']}　⚔️ ATK: {monster['atk']}\n"
                    f"特殊: {monster['special_label']}　💰 報酬: {monster['reward']}G"
                ),
                inline=False,
            )
        embed.set_footer(text="例: !hunt 1")

        await ctx.reply(embed=embed)

        task = asyncio.create_task(self._expire_pending_hunt(ctx, user_id))
        self.pending_hunts[user_id] = {"monsters": candidates, "task": task}

    async def _expire_pending_hunt(self, ctx: commands.Context, user_id: int) -> None:
        """Cancel the monster selection after the timeout."""
        try:
            await asyncio.sleep(HUNT_SELECT_TIMEOUT)
            if user_id in self.pending_hunts:
                del self.pending_hunts[user_id]
                await ctx.send(f"{ctx.author.mention} モンスター選択がタイムアウトしたためキャンセルされました。")
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("モンスター選択タイムアウト処理でエラーが発生しました: user=%s", user_id)

    def _cancel_pending_hunt(self, user_id: int) -> None:
        """Remove a pending monster selection without notification."""
        pending = self.pending_hunts.pop(user_id, None)
        if pending:
            task = pending.get("task")
            if task and not task.done():
                task.cancel()

    async def _start_battle(self, ctx: commands.Context, number: str) -> None:
        """Start a battle with the selected monster."""
        user_id = ctx.author.id
        pending = self.pending_hunts.get(user_id)

        if pending is None:
            await ctx.reply("先に `!hunt` でモンスター一覧を表示してください。")
            return

        if not number.isdigit() or not 1 <= int(number) <= len(pending["monsters"]):
            await ctx.reply("1〜3の番号を指定してください。")
            return

        monster = copy.deepcopy(pending["monsters"][int(number) - 1])
        monster["max_hp"] = monster["hp"]
        monster["guarding"] = False
        self._cancel_pending_hunt(user_id)

        session = _new_player_state(monster)
        sessions[user_id] = session
        self._reset_battle_timeout(ctx, session)

        logger.info("ミニゲームバトル開始: user=%s monster=%s", ctx.author, monster["name"])
        await ctx.reply(
            f"{monster['emoji']} **{monster['name']}** が現れた！",
            embed=self._make_battle_embed(ctx, session),
        )

    # =========================
    # バトル進行
    # =========================

    async def _get_battle_session(self, ctx: commands.Context) -> Optional[dict]:
        """Return the user's battle session, replying with an error if absent."""
        session = sessions.get(ctx.author.id)
        if session is None or not session["in_battle"]:
            await ctx.reply("バトルしていません。`!hunt` でモンスターを選んでください。")
            return None
        if session.get("busy"):
            return None
        return session

    async def _apply_player_attack(self, session: dict, damage: int, message: str) -> list[str]:
        """Apply player damage to the monster, honoring its guard state."""
        monster = session["monster"]
        if monster.get("guarding"):
            damage = max(1, damage // 2)
            monster["guarding"] = False
            message += f"\n🛡️ {monster['name']} は防御していたためダメージ半減！ ({damage} ダメージ)"
        monster["hp"] -= damage
        return [message]

    async def _process_turn(self, ctx: commands.Context, session: dict, log: list[str]) -> None:
        """Run the monster turn and battle-end checks after a player attack."""
        if await self._check_battle_end(ctx, session, log):
            return

        log.extend(self._monster_turn(session))

        if await self._check_battle_end(ctx, session, log):
            return

        session["turn"] += 1
        self._reset_battle_timeout(ctx, session)
        await ctx.reply("\n".join(log), embed=self._make_battle_embed(ctx, session))

    def _monster_turn(self, session: dict) -> list[str]:
        """Execute the monster's action (normal attack or 30% special)."""
        monster = session["monster"]
        special = monster["special"]
        log: list[str] = []

        if special and random.random() < SPECIAL_ACTION_RATE:
            if special == "guard":
                monster["guarding"] = True
                log.append(f"🛡️ {monster['name']} は防御を固めた！ (次の攻撃のダメージ半減)")
            elif special == "double":
                for hit in (1, 2):
                    damage = _calc_damage(monster["atk"])
                    session["hp"] -= damage
                    log.append(f"⚡ {monster['name']} の2連撃 ({hit}撃目)！ {damage} ダメージ！")
            elif special == "magic":
                damage = _calc_damage(monster["atk"] * 1.3)
                mp_damage = min(10, session["mp"])
                session["hp"] -= damage
                session["mp"] -= mp_damage
                log.append(f"🔮 {monster['name']} の全体魔法！ {damage} ダメージ！ (MP -{mp_damage})")
            elif special == "drain":
                damage = _calc_damage(monster["atk"])
                heal = damage // 2
                session["hp"] -= damage
                monster["hp"] = min(monster["max_hp"], monster["hp"] + heal)
                log.append(f"🩸 {monster['name']} のHP吸収！ {damage} ダメージ！ ({monster['name']} は {heal} 回復)")
        else:
            damage = _calc_damage(monster["atk"])
            session["hp"] -= damage
            log.append(f"💥 {monster['name']} の攻撃！ {damage} ダメージ！")

        return log

    async def _check_battle_end(self, ctx: commands.Context, session: dict, log: list[str]) -> bool:
        """Check win/lose conditions; finish the battle and return True if ended."""
        monster = session["monster"]

        if monster["hp"] <= 0:
            self._end_session(ctx.author.id)
            embed = discord.Embed(
                title="🎉 勝利！",
                description=(
                    f"{monster['emoji']} **{monster['name']}** を撃破した！\n"
                    f"💰 獲得ゴールド: **{monster['reward']}G**\n"
                    f"⏱️ 消費ターン数: **{session['turn']}**"
                ),
                color=COLOR_WIN,
            )
            await ctx.reply("\n".join(log), embed=embed)
            return True

        if session["hp"] <= 0:
            self._end_session(ctx.author.id)
            embed = discord.Embed(
                title="💀 敗北...",
                description=f"{monster['emoji']} **{monster['name']}** に倒されてしまった...\nゲームオーバー",
                color=COLOR_LOSE,
            )
            await ctx.reply("\n".join(log), embed=embed)
            return True

        return False

    def _end_session(self, user_id: int) -> None:
        """Remove the user's battle session and cancel its timeout task."""
        session = sessions.pop(user_id, None)
        if session:
            task = session.get("timeout_task")
            if task and not task.done():
                task.cancel()

    def _reset_battle_timeout(self, ctx: commands.Context, session: dict) -> None:
        """Restart the 60-second inactivity timeout for the battle."""
        task = session.get("timeout_task")
        if task and not task.done():
            task.cancel()
        session["timeout_task"] = asyncio.create_task(self._battle_timeout(ctx, ctx.author.id))

    async def _battle_timeout(self, ctx: commands.Context, user_id: int) -> None:
        """End the battle when the player is idle for too long."""
        try:
            await asyncio.sleep(BATTLE_TIMEOUT)
            if user_id in sessions:
                del sessions[user_id]
                await ctx.send(f"{ctx.author.mention} タイムアウトでバトルが終了しました。")
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("バトルタイムアウト処理でエラーが発生しました: user=%s", user_id)

    # =========================
    # Embed
    # =========================

    def _make_battle_embed(self, ctx: commands.Context, session: dict) -> discord.Embed:
        """Build the battle status embed."""
        monster = session["monster"]
        embed = discord.Embed(
            title=f"⚔️ バトル中 - ターン{session['turn']}",
            color=COLOR_BATTLE,
        )
        embed.add_field(
            name=f"👤 {ctx.author.display_name}",
            value=(
                f"❤️ HP: {max(0, session['hp'])}/{session['max_hp']}　"
                f"💧 MP: {session['mp']}/{session['max_mp']}"
            ),
            inline=False,
        )
        monster_status = f"❤️ HP: {max(0, monster['hp'])}/{monster['max_hp']}"
        if monster.get("guarding"):
            monster_status += "　🛡️ 防御中"
        embed.add_field(
            name=f"{monster['emoji']} {monster['name']}",
            value=monster_status,
            inline=False,
        )
        embed.set_footer(text="!attack / !skill / !run で行動してください")
        return embed


async def setup(bot: commands.Bot) -> None:
    """Load the mini game cog."""
    await bot.add_cog(MiniGame(bot))
