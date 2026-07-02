# -*- coding: utf-8 -*-
"""One-shot pipeline: scrape -> analyze -> AI comments -> cards.

GUI（app.py）のボタンを順に押していた流れを1コマンドに束ねる:

    python -m src.pipeline                     # 全段実行
    python -m src.pipeline --skip-scrape       # 取得済みデータから再集計
    python -m src.pipeline --skip-comments     # AIコメント無しでカルテまで
    python -m src.pipeline --new-session       # 新しい収集として開始

失敗の扱い:
    取得・一覧作成の失敗はそこで停止（後段は入力が無いと意味がないため）。
    AIコメント段の失敗は警告して続行する — make_card はAIコメントが無ければ
    ルールベースの auto_comment に自動フォールバックするので、カルテは作れる。

各段は子プロセスとして実行する（GUIと同じ形）。stdin は閉じて渡すので、
scraper の「Enterで終了」待ちは発生しない。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

try:
    from .paths import PROJECT_ROOT, SRC_DIR, ensure_dirs
    from . import session as session_state
except ImportError:  # 直接実行された場合のため
    from paths import PROJECT_ROOT, SRC_DIR, ensure_dirs  # type: ignore
    import session as session_state  # type: ignore

VENV_PYTHON = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"

EXIT_OK = 0
EXIT_WARNINGS = 1
EXIT_FATAL = 2


@dataclass(frozen=True)
class Stage:
    """One pipeline stage: a child script plus its arguments."""

    label: str
    script: str
    args: tuple[str, ...] = ()
    fatal: bool = True  # False = 失敗しても後段へ進む（警告扱い）


def build_stage_plan(
    *,
    skip_scrape: bool = False,
    skip_analyze: bool = False,
    skip_comments: bool = False,
    skip_cards: bool = False,
    model: str | None = None,
    source: str | None = None,
    capture_api: str | None = None,
) -> list[Stage]:
    """Build the ordered stage list from the CLI flags (pure, testable)."""

    stages: list[Stage] = []
    if not skip_scrape:
        scrape_args: tuple[str, ...] = ()
        if capture_api:
            scrape_args = ("--capture-api", capture_api)
        stages.append(Stage("DBonkデータ取得", "scraper.py", scrape_args))
    if not skip_analyze:
        analyze_args: tuple[str, ...] = ("--source", source) if source else ()
        stages.append(Stage("一覧作成", "analyze.py", analyze_args))
    if not skip_comments:
        model_args: tuple[str, ...] = ("--model", model) if model else ()
        stages.append(
            Stage("コメント元データ作成", "comment_source.py", model_args, fatal=False)
        )
        stages.append(Stage("AIコメント生成", "comment_ai.py", model_args, fatal=False))
    if not skip_cards:
        stages.append(Stage("カルテ作成", "make_card.py"))
    return stages


def session_guard_message(session: dict | None, today: str) -> str | None:
    """Return an abort message when the active session is stale (pure, testable).

    リセットし忘れた古いセッションのまま全自動で走ると、過去日の
    summary_<日付>.xlsx を新データで上書きしてしまう。日付が今日でない
    セッションが残っている場合は、明示フラグが無い限り停止する。
    """

    if session is None:
        return None
    session_date = str(session.get("session_date") or "")
    if session_date == today:
        return None
    collected = len(session.get("guilds", {}))
    return (
        f"前回の収集セッション（{session_date}・{collected}ギルド収集済み）が残っています。\n"
        f"このまま実行すると summary_{session_date}.xlsx が今回のデータで上書きされます。\n"
        "  --new-session      : 新しい収集として開始する（推奨）\n"
        "  --continue-session : 前回セッションに追記する（夜またぎ・分割収集の続き）\n"
        "のどちらかを指定して再実行してください。"
    )


def pipeline_python() -> str:
    """Prefer the project venv python for child stages."""

    if VENV_PYTHON.exists():
        return str(VENV_PYTHON)
    return sys.executable


def child_env() -> dict[str, str]:
    return {
        **os.environ,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "PYTHONUNBUFFERED": "1",
    }


def run_stage(python_exe: str, stage: Stage) -> int:
    script_path = SRC_DIR / stage.script
    command = [python_exe, str(script_path), *stage.args]
    print()
    print(f"========== [{stage.label}] 開始 ==========")
    print(f"> {' '.join(command)}")
    completed = subprocess.run(
        command,
        cwd=str(PROJECT_ROOT),
        env=child_env(),
        stdin=subprocess.DEVNULL,
    )
    return completed.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="取得→一覧作成→AIコメント→カルテ作成を一括実行します。"
    )
    parser.add_argument("--skip-scrape", action="store_true", help="DBonk取得を飛ばす")
    parser.add_argument("--skip-analyze", action="store_true", help="一覧作成を飛ばす")
    parser.add_argument(
        "--skip-comments", action="store_true", help="コメント元データ作成とAIコメント生成を飛ばす"
    )
    parser.add_argument("--skip-cards", action="store_true", help="カルテ作成を飛ばす")
    parser.add_argument("--model", default=None, help="AIコメントに使うOllamaモデル")
    parser.add_argument(
        "--source",
        choices=("auto", "sqlite", "excel"),
        default=None,
        help="一覧作成の入力ソース（analyze.py --source に渡す）",
    )
    parser.add_argument(
        "--capture-api",
        metavar="DIR",
        default=None,
        help="取得時にAPIレスポンスを記録する（scraper.py --capture-api に渡す）",
    )
    session_group = parser.add_mutually_exclusive_group()
    session_group.add_argument(
        "--new-session",
        action="store_true",
        help="実行前に収集セッションをリセットし、新しい収集として開始する",
    )
    session_group.add_argument(
        "--continue-session",
        action="store_true",
        help="日付が古い収集セッションが残っていても、そのまま追記する",
    )
    args = parser.parse_args(argv)

    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:  # noqa: BLE001
        pass

    ensure_dirs()

    if args.new_session:
        session = session_state.load_session()
        if session is not None:
            print(
                f"収集セッション（{session.get('session_date')}・"
                f"{len(session.get('guilds', {}))}ギルド）をリセットします。"
            )
        session_state.clear_session()

    # 取得を伴う実行では、古いセッションのまま走って過去日サマリーを
    # 上書きしないよう確認する（--continue-session で明示的に続行できる）。
    if not args.skip_scrape and not args.continue_session:
        message = session_guard_message(
            session_state.load_session(), date.today().isoformat()
        )
        if message:
            print("⚠ " + message)
            return EXIT_FATAL

    stages = build_stage_plan(
        skip_scrape=args.skip_scrape,
        skip_analyze=args.skip_analyze,
        skip_comments=args.skip_comments,
        skip_cards=args.skip_cards,
        model=args.model,
        source=args.source,
        capture_api=args.capture_api,
    )
    if not stages:
        print("実行する段がありません（すべて --skip-* 指定されています）。")
        return EXIT_OK

    python_exe = pipeline_python()
    print(f"パイプライン開始: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Python: {python_exe}")
    print("実行順: " + " → ".join(stage.label for stage in stages))

    results: list[tuple[Stage, str, float]] = []
    warnings = 0
    for index, stage in enumerate(stages):
        started = time.monotonic()
        exit_code = run_stage(python_exe, stage)
        elapsed = time.monotonic() - started
        if exit_code == 0:
            results.append((stage, "OK", elapsed))
            print(f"[OK] {stage.label}（{_format_elapsed(elapsed)}）")
            continue
        if stage.fatal:
            results.append((stage, f"失敗（終了コード {exit_code}）", elapsed))
            _print_summary(results, skipped=stages[index + 1 :])
            print(f"❌ {stage.label} に失敗したため中断しました。")
            return EXIT_FATAL
        warnings += 1
        results.append((stage, f"失敗→続行（終了コード {exit_code}）", elapsed))
        print(
            f"⚠ {stage.label} に失敗しましたが続行します"
            "（カルテはルールベースコメントで作成されます）。"
        )

    _print_summary(results, skipped=[])
    if warnings:
        print(f"⚠ 警告付きで完了しました（失敗 {warnings} 段）。")
        return EXIT_WARNINGS
    print("✅ すべて完了しました。")
    return EXIT_OK


def _format_elapsed(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}分{secs:02d}秒" if minutes else f"{secs}秒"


def _print_summary(results: list[tuple[Stage, str, float]], skipped: list[Stage]) -> None:
    print()
    print("========== 実行結果サマリー ==========")
    for stage, status, elapsed in results:
        print(f"  {stage.label}: {status}（{_format_elapsed(elapsed)}）")
    for stage in skipped:
        print(f"  {stage.label}: 未実行")
    print("=====================================")


if __name__ == "__main__":
    raise SystemExit(main())
