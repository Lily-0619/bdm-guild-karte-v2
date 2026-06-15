"""Collection-session state shared across the scraper, analyzer, and detail app.

A "collection session" groups every guild scraped from one continuous data
gathering effort under a single logical date -- the day the gathering started
(分析を開始した日).  Because scraping can cross midnight and may be split over
multiple runs, the per-file collection date is unreliable for management.  This
module stores the session date and the guilds collected so far in one JSON file,
which is the only channel shared between the separate scraper/analyzer processes.

The session intentionally persists across multiple scraper runs.  Starting a new
collection is an explicit action (``clear_session``) so retries and night-crossing
runs keep accumulating into the same summary.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

try:
    from .paths import DATA_DIR, PROJECT_ROOT
except ImportError:  # 直接実行された場合のため
    from paths import DATA_DIR, PROJECT_ROOT  # type: ignore

SESSION_PATH = DATA_DIR / ".session.json"


def load_session() -> dict | None:
    """Return the current session dict, or ``None`` when no session is active."""

    if not SESSION_PATH.exists():
        return None
    try:
        data = json.loads(SESSION_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict) or not data.get("session_date"):
        return None
    data.setdefault("guilds", {})
    return data


def save_session(session: dict) -> None:
    SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
    SESSION_PATH.write_text(
        json.dumps(session, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_or_create_session(now: datetime | None = None) -> dict:
    """Return the active session, creating one stamped with today if needed.

    Creation happens lazily at scrape start, so ``session_date`` is the day the
    collection actually begins.
    """

    session = load_session()
    if session is not None:
        return session
    run_datetime = now or datetime.now()
    session = {
        "session_date": run_datetime.strftime("%Y-%m-%d"),
        "started_at": run_datetime.isoformat(timespec="seconds"),
        "guilds": {},
    }
    save_session(session)
    return session


def record_guild(guild_name: str, workbook_path: Path | str) -> dict:
    """Record that ``guild_name`` was collected in the current session."""

    session = get_or_create_session()
    path = Path(workbook_path)
    try:
        stored = path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        stored = str(path)
    session.setdefault("guilds", {})[guild_name] = stored
    save_session(session)
    return session


def clear_session() -> None:
    """Drop the active session so the next scrape starts a fresh one."""

    if SESSION_PATH.exists():
        SESSION_PATH.unlink()


def session_guild_files(session: dict | None = None) -> dict[str, Path]:
    """Return a mapping of guild name -> absolute workbook path for the session."""

    session = session or load_session()
    if not session:
        return {}
    resolved: dict[str, Path] = {}
    for guild_name, stored in session.get("guilds", {}).items():
        candidate = Path(stored)
        if not candidate.is_absolute():
            candidate = PROJECT_ROOT / candidate
        resolved[guild_name] = candidate
    return resolved
