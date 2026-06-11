"""Helpers for finding already-generated guild card PNG files."""

from __future__ import annotations

from pathlib import Path

from bot.config import CARDS_DIR


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
