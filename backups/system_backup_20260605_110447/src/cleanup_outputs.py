"""Clean up old generated card PNG files.

Examples:
    python -m src.cleanup_outputs --dry-run
    python -m src.cleanup_outputs --keep-latest
    python -m src.cleanup_outputs --keep-latest-per-guild
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CARDS_DIR = PROJECT_ROOT / "output" / "cards"


@dataclass(frozen=True)
class CardFile:
    path: Path
    guild_name: str
    sort_key: tuple[float, str]


def main() -> int:
    parser = argparse.ArgumentParser(description="Clean old generated card PNGs.")
    parser.add_argument("--cards-dir", type=Path, default=DEFAULT_CARDS_DIR)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only show files that would be deleted; do not delete anything.",
    )
    parser.add_argument(
        "--keep-latest",
        action="store_true",
        help="Keep only the newest PNG in output/cards overall.",
    )
    parser.add_argument(
        "--keep-latest-per-guild",
        action="store_true",
        help="Keep the newest PNG for each guild name inferred from file names.",
    )
    args = parser.parse_args()

    if not args.keep_latest and not args.keep_latest_per_guild:
        args.keep_latest_per_guild = True

    delete_targets = find_cleanup_targets(
        args.cards_dir,
        keep_latest=args.keep_latest,
        keep_latest_per_guild=args.keep_latest_per_guild,
    )
    action = "DRY-RUN delete" if args.dry_run else "Delete"
    if not delete_targets:
        print(f"No PNG files to delete in {args.cards_dir}")
        return 0

    for path in delete_targets:
        print(f"{action}: {path}")
        if not args.dry_run:
            path.unlink()
    print(f"Done. target_count={len(delete_targets)} dry_run={args.dry_run}")
    return 0


def find_cleanup_targets(
    cards_dir: Path,
    *,
    keep_latest: bool = False,
    keep_latest_per_guild: bool = True,
) -> list[Path]:
    """Return PNG files that should be deleted without touching SQLite data."""

    cards = scan_cards(cards_dir)
    if not cards:
        return []
    keep: set[Path] = set()
    if keep_latest:
        keep.add(max(cards, key=lambda card: card.sort_key).path)
    if keep_latest_per_guild:
        grouped: dict[str, list[CardFile]] = {}
        for card in cards:
            grouped.setdefault(card.guild_name, []).append(card)
        for group in grouped.values():
            keep.add(max(group, key=lambda card: card.sort_key).path)
    return sorted({card.path for card in cards if card.path not in keep})


def scan_cards(cards_dir: Path) -> list[CardFile]:
    if not cards_dir.exists():
        return []
    return [build_card_file(path) for path in cards_dir.glob("*.png")]


def build_card_file(path: Path) -> CardFile:
    return CardFile(
        path=path,
        guild_name=infer_guild_name(path),
        sort_key=(path.stat().st_mtime, path.name),
    )


def infer_guild_name(path: Path) -> str:
    """Infer guild name from common card file names.

    Supports names such as ``card_xAEGISx_2026-06-01.png`` and
    ``xAEGISx_2026-06-01.png``. If a date cannot be found, the stem is treated
    as one guild bucket.
    """

    stem = path.stem
    stem = re.sub(r"^(card|karte|guild)[_\-]", "", stem, flags=re.IGNORECASE)
    match = re.match(r"(?P<guild>.+?)[_\-]\d{4}[_\-]?\d{2}[_\-]?\d{2}.*$", stem)
    if match:
        return match.group("guild")
    return stem


if __name__ == "__main__":
    raise SystemExit(main())
