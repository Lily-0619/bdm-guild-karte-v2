"""SQLite-backed person browser embedded in the desktop application."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

try:
    from . import db
    from .paths import DB_PATH
except ImportError:  # pragma: no cover - direct execution compatibility
    import db  # type: ignore
    from paths import DB_PATH  # type: ignore


PERSON_COLUMNS = ["person_id", "家門名", "所属", "CPM", "FCP", "レベル", "職業", "最終取得"]
HISTORY_COLUMNS = ["取得日時", "家門名", "所属", "CPM", "FCP", "レベル", "職業"]


class PersonDataDialog(QDialog):
    """Browse people and their full history without opening Excel files."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("個人データ（SQLite）")
        self.resize(1250, 760)
        self._people: list[dict] = []

        root = QVBoxLayout(self)
        title_row = QHBoxLayout()
        title_row.addWidget(QLabel("個人データ"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("家門名・ギルド名・person_idで検索")
        self.search.textChanged.connect(self.apply_filter)
        title_row.addWidget(self.search, 1)
        root.addLayout(title_row)

        splitter = QSplitter(Qt.Orientation.Vertical)
        self.people_table = QTableWidget(0, len(PERSON_COLUMNS))
        self.people_table.setHorizontalHeaderLabels(PERSON_COLUMNS)
        self.people_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.people_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.people_table.itemSelectionChanged.connect(self.load_selected_history)
        splitter.addWidget(self.people_table)

        self.history_table = QTableWidget(0, len(HISTORY_COLUMNS))
        self.history_table.setHorizontalHeaderLabels(HISTORY_COLUMNS)
        self.history_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        splitter.addWidget(self.history_table)
        splitter.setSizes([390, 300])
        root.addWidget(splitter, 1)
        self.load_people()

    def load_people(self) -> None:
        with db.initialize(DB_PATH) as conn:
            rows = conn.execute(
                """
                SELECT p.id AS person_id, p.canonical_family_name,
                       m.guild_name, m.cpm, m.fcp, m.level,
                       COALESCE(m.class_name_normalized, m.class_name, m.class_name_raw) AS class_name,
                       m.retrieved_at
                FROM persons p
                LEFT JOIN member_snapshots m ON m.id = (
                    SELECT latest.id FROM member_snapshots latest
                    WHERE latest.person_id = p.id
                    ORDER BY latest.retrieved_at DESC, latest.id DESC LIMIT 1
                )
                ORDER BY p.canonical_family_name, p.id
                """
            ).fetchall()
        self._people = [dict(row) for row in rows]
        self.apply_filter()

    def apply_filter(self) -> None:
        needle = self.search.text().strip().casefold()
        rows = [
            row
            for row in self._people
            if not needle
            or needle in str(row.get("person_id", "")).casefold()
            or needle in str(row.get("canonical_family_name", "")).casefold()
            or needle in str(row.get("guild_name", "")).casefold()
        ]
        self.people_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            values = [
                row.get("person_id"), row.get("canonical_family_name"), row.get("guild_name"),
                row.get("cpm"), row.get("fcp"), row.get("level"), row.get("class_name"),
                row.get("retrieved_at"),
            ]
            for column, value in enumerate(values):
                self.people_table.setItem(row_index, column, QTableWidgetItem("" if value is None else str(value)))
        self.people_table.resizeColumnsToContents()
        self.history_table.setRowCount(0)

    def load_selected_history(self) -> None:
        row_index = self.people_table.currentRow()
        item = self.people_table.item(row_index, 0) if row_index >= 0 else None
        if item is None:
            return
        person_id = int(item.text())
        with db.initialize(DB_PATH) as conn:
            rows = db.person_history(conn, person_id)
        self.history_table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            values = [
                row["retrieved_at"], row["family_name"], row["guild_name"], row["cpm"],
                row["fcp"], row["level"],
                row["class_name_normalized"] or row["class_name"] or row["class_name_raw"],
            ]
            for column, value in enumerate(values):
                self.history_table.setItem(index, column, QTableWidgetItem("" if value is None else str(value)))
        self.history_table.resizeColumnsToContents()

