from __future__ import annotations

import subprocess
import sys

from PySide6.QtCore import QProcess, QProcessEnvironment, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QIcon, QPainter, QPixmap, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

try:
    from .paths import (
        APP_ICON_PATH,
        BACKDESIGN_PATH,
        CONFIG_DIR,
        PROJECT_ROOT,
        SRC_DIR,
        ensure_dirs,
    )
except ImportError:  # 直接実行された場合のため
    from paths import (  # type: ignore
        APP_ICON_PATH,
        BACKDESIGN_PATH,
        CONFIG_DIR,
        PROJECT_ROOT,
        SRC_DIR,
        ensure_dirs,
    )

CARD_GUILDS_FILE = CONFIG_DIR / "card_guilds.txt"
GUILDS_FILE = CONFIG_DIR / "guilds.txt"
BACKGROUND_IMAGE = BACKDESIGN_PATH
APP_ICON = APP_ICON_PATH
VENV_PYTHON = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"


class BackgroundWidget(QWidget):
    def __init__(self, image_path: Path) -> None:
        super().__init__()
        self.background = QPixmap(str(image_path)) if image_path.exists() else QPixmap()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        if not self.background.isNull():
            painter.drawPixmap(self.rect(), self.background)
        else:
            painter.fillRect(self.rect(), Qt.GlobalColor.white)
        super().paintEvent(event)


class LauncherWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.process: QProcess | None = None
        self.run_queue: list[tuple[str, Path]] = []

        self.setWindowTitle("BDM Guild Karte Tool")
        if APP_ICON.exists():
            self.setWindowIcon(QIcon(str(APP_ICON)))
        self.resize(1280, 760)
        self.setMinimumSize(1040, 640)

        self.run_buttons: list[QPushButton] = []
        self._build_ui()
        self.load_config_files()

    def _build_ui(self) -> None:
        root = BackgroundWidget(BACKGROUND_IMAGE)
        root.setObjectName("root")
        self.setCentralWidget(root)

        main_layout = QVBoxLayout(root)
        main_layout.setContentsMargins(28, 24, 28, 26)
        main_layout.setSpacing(16)

        header_layout = QHBoxLayout()
        header_layout.setSpacing(16)
        main_layout.addLayout(header_layout)

        title = QLabel("BDM Guild Karte Tool")
        title.setObjectName("title")
        title.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header_layout.addWidget(title)
        header_layout.addStretch(1)

        self.pv_detail_button = self._make_run_button("個人カルテへ")
        self.pv_detail_button.setObjectName("topActionButton")
        self.pv_detail_button.setMinimumWidth(150)
        self.pv_detail_button.clicked.connect(self.launch_pv_detail_app)
        header_layout.addWidget(self.pv_detail_button, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.analyze_button = self._make_run_button("一覧作成")
        self.analyze_button.setObjectName("topActionButton")
        self.analyze_button.setMinimumWidth(150)
        self.analyze_button.clicked.connect(
            lambda: self.run_scripts([("一覧作成", SRC_DIR / "analyze.py")])
        )
        header_layout.addWidget(self.analyze_button, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("progressBar")
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        main_layout.addWidget(self.progress_bar)

        columns_layout = QHBoxLayout()
        columns_layout.setSpacing(18)
        main_layout.addLayout(columns_layout, 1)

        log_card = self._make_card("blueCard")
        log_layout = QVBoxLayout(log_card)
        log_layout.setContentsMargins(20, 18, 20, 20)
        log_layout.setSpacing(12)

        log_header = QHBoxLayout()
        log_title = QLabel("ログ")
        log_title.setObjectName("cardTitle")
        log_header.addWidget(log_title)
        log_header.addStretch(1)
        self.clear_button = QPushButton("ログをクリア")
        self.clear_button.setObjectName("smallButton")
        self.clear_button.clicked.connect(self.clear_log)
        log_header.addWidget(self.clear_button)
        log_layout.addLayout(log_header)

        self.log_view = QTextEdit()
        self.log_view.setObjectName("logView")
        self.log_view.setReadOnly(True)
        self.log_view.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.log_view.setFont(QFont("Consolas", 10))
        self.log_view.setPlaceholderText("ここに実行ログが表示されます。")
        self.log_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        log_layout.addWidget(self.log_view, 1)

        card_guilds_card = self._make_editor_card(title="カルテ作成リスト")
        self.make_card_button = card_guilds_card.findChild(QPushButton, "headerButton")
        self.card_guilds_editor = card_guilds_card.findChild(QTextEdit, "editor")
        self.save_card_guilds_button = card_guilds_card.findChild(QPushButton, "saveButton")
        self.make_card_button.clicked.connect(
            lambda: self.save_then_run(
                CARD_GUILDS_FILE,
                self.card_guilds_editor,
                [("カルテ作成", SRC_DIR / "make_card.py")],
            )
        )
        self.save_card_guilds_button.setText("カルテ作成リストを保存")
        self.save_card_guilds_button.clicked.connect(
            lambda: self.save_config_file(CARD_GUILDS_FILE, self.card_guilds_editor)
        )

        guilds_card = self._make_editor_card(title="データ取得")
        self.scraper_button = guilds_card.findChild(QPushButton, "headerButton")
        self.guilds_editor = guilds_card.findChild(QTextEdit, "editor")
        self.save_guilds_button = guilds_card.findChild(QPushButton, "saveButton")
        self.scraper_button.clicked.connect(
            lambda: self.save_then_run(
                GUILDS_FILE,
                self.guilds_editor,
                [("DBonkデータ取得", SRC_DIR / "scraper.py")],
            )
        )
        self.save_guilds_button.setText("データ取得リストを保存")
        self.save_guilds_button.clicked.connect(
            lambda: self.save_config_file(GUILDS_FILE, self.guilds_editor)
        )

        columns_layout.addWidget(log_card, 1)
        columns_layout.addWidget(card_guilds_card, 1)
        columns_layout.addWidget(guilds_card, 1)

        self.statusBar().showMessage(f"Project: {PROJECT_ROOT}")
        self._apply_styles()

    def launch_pv_detail_app(self) -> None:
        started = QProcess.startDetached(
            sys.executable,
            ["-m", "src.pv_detail_app"],
            str(PROJECT_ROOT),
        )
        if started:
            self.append_log(f"[START] 個人カルテへ: {sys.executable} -m src.pv_detail_app")
            self.statusBar().showMessage("詳細分析システムを別ウィンドウで起動しました。")
        else:
            self.append_log("[ERROR] 詳細分析システムを起動できませんでした。")
            QMessageBox.warning(self, "起動エラー", "詳細分析システムを起動できませんでした。")

    def _make_card(self, object_name: str) -> QFrame:
        card = QFrame()
        card.setObjectName(object_name)
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        return card

    def _make_editor_card(self, title: str) -> QFrame:
        card = self._make_card("glassCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 20)
        layout.setSpacing(12)

        header_button = self._make_run_button(title)
        header_button.setObjectName("headerButton")
        header_button.setMinimumHeight(68)
        layout.addWidget(header_button)

        editor = QTextEdit()
        editor.setObjectName("editor")
        editor.setAcceptRichText(False)
        editor.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        editor.setFont(QFont("Consolas", 10))
        editor.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(editor, 1)

        save_button = QPushButton()
        save_button.setObjectName("saveButton")
        save_button.setMinimumHeight(42)
        save_button.setCursor(Qt.CursorShape.PointingHandCursor)
        layout.addWidget(save_button)

        return card

    def _make_run_button(self, text: str) -> QPushButton:
        button = QPushButton(text)
        button.setMinimumHeight(40)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.run_buttons.append(button)
        return button

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            """
            QWidget#root {
                color: #4A3B45;
                font-family: "Segoe UI", "Yu Gothic UI", "Meiryo", sans-serif;
                font-size: 14px;
            }
            QLabel#title {
                color: #4A3B45;
                font-size: 30px;
                font-weight: 800;
                padding: 4px 10px;
            }
            QFrame#glassCard,
            QFrame#blueCard {
                border: 1px solid rgba(232, 168, 200, 160);
                border-radius: 24px;
            }
            QFrame#glassCard { background-color: rgba(255, 255, 255, 190); }
            QFrame#blueCard { background-color: rgba(218, 247, 246, 185); }
            QLabel#cardTitle {
                color: #4A3B45;
                font-size: 18px;
                font-weight: 800;
            }
            QPushButton#headerButton {
                background-color: rgba(232, 168, 200, 220);
                color: #4A3B45;
                border: 1px solid rgba(217, 140, 179, 150);
                border-radius: 22px;
                font-size: 17px;
                font-weight: 800;
                padding: 9px 16px;
            }
            QPushButton#headerButton:hover { background-color: rgba(217, 140, 179, 230); }
            QPushButton#headerButton:pressed { background-color: rgba(201, 119, 162, 235); }
            QPushButton {
                background: #E8A8C8;
                color: #4A3B45;
                border: 1px solid rgba(217, 140, 179, 210);
                border-radius: 16px;
                padding: 8px 14px;
                font-weight: 800;
            }
            QPushButton:hover { background: #D98CB3; }
            QPushButton:pressed { background: #C977A2; }
            QPushButton:disabled {
                background: rgba(242, 222, 232, 190);
                color: #9C8994;
                border-color: rgba(232, 209, 221, 180);
            }
            QPushButton#smallButton {
                background: rgba(255, 255, 255, 190);
                border-radius: 14px;
                padding: 7px 12px;
            }
            QPushButton#topActionButton {
                background-color: rgba(255, 255, 255, 205);
                border: 1px solid rgba(217, 140, 179, 190);
                border-radius: 20px;
                padding: 10px 20px;
                font-size: 15px;
            }
            QPushButton#topActionButton:hover { background-color: rgba(232, 168, 200, 230); }
            QPushButton#topActionButton:pressed { background-color: rgba(201, 119, 162, 235); }
            QTextEdit#logView,
            QTextEdit#editor {
                background-color: rgba(255, 255, 255, 205);
                color: #4A3B45;
                border: 1px solid rgba(232, 168, 200, 140);
                border-radius: 16px;
                padding: 12px;
                selection-background-color: #D98CB3;
                selection-color: #FFFFFF;
            }
            QTextEdit#logView { background-color: rgba(220, 248, 247, 180); }
            QProgressBar#progressBar {
                background-color: rgba(255, 255, 255, 170);
                border: 1px solid rgba(232, 168, 200, 140);
                border-radius: 9px;
                min-height: 16px;
                max-height: 16px;
            }
            QProgressBar#progressBar::chunk {
                background-color: #E8A8C8;
                border-radius: 8px;
            }
            QStatusBar { background: transparent; color: #6F5C68; }
            """
        )

    def load_config_files(self) -> None:
        self.card_guilds_editor.setPlainText(self.read_text_file(CARD_GUILDS_FILE))
        self.guilds_editor.setPlainText(self.read_text_file(GUILDS_FILE))

    def read_text_file(self, path: Path) -> str:
        if not path.exists():
            return ""
        for encoding in ("utf-8-sig", "utf-8", "cp932"):
            try:
                return path.read_text(encoding=encoding)
            except UnicodeDecodeError:
                continue
        return path.read_text(errors="replace")

    def save_config_file(self, path: Path, editor: QTextEdit) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(editor.toPlainText(), encoding="utf-8")
        relative_path = path.relative_to(PROJECT_ROOT).as_posix()
        self.append_log(f"INFO: {relative_path} を保存しました。")

    def save_then_run(self, path: Path, editor: QTextEdit, scripts: list[tuple[str, Path]]) -> None:
        if self.process is not None:
            self.append_log("INFO: すでに実行中です。完了するまでお待ちください。")
            return
        try:
            self.save_config_file(path, editor)
        except Exception as exc:
            relative_path = path.relative_to(PROJECT_ROOT).as_posix()
            self.append_log(f"ERROR: {relative_path} の保存に失敗しました: {exc}")
            return
        self.run_scripts(scripts)

    def run_scripts(self, scripts: list[tuple[str, Path]]) -> None:
        if self.process is not None:
            self.append_log("INFO: すでに実行中です。完了するまでお待ちください。")
            return
        missing = [str(path) for _, path in scripts if not path.exists()]
        if missing:
            self.append_log("ERROR: 実行ファイルが見つかりません。")
            for path in missing:
                self.append_log(f"  {path}")
            return
        self.run_queue = scripts.copy()
        self.set_run_buttons_enabled(False)
        self.start_progress()
        self.append_log("")
        self.append_log("===== 実行開始 =====")
        self.start_next_script()

    def start_next_script(self) -> None:
        if not self.run_queue:
            self.append_log("===== すべて完了しました =====")
            self.finish_run(success=True)
            return
        label, script_path = self.run_queue.pop(0)
        python_executable = self.script_python_executable()
        self.append_log(f"[START] {label}")
        self.append_log(f"> {python_executable} {script_path.relative_to(PROJECT_ROOT)}")
        process = QProcess(self)
        process.setProgram(str(python_executable))
        process.setArguments([str(script_path)])
        process.setWorkingDirectory(str(PROJECT_ROOT))
        process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONIOENCODING", "utf-8")
        process.setProcessEnvironment(env)
        process.readyReadStandardOutput.connect(self.read_stdout)
        process.readyReadStandardError.connect(self.read_stderr)
        process.finished.connect(self.process_finished)
        process.errorOccurred.connect(self.process_error)
        self.process = process
        process.start()
        if not process.waitForStarted(3000):
            self.append_log(f"[ERROR] 起動できませんでした: {process.errorString()}")
            self.process = None
            self.finish_run(success=False)

    def script_python_executable(self):
        if VENV_PYTHON.exists() and is_python_executable_usable(VENV_PYTHON):
            return VENV_PYTHON
        return sys.executable

    def read_stdout(self) -> None:
        if self.process is None:
            return
        text = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        self.append_log(text.rstrip("\n\r"))

    def read_stderr(self) -> None:
        if self.process is None:
            return
        text = bytes(self.process.readAllStandardError()).decode("utf-8", errors="replace")
        self.append_log(text.rstrip("\n\r"))

    def process_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        crashed = exit_status == QProcess.ExitStatus.CrashExit
        if exit_code == 0 and not crashed:
            self.append_log("[OK] 正常終了しました。")
            self.process = None
            self.start_next_script()
            return
        status = "クラッシュ" if crashed else f"終了コード {exit_code}"
        self.append_log(f"[FAILED] 実行に失敗しました: {status}")
        self.run_queue.clear()
        self.process = None
        self.finish_run(success=False)

    def process_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart and self.process is not None:
            self.append_log(f"[ERROR] 起動エラー: {self.process.errorString()}")

    def start_progress(self) -> None:
        self.progress_bar.setRange(0, 0)

    def finish_run(self, success: bool) -> None:
        self.set_run_buttons_enabled(True)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100 if success else 0)
        self.statusBar().showMessage("Ready")

    def set_run_buttons_enabled(self, enabled: bool) -> None:
        for button in self.run_buttons:
            button.setEnabled(enabled)

    def open_folder(self, folder_path: Path) -> None:
        folder_path.mkdir(parents=True, exist_ok=True)
        opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder_path)))
        if opened:
            self.append_log(f"[OPEN] {folder_path}")
        else:
            self.append_log(f"[ERROR] フォルダを開けませんでした: {folder_path}")
            QMessageBox.warning(self, "フォルダを開けません", str(folder_path))

    def clear_log(self) -> None:
        self.log_view.clear()

    def append_log(self, text: str) -> None:
        if not text:
            self.log_view.append("")
            return
        for line in text.splitlines():
            self.log_view.append(line)
        self.log_view.moveCursor(QTextCursor.MoveOperation.End)


def is_python_executable_usable(path) -> bool:
    try:
        result = subprocess.run(
            [str(path), "--version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def main() -> int:
    ensure_dirs()
    app = QApplication(sys.argv)
    window = LauncherWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
