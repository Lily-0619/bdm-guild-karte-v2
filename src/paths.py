"""Project-root based paths for the BDM Guild Karte tools."""

from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
DATA_DIR = PROJECT_ROOT / "data"
ANALYSIS_DIR = PROJECT_ROOT / "analysis"
AUTOCOMMENT_DIR = ANALYSIS_DIR / "autocomment_materials"
DETA_PV_DIR = PROJECT_ROOT / "deta_PV"
OUTPUT_DIR = PROJECT_ROOT / "output"
CARDS_DIR = OUTPUT_DIR / "cards"
CONFIG_DIR = PROJECT_ROOT / "config"
UI_DIR = PROJECT_ROOT / "ui"
ASSETS_DIR = UI_DIR / "assets"
DB_PATH = DATA_DIR / "bdm_guild.sqlite3"
PV_TEMPLATE_PATH = PROJECT_ROOT / "template" / "pv_karute.xlsx"
BACKDESIGN_PATH = ASSETS_DIR / "backdesign.png"
APP_ICON_PATH = ASSETS_DIR / "cat_elf_app_icon.ico"


def ensure_dirs() -> None:
    """Create the standard writable project folders when they are missing."""

    for directory in (
        DATA_DIR,
        ANALYSIS_DIR,
        AUTOCOMMENT_DIR,
        DETA_PV_DIR,
        OUTPUT_DIR,
        CARDS_DIR,
        CONFIG_DIR,
    ):
        directory.mkdir(parents=True, exist_ok=True)
