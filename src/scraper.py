"""DBonk guild member scraper (MVP, Playwright版)。"""

from __future__ import annotations

import csv
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from playwright.sync_api import Locator, Page

try:
    from .paths import CONFIG_DIR, DATA_DIR, PROJECT_ROOT, ensure_dirs
    from . import session as session_state
except ImportError:  # 直接実行された場合のため
    from paths import CONFIG_DIR, DATA_DIR, PROJECT_ROOT, ensure_dirs  # type: ignore
    import session as session_state  # type: ignore

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src import db  # noqa: E402

CONFIG_PATH = CONFIG_DIR / "guilds.txt"
ENV_PATH = PROJECT_ROOT / ".env"
DBONK_ENV_KEYS = ("DBONK_USERNAME", "DBONK_PASSWORD")

DBONK_LOGIN_URL = "https://dbonk.com/bdmbsmv2/index.php"
RESULT_TIMEOUT_SECONDS = 40
GUILD_LOAD_TIMEOUT_SECONDS = 60
POLL_INTERVAL_SECONDS = 0.5
DEBUG_SAVE_FILES = False


@dataclass
class MemberRow:
    rank: str
    player_name: str
    level: str
    cpm: str
    fcp: str
    retrieved_at: str


def sanitize_filename(text: str) -> str:
    text = text.strip()
    text = re.sub(r'[\\/:*?"<>|]', "_", text)
    text = re.sub(r"\s+", " ", text)
    text = text.rstrip(". ")
    if not text:
        return "unknown_guild"
    reserved = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "COM1",
        "COM2",
        "COM3",
        "COM4",
        "COM5",
        "COM6",
        "COM7",
        "COM8",
        "COM9",
        "LPT1",
        "LPT2",
        "LPT3",
        "LPT4",
        "LPT5",
        "LPT6",
        "LPT7",
        "LPT8",
        "LPT9",
    }
    if text.upper() in reserved:
        text = f"_{text}"
    return text[:80]


def get_guild_data_dir(guild_name: str) -> Path:
    guild_dir = DATA_DIR / sanitize_filename(guild_name)
    guild_dir.mkdir(parents=True, exist_ok=True)
    return guild_dir


def load_guild_names(config_path: Path) -> List[str]:
    if not config_path.exists():
        raise FileNotFoundError(f"ギルド設定ファイルが見つかりません: {config_path}")
    guilds = [
        line.strip()
        for line in config_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not guilds:
        raise ValueError("guilds.txt が空です。1行に1ギルド名を書いてください。")
    return guilds


def parse_env_line(line: str) -> tuple[str, str] | None:
    text = line.strip()
    if not text or text.startswith("#"):
        return None
    if text.startswith("export "):
        text = text[len("export ") :].lstrip()
    if "=" not in text:
        return None
    key, value = text.split("=", 1)
    key = key.strip()
    value = value.strip()
    if not key:
        return None
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return key, value


def load_project_env(env_path: Path = ENV_PATH) -> dict[str, str]:
    """Load project-root .env without relying on python-dotenv."""
    loaded: dict[str, str] = {}
    if not env_path.exists():
        return loaded

    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        parsed = parse_env_line(line)
        if parsed is None:
            continue
        key, value = parsed
        loaded[key] = value
        if key.startswith("DBONK_"):
            os.environ[key] = value
    return loaded


def detected_dbonk_keys(env_path: Path = ENV_PATH) -> list[str]:
    if not env_path.exists():
        return []
    try:
        loaded = {
            key
            for line in env_path.read_text(encoding="utf-8-sig").splitlines()
            if (parsed := parse_env_line(line)) is not None
            for key, _value in (parsed,)
        }
    except OSError:
        return []
    return sorted(key for key in loaded if key.startswith("DBONK_"))


def dbonk_env_error_message(env_path: Path = ENV_PATH) -> str:
    absolute_path = env_path.resolve()
    return (
        "DBONK_USERNAME / DBONK_PASSWORD が見つかりません。"
        f"確認した.env: {absolute_path} / "
        f"存在: {'あり' if env_path.exists() else 'なし'} / "
        f"検出したDBONK系キー: {detected_dbonk_keys(env_path) or 'なし'}"
    )


def try_auto_login(page: Page) -> None:
    """通常ログインフォームが見えている時だけ .env の情報で自動ログインする。"""
    username_input = page.locator("input[placeholder='Input Username']")
    password_input = page.locator("input[placeholder='Input Password'], input[type='password']")

    if username_input.count() == 0 or password_input.count() == 0:
        print("ログイン済み、または通常ログインフォームが見つからないため自動ログインをスキップします。")
        return

    username = os.getenv("DBONK_USERNAME", "").strip()
    password = os.getenv("DBONK_PASSWORD", "").strip()

    if not username or not password:
        raise RuntimeError(dbonk_env_error_message())

    print("自動ログインを開始します。")
    username_input.first.fill(username)
    password_input.first.fill(password)

    login_btn = page.get_by_role("button", name=re.compile(r"^\s*Login\s*$", re.I))
    if login_btn.count() > 0 and login_btn.first.is_visible():
        login_btn.first.click(timeout=3000)
    else:
        password_input.first.press("Enter")

    page.wait_for_timeout(1500)
    print("自動ログインを実行しました。")


def try_select_asia_server(page: Page) -> None:
    """サーバー選択画面が表示された場合は Asia を試す（失敗しても継続）。"""
    asia_candidates = [
        page.get_by_text(re.compile(r"^\s*Asia\s*$", re.I)),
        page.get_by_role("button", name=re.compile(r"^\s*Asia\s*$", re.I)),
    ]
    for candidate in asia_candidates:
        try:
            if candidate.count() > 0 and candidate.first.is_visible():
                candidate.first.click(timeout=2000)
                page.wait_for_timeout(1000)
                print("Asiaサーバー選択を実行しました。")
                return
        except Exception:
            continue


def close_general_chat_panel(page: Page) -> None:
    try:
        if page.locator('input[placeholder="Search General Chat"]').count() == 0:
            return
    except Exception:
        return
    candidates = [
        page.locator("button:has-text('>')"),
        page.locator("button:has-text('＞')"),
        page.locator("button:has-text('arrow_forward_ios')"),
        page.locator(":is(div,span,i):has-text('>')"),
        page.locator(":is(div,span,i):has-text('＞')"),
        page.locator(":is(div,span,i):has-text('arrow_forward_ios')"),
        page.locator("[aria-label*='chat' i]"),
    ]
    for c in candidates:
        try:
            for i in range(min(c.count(), 10)):
                el = c.nth(i)
                if not el.is_visible():
                    continue
                box = el.bounding_box()
                if box and box["x"] > 650:
                    el.click(timeout=1500)
                    page.wait_for_timeout(400)
                    return
        except Exception:
            continue


# --- Name Search（左メニュー Search）からギルドを開く -------------------------
# 旧実装は左メニューの「Guild Ranking」を面積ヒューリスティクスで探し、検索後に
# 1〜4ページ分の行を走査していた。これは非常に遅いうえ、展開中の左メニューでは
# 別項目（Node Holder / Enhancement 等）がクリックを横取りし、Chaos Gear
# Enhancement 画面へ飛んでしまう事故が多かった。
# 現在は安定した id を持つ Name Search 画面だけを使い、完全一致の 1 件だけを
# クリックする。

SEARCH_MENU_ITEM = "li#searchg"
SEARCH_FORM_INPUT = "form#statsearch input.gsinput"
SEARCH_FORM_SUBMIT = "form#statsearch button.gbuttons"
SEARCH_RESULT_CONTAINER = "div#mcontent4"
SEARCH_RESULT_GUILD_SPANS = "result#guildsearch span[data-id]"
SEARCH_PAGE_READY_TIMEOUT_MS = 20_000
SEARCH_RESULT_TIMEOUT_MS = 90_000
DETAIL_LOAD_TIMEOUT_MS = 90_000

DETAIL_READY_JS = """() => {
    const b = document.body.innerText || '';
    return b.includes('Active Guild Members')
        || b.includes('Guild Combat Power')
        || b.includes('Server Origin');
}"""


def js_click(page: Page, selector: str) -> bool:
    """JS の click() で要素を押す。

    左メニューは展開すると項目同士が重なり、Playwright の通常クリックは
    「... intercepts pointer events」で隣の項目に吸われて別画面へ遷移する。
    サイト側は jQuery の委譲ハンドラなので、JS の click() でも正しく発火する。
    """
    return bool(
        page.evaluate(
            """(sel) => {
                const el = document.querySelector(sel);
                if (!el) return false;
                el.click();
                return true;
            }""",
            selector,
        )
    )


def open_search_page(page: Page) -> None:
    """左メニューの Search（li#searchg）を開き、検索欄が使えるまで待つ。"""
    if not js_click(page, SEARCH_MENU_ITEM):
        raise RuntimeError(f"左メニューの Search（{SEARCH_MENU_ITEM}）が見つかりません。")
    try:
        page.wait_for_function(
            """(sel) => {
                const el = document.querySelector(sel);
                if (!el) return false;
                const r = el.getBoundingClientRect();
                return r.width > 0 && r.height > 0;
            }""",
            arg=SEARCH_FORM_INPUT,
            timeout=SEARCH_PAGE_READY_TIMEOUT_MS,
        )
    except Exception as exc:
        raise RuntimeError(f"Name Search 画面を開けませんでした: {exc}") from exc


def run_name_search(page: Page, guild_name: str) -> None:
    """ギルド名で Name Search を実行し、結果が描画されるまで待つ。"""
    # 前回の結果が残ったままだと待機条件が即座に成立し、前のギルドの候補を
    # 掴んでしまう。検索前に必ず結果欄を空にする。
    page.evaluate(
        """(sel) => {
            const c = document.querySelector(sel);
            if (c) c.innerHTML = '';
        }""",
        SEARCH_RESULT_CONTAINER,
    )
    box = page.locator(SEARCH_FORM_INPUT)
    box.click()
    box.fill(guild_name)
    if not js_click(page, SEARCH_FORM_SUBMIT):
        raise RuntimeError(f"検索ボタン（{SEARCH_FORM_SUBMIT}）が見つかりません。")

    start = time.monotonic()
    try:
        page.wait_for_function(
            """(sel) => {
                const c = document.querySelector(sel);
                if (!c) return false;
                const t = c.innerText || '';
                return t.includes('Guild Results:')
                    || t.includes('Player Results:')
                    || t.toLowerCase().includes('not found');
            }""",
            arg=SEARCH_RESULT_CONTAINER,
            timeout=SEARCH_RESULT_TIMEOUT_MS,
        )
    except Exception as exc:
        raise RuntimeError(
            f"'{guild_name}' の検索結果が{SEARCH_RESULT_TIMEOUT_MS // 1000}秒以内に返りませんでした。"
        ) from exc
    print(f"  検索結果を取得（{time.monotonic() - start:.1f}秒）")


def find_exact_guild_result(page: Page, guild_name: str) -> Locator | None:
    """Guild Results の中から完全一致のギルド名だけを返す。

    'TRAITORs' の検索で 'TRAITORsJP' も返るなど部分一致が混ざるため、
    完全一致（次点で大文字小文字を無視した一致）以外はクリックしない。
    """
    spans = page.locator(SEARCH_RESULT_GUILD_SPANS)
    try:
        count = spans.count()
    except Exception:
        return None

    names: List[str] = []
    fallback: Locator | None = None
    for i in range(count):
        span = spans.nth(i)
        try:
            name = span.inner_text().strip()
        except Exception:
            continue
        names.append(name)
        if name == guild_name:
            print(f"  完全一致: {name}")
            return span
        if fallback is None and name.casefold() == guild_name.casefold():
            fallback = span

    if fallback is not None:
        print(f"  大文字小文字を無視して一致: {guild_name}")
        return fallback
    print(f"  完全一致なし。候補: {names if names else '（Guild Results なし）'}")
    return None


def open_guild_detail(page: Page, span: Locator, guild_name: str) -> None:
    """検索結果のギルド名をクリックし、詳細画面が出るまで待つ。"""
    start = time.monotonic()
    span.evaluate("el => el.click()")
    try:
        page.wait_for_function(DETAIL_READY_JS, timeout=DETAIL_LOAD_TIMEOUT_MS)
    except Exception as exc:
        raise RuntimeError(
            f"'{guild_name}' の詳細画面が{DETAIL_LOAD_TIMEOUT_MS // 1000}秒以内に開きませんでした。"
        ) from exc
    print(f"  詳細画面を表示（{time.monotonic() - start:.1f}秒）")


def dump_search_debug(page: Page, guild_name: str) -> None:
    """完全一致が見つからなかったときの調査用ダンプ。"""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        stem = f"debug_search_{sanitize_filename(guild_name)}"
        page.screenshot(path=str(DATA_DIR / f"{stem}.png"), full_page=True)
        (DATA_DIR / f"{stem}.txt").write_text(
            page.locator("body").inner_text(), encoding="utf-8"
        )
    except Exception:
        pass


def get_scroll_metrics(page: Page) -> tuple[int, int]:
    return page.evaluate("() => [Math.round(window.scrollY), Math.round(document.body.scrollHeight)]")


def wait_for_active_members_ready(page: Page) -> None:
    start = time.monotonic()
    next_log = start + 5
    while time.monotonic() - start < GUILD_LOAD_TIMEOUT_SECONDS:
        # 全文転送を避け、必要な2フラグだけをブラウザ側で計算して受け取る（判定は同一）。
        state = page.evaluate(
            """() => {
                const body = document.body.innerText;
                return {
                    loading: body.includes('Loading Data'),
                    active: body.includes('Active Guild Members'),
                };
            }"""
        )
        has_loading = state["loading"]
        has_active_members = state["active"]
        if not has_loading and has_active_members:
            print("Active Guild Members の表示を確認しました。")
            return
        if time.monotonic() >= next_log:
            elapsed = int(time.monotonic() - start)
            print(
                f"ギルドページ読み込み待機中... ({elapsed}秒経過, "
                f"loading={'ON' if has_loading else 'OFF'}, "
                f"active_members={'ON' if has_active_members else 'OFF'})"
            )
            next_log += 5
        time.sleep(POLL_INTERVAL_SECONDS)
    raise RuntimeError(
        f"{GUILD_LOAD_TIMEOUT_SECONDS}秒以内に Active Guild Members が表示されませんでした。"
    )


def has_member_signals(page: Page) -> bool:
    # extract_active_members_text と同じ切り出し条件をブラウザ側で評価し、
    # スクロール毎の body.inner_text() 全文転送を避ける。
    return bool(
        page.evaluate(
            """() => {
                const body = document.body.innerText;
                const start = body.indexOf('Active Guild Members');
                if (start === -1) return false;
                let section = body.slice(start);
                const end = section.indexOf('Guild Member History');
                if (end !== -1) section = section.slice(0, end);
                return section.includes('CPM')
                    || section.includes('FCP')
                    || section.includes('Lv');
            }"""
        )
    )


def scroll_until_member_section(page: Page) -> None:
    start = time.monotonic()
    next_log = start + 5
    while time.monotonic() - start < RESULT_TIMEOUT_SECONDS:
        y, h = get_scroll_metrics(page)
        print(f"スクロール位置: y={y}, page_height={h}")
        if has_member_signals(page):
            print("メンバー候補要素(CPM/FCP/Lv/arrow_upward)を検出しました。")
            return
        page.mouse.wheel(0, 800)
        page.wait_for_timeout(500)
        if time.monotonic() >= next_log:
            print(f"メンバー一覧待機中... ({int(time.monotonic()-start)}秒経過)")
            next_log += 5
    raise RuntimeError(
        f"{RESULT_TIMEOUT_SECONDS}秒以内にメンバー一覧候補を検出できませんでした。"
    )


def collect_horizontal_texts(page: Page) -> List[str]:
    script = """
() => {
  const results = [];
  const seen = new Set();
  const nodes = Array.from(document.querySelectorAll('*')).filter(el => el.scrollWidth > el.clientWidth + 20);
  for (const el of nodes) {
    const old = el.scrollLeft;
    const max = el.scrollWidth - el.clientWidth;
    const steps = [0, Math.floor(max*0.33), Math.floor(max*0.66), max];
    for (const s of steps) {
      el.scrollLeft = s;
      const txt = (el.innerText || '').trim();
      if (txt && !seen.has(txt)) { seen.add(txt); results.push(txt); }
    }
    el.scrollLeft = old;
  }
  return results;
}
"""
    return page.evaluate(script)


def dump_guild_debug_files(page: Page) -> None:
    if not DEBUG_SAVE_FILES:
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(DATA_DIR / "debug_guild_page.png"), full_page=True)
    body_text = page.locator("body").inner_text()
    (DATA_DIR / "debug_guild_page_text.txt").write_text(body_text, encoding="utf-8")

    blocks = page.locator(
        ":is(div,li,article,section,span,p):has-text('CPM'), "
        ":is(div,li,article,section,span,p):has-text('FCP'), "
        ":is(div,li,article,section,span,p):has-text('Lv')"
    )
    lines: List[str] = []
    for i in range(min(blocks.count(), 300)):
        t = blocks.nth(i).inner_text().strip()
        if t:
            lines.append(f"[{i+1}] {t}")
    for t in collect_horizontal_texts(page):
        if "CPM" in t or "FCP" in t or "Lv" in t:
            lines.append("[H] " + t)
    (DATA_DIR / "debug_member_blocks.txt").write_text(
        "\n\n".join(lines) if lines else "(no blocks)", encoding="utf-8"
    )


def parse_node_history_rows(body_text: str) -> List[dict[str, str]]:
    section = ""
    start = body_text.find("Node & Siege War History")
    if start != -1:
        section = body_text[start:]
        end = section.find("Guild War History")
        if end != -1:
            section = section[:end]
    if not section:
        return []

    lines = [x.strip() for x in section.splitlines() if x.strip()]
    rows: List[dict[str, str]] = []
    i = 0
    date_pattern = re.compile(r"^(\d{1,2}\s+[A-Za-z]+\s+\d{4})")
    while i < len(lines):
        m = date_pattern.match(lines[i])
        if not m:
            i += 1
            continue
        date = m.group(1)
        content_name = lines[i + 1] if i + 1 < len(lines) else ""
        result = ""
        if i + 2 < len(lines):
            rm = re.search(r"\b(Win|Lost)\b", lines[i + 2], re.I)
            if rm:
                result = rm.group(1)
        if date and content_name and result:
            rows.append({"date": date, "content_name": content_name, "result": result})
            i += 3
            continue
        i += 1
    return rows


def extract_active_members_text(text: str) -> str:
    start_marker = "Active Guild Members"
    end_marker = "Guild Member History"
    start_idx = text.find(start_marker)
    if start_idx == -1:
        return ""
    section = text[start_idx:]
    end_idx = section.find(end_marker)
    if end_idx != -1:
        section = section[:end_idx]
    return section


def parse_members_from_blocks(text: str) -> List[MemberRow]:
    retrieved_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    members: List[MemberRow] = []
    active_text = extract_active_members_text(text)
    if not active_text:
        return members

    lines = [ln for ln in active_text.splitlines() if "fmd_bad" not in ln]
    cleaned = "\n".join(lines)
    blocks = re.split(r"(?m)^\s*(?=\d+\.)", cleaned)
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        block_lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if not block_lines:
            continue
        m_head = re.match(r"^(\d+)\.\s*(.+)$", block_lines[0])
        if not m_head:
            continue
        rank = m_head.group(1).strip()
        player_name = m_head.group(2).strip()
        level = ""
        cpm = ""
        fcp = ""
        for ln in block_lines[1:]:
            if not level:
                m = re.search(r"Lv\s*:?\s*(\d+)", ln, re.I)
                if m:
                    level = m.group(1)
            if not cpm:
                m = re.search(r"CPM\s*:?\s*([\d,]+)", ln, re.I)
                if m:
                    cpm = re.sub(r"\D", "", m.group(1))
            if fcp == "":
                m = re.search(r"FCP\s*:?\s*([\d,]+)", ln, re.I)
                if m:
                    fcp = re.sub(r"\D", "", m.group(1))
        if player_name and level and cpm and fcp != "":
            members.append(MemberRow(rank, player_name, level, cpm, fcp, retrieved_at))

    uniq_by_rank: dict[str, MemberRow] = {}
    for member in members:
        uniq_by_rank[member.rank] = member
    return list(uniq_by_rank.values())


def parse_summary_metrics(body_text: str) -> dict[str, str]:
    """ギルド概要メトリクスを body.inner_text() から抽出する。"""

    def section_between(text: str, start: str, end_markers: list[str]) -> str:
        start_idx = text.find(start)
        if start_idx == -1:
            return ""
        part = text[start_idx:]
        end_idx = len(part)
        for marker in end_markers:
            idx = part.find(marker)
            if idx != -1:
                end_idx = min(end_idx, idx)
        return part[:end_idx]

    def norm_num(v: str) -> str:
        return re.sub(r"[^0-9]", "", v)

    combat = section_between(body_text, "Combat Power", ["Guild War Activity", "Node War Activity"])
    guild_war = section_between(
        body_text, "Guild War Activity", ["Node War Activity", "Node & Siege War History"]
    )
    node_war = section_between(
        body_text,
        "Node War Activity",
        ["Known Guild Names", "CP Distributions", "Node & Siege War History"],
    )

    def pick_after_label(section: str, label: str) -> str:
        m_inline = re.search(re.escape(label) + r"\s*:?\s*([^\n]+)", section, re.I)
        if m_inline:
            value = m_inline.group(1).strip()
            if value and value.lower() != label.lower():
                return value
        lines = [ln.strip() for ln in section.splitlines() if ln.strip()]
        for i, ln in enumerate(lines):
            if re.fullmatch(re.escape(label), ln, re.I) and i + 1 < len(lines):
                return lines[i + 1]
        return ""

    low_member_cp = ""
    high_member_cp = ""
    lines = [ln.strip() for ln in combat.splitlines() if ln.strip()]
    for i, ln in enumerate(lines):
        if re.fullmatch(r"ACTIVE LOW & HIGH POINT MEMBER CP", ln, re.I):
            look = lines[i + 1 : i + 8]
            for v in look:
                m = re.search(r"([\d,]+)\s*\((L|H)\)", v, re.I)
                if not m:
                    continue
                if m.group(2).upper() == "L":
                    low_member_cp = norm_num(m.group(1))
                else:
                    high_member_cp = norm_num(m.group(1))
            break

    return {
        "avg_cp": norm_num(pick_after_label(combat, "AVERAGE CP FROM ACTIVE MEMBER")),
        "total_cp": norm_num(pick_after_label(combat, "TOTAL CP FROM ACTIVE MEMBER")),
        "total_family_cp": norm_num(
            pick_after_label(combat, "TOTAL FAMILY CP FROM ACTIVE MEMBER")
        ),
        "active_member_count": norm_num(
            pick_after_label(combat, "ACTIVE MEMBER - EXCLUDE MEMBERS WHO MOSTLY BSM")
        ),
        "low_member_cp": low_member_cp,
        "high_member_cp": high_member_cp,
        "declared_on_other_guild": norm_num(
            pick_after_label(guild_war, "DECLARE ON OTHER GUILD")
        ),
        "declared_by_other_guild": norm_num(
            pick_after_label(guild_war, "DECLARED BY OTHER GUILD")
        ),
        "total_war": norm_num(pick_after_label(guild_war, "TOTAL WAR")),
        "all_time_win_rate": pick_after_label(guild_war, "ALL TIME WIN RATE").strip(),
        "most_war_with_guild": pick_after_label(guild_war, "MOST WAR WITH GUILD").strip(),
        "total_node_wars": norm_num(pick_after_label(node_war, "TOTAL NODE WARS")),
        "node_won": norm_num(pick_after_label(node_war, "NODE WON")),
        "total_siege_wars": norm_num(pick_after_label(node_war, "TOTAL SIEGE WARS")),
        "siege_won": norm_num(pick_after_label(node_war, "SIEGE WON")),
        "currently_holding": pick_after_label(node_war, "CURRENTLY HOLDING").strip(),
    }


def save_summary_to_csv(guild_name: str, body_text: str) -> Path:
    today = datetime.now().strftime("%Y-%m-%d")
    retrieved_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    safe_guild_name = sanitize_filename(guild_name)
    file_path = get_guild_data_dir(guild_name) / f"summary_{safe_guild_name}_{today}.csv"
    metrics = parse_summary_metrics(body_text)
    row = {"guild_name": guild_name, "retrieved_at": retrieved_at, **metrics}
    cols = get_summary_columns()
    with file_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerow(row)
    return file_path


def save_node_history_to_csv(guild_name: str, body_text: str) -> Path:
    today = datetime.now().strftime("%Y-%m-%d")
    retrieved_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    safe_guild_name = sanitize_filename(guild_name)
    file_path = get_guild_data_dir(guild_name) / f"node_history_{safe_guild_name}_{today}.csv"
    rows = [
        {
            "guild_name": guild_name,
            "retrieved_at": retrieved_at,
            "date": row["date"],
            "content_name": row["content_name"],
            "result": row["result"],
        }
        for row in parse_node_history_rows(body_text)
    ]
    cols = ["guild_name", "retrieved_at", "date", "content_name", "result"]
    with file_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for row in rows:
            w.writerow(row)
    return file_path


def get_summary_columns() -> list[str]:
    return [
        "guild_name",
        "retrieved_at",
        "avg_cp",
        "total_cp",
        "total_family_cp",
        "active_member_count",
        "low_member_cp",
        "high_member_cp",
        "declared_on_other_guild",
        "declared_by_other_guild",
        "total_war",
        "all_time_win_rate",
        "most_war_with_guild",
        "total_node_wars",
        "node_won",
        "total_siege_wars",
        "siege_won",
        "currently_holding",
    ]


def save_guild_workbook(guild_name: str, members: List[MemberRow], body_text: str) -> Path:
    from openpyxl import Workbook

    today = datetime.now().strftime("%Y-%m-%d")
    retrieved_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    safe_guild_name = sanitize_filename(guild_name)
    guild_dir = get_guild_data_dir(guild_name)
    file_path = guild_dir / f"guild_{safe_guild_name}_{today}.xlsx"

    wb = Workbook()
    ws_members = wb.active
    ws_members.title = "members"
    ws_members.append(["rank", "player_name", "level", "cpm", "fcp", "retrieved_at"])
    for m in members:
        ws_members.append([m.rank, m.player_name, m.level, m.cpm, m.fcp, m.retrieved_at])

    ws_summary = wb.create_sheet("summary")
    summary_cols = get_summary_columns()
    ws_summary.append(summary_cols)
    metrics = parse_summary_metrics(body_text)
    ws_summary.append([guild_name, retrieved_at] + [metrics.get(c, "") for c in summary_cols[2:]])

    ws_node = wb.create_sheet("node_history")
    ws_node.append(["guild_name", "retrieved_at", "date", "content_name", "result"])
    for row in parse_node_history_rows(body_text):
        ws_node.append([guild_name, retrieved_at, row["date"], row["content_name"], row["result"]])

    wb.save(file_path)
    return file_path


def save_guild_snapshot_to_sqlite(
    guild_name: str, members: List[MemberRow], body_text: str
) -> bool:
    """Save scraper results to SQLite without affecting Excel success."""

    retrieved_at = members[0].retrieved_at if members else datetime.now().strftime("%Y-%m-%d %H:%M")
    member_rows = [
        {
            "rank_no": m.rank,
            "family_name": m.player_name,
            "level": m.level,
            "cpm": m.cpm,
            "fcp": m.fcp,
            "class_name": None,
            "class_name_raw": None,
            "class_name_normalized": None,
            "class_name_version": None,
        }
        for m in members
    ]
    node_history = [
        {
            "row_no": index,
            "war_date": row.get("date", ""),
            "node_name": row.get("content_name", ""),
            "opponent_guild": "",
            "result": row.get("result", ""),
            "raw": row,
        }
        for index, row in enumerate(parse_node_history_rows(body_text), start=1)
    ]
    summary = parse_summary_metrics(body_text)
    try:
        conn = db.initialize()
        try:
            db.save_snapshot(
                conn,
                retrieved_at=retrieved_at,
                guild_name=guild_name,
                members=member_rows,
                member_count=len(member_rows),
                node_history=node_history,
                summary=summary,
            )
        finally:
            conn.close()
    except Exception as exc:
        print(f"⚠ SQLite保存に失敗しました（Excelは作成済みです）: {exc}")
        return False
    print(f"SQLite保存完了: {db.DEFAULT_DB_PATH}")
    return True


def save_scraped_data_to_sqlite(
    *,
    retrieved_at: str,
    guild_name: str,
    members: list[dict[str, object]],
    db_path: str | Path | None = None,
    member_count: int | None = None,
    avg_cpm: float | None = None,
    total_cpm: float | None = None,
    node_history: list[dict[str, object]] | None = None,
    summary: dict[str, object] | None = None,
) -> bool:
    """Compatibility helper for callers that already have dict rows."""

    try:
        conn = db.initialize(db_path)
        try:
            db.save_snapshot(
                conn,
                retrieved_at=retrieved_at,
                guild_name=guild_name,
                members=members,
                member_count=member_count,
                avg_cpm=avg_cpm,
                total_cpm=total_cpm,
                node_history=node_history,
                summary=summary,
            )
        finally:
            conn.close()
    except Exception as exc:
        print(f"⚠ SQLite保存に失敗しました（Excelは作成済みです）: {exc}")
        return False
    return True


def parse_expected_active_member_count(text: str) -> int | None:
    patterns = [
        r"ACTIVE MEMBER - EXCLUDE MEMBERS WHO MOSTLY BSM\s*:?\s*(\d+)",
        r"ACTIVE MEMBER\s*:?\s*(\d+)",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m:
            return int(m.group(1))
    return None


def scrape_members_from_guild_page(page: Page) -> List[MemberRow]:
    wait_for_active_members_ready(page)
    scroll_until_member_section(page)
    y, h = get_scroll_metrics(page)
    print(f"取得前スクロール位置: y={y}, page_height={h}")
    dump_guild_debug_files(page)
    body_text = page.locator("body").inner_text()
    active_text = extract_active_members_text(body_text)
    if not active_text:
        print("Active Guild Members セクションの切り出しに失敗しました。")
    members = parse_members_from_blocks(body_text)
    if not members:
        raise RuntimeError("ギルドページでメンバー情報を取得できませんでした。debug_member_blocks.txt を確認してください。")
    return members


def save_members_to_csv(guild_name: str, members: List[MemberRow]) -> Path:
    today = datetime.now().strftime("%Y-%m-%d")
    safe_guild_name = sanitize_filename(guild_name)
    file_path = get_guild_data_dir(guild_name) / f"members_{safe_guild_name}_{today}.csv"
    with file_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["rank", "player_name", "level", "cpm", "fcp", "retrieved_at"])
        for m in members:
            w.writerow([m.rank, m.player_name, m.level, m.cpm, m.fcp, m.retrieved_at])
    return file_path


def search_and_open_guild(page: Page, guild_name: str) -> None:
    """Name Search でギルドを検索し、完全一致の 1 件だけを開く。"""
    close_general_chat_panel(page)
    open_search_page(page)
    run_name_search(page, guild_name)
    span = find_exact_guild_result(page, guild_name)
    if span is None:
        dump_search_debug(page, guild_name)
        raise RuntimeError(
            f"Name Search で '{guild_name}' に完全一致するギルドが見つかりませんでした。"
        )
    open_guild_detail(page, span, guild_name)


def main() -> int:
    from playwright.sync_api import (
        TimeoutError as PlaywrightTimeoutError,
        sync_playwright,
    )

    # GUI(app.py)から QProcess 経由で起動された場合、stdout はパイプになり
    # ブロックバッファリングのせいでログが最後にまとめて出てしまう。
    # 行バッファリングにして、ギルドごとの進捗をリアルタイムに表示する。
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass

    ensure_dirs()
    load_project_env(ENV_PATH)

    session = session_state.get_or_create_session()
    print(f"収集セッション: {session['session_date']}（このセッションに追記します）")

    try:
        guilds = load_guild_names(CONFIG_PATH)
    except Exception as e:
        print(f"❌ 初期化エラー: {e}")
        return 1

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto(DBONK_LOGIN_URL, wait_until="domcontentloaded")
        try_auto_login(page)
        try_select_asia_server(page)
        page.wait_for_timeout(1000)
        total, ok = len(guilds), 0
        succeeded: list[tuple[str, int]] = []
        failed: list[tuple[str, str]] = []
        try:
            for i, guild in enumerate(guilds, 1):
                print("")
                print(f"▶ [{i}/{total}] 「{guild}」 取得開始")
                try:
                    search_and_open_guild(page, guild)
                    members = scrape_members_from_guild_page(page)
                    body_text = page.locator("body").inner_text()
                    workbook_path = save_guild_workbook(guild, members, body_text)
                    session_state.record_guild(guild, workbook_path)
                    save_guild_snapshot_to_sqlite(guild, members, body_text)
                    print(f"  取得メンバー数: {len(members)}人")
                    expected_count = parse_expected_active_member_count(body_text)
                    if expected_count is not None and expected_count != len(members):
                        print(
                            f"  ⚠ 表示上のActive Memberは{expected_count}人ですが、"
                            f"取得は{len(members)}人です"
                        )
                    ok += 1
                    succeeded.append((guild, len(members)))
                    print(
                        f"✅ [{i}/{total}] 「{guild}」 取得成功（{len(members)}人） "
                        f"-> {workbook_path}"
                    )
                except (PlaywrightTimeoutError, RuntimeError) as e:
                    failed.append((guild, str(e)))
                    print(f"❌ [{i}/{total}] 「{guild}」 取得失敗: {e}")
                except Exception as e:
                    failed.append((guild, f"想定外エラー: {e}"))
                    print(f"❌ [{i}/{total}] 「{guild}」 想定外エラー: {e}")

            print("")
            print("========== 取得結果サマリー ==========")
            print(f"成功: {ok}/{total} ギルド")
            if succeeded:
                print(f"✅ 成功したギルド ({len(succeeded)}):")
                for name, count in succeeded:
                    print(f"   - {name}（{count}人）")
            if failed:
                print(f"❌ 失敗したギルド ({len(failed)}):")
                for name, reason in failed:
                    print(f"   - {name}: {reason}")
            else:
                print("❌ 失敗したギルド: なし")
            print("=====================================")
            return 0
        finally:
            # コンソールから直接実行したときだけ Enter 待ちにする。
            # GUI(app.py)は QProcess 経由で起動し stdin が無いため、
            # 無条件に input() するとここで永久にブロックし「実行中」のまま固まる。
            if sys.stdin is not None and sys.stdin.isatty():
                input("確認したらEnterで終了")
            ctx.close()
            browser.close()


if __name__ == "__main__":
    sys.exit(main())
