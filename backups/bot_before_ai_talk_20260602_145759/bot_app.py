"""PySide6 desktop controller dedicated to the Discord Bot."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QProcess, QProcessEnvironment, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BOT_STOP_TIMEOUT_MS = 5000


class BotControllerWindow(QMainWindow):
    """Small desktop app for starting, stopping, and monitoring the Discord Bot."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("BDM Discord Bot Controller")
        self.bot_process: QProcess | None = None
        self.bot_stop_requested = False

        title_label = QLabel("BDM Discord Bot Controller")
        title_label.setObjectName("titleLabel")
        self.bot_status_label = QLabel("状態: 停止中")
        self.bot_status_label.setObjectName("statusLabel")
        self.bot_start_button = QPushButton("Bot起動")
        self.bot_stop_button = QPushButton("Bot停止")
        self.bot_stop_button.setObjectName("stopButton")
        self.clear_log_button = QPushButton("ログをクリア")
        self.bot_stop_button.setEnabled(False)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)

        self.bot_start_button.clicked.connect(self.start_bot)
        self.bot_stop_button.clicked.connect(self.stop_bot)
        self.clear_log_button.clicked.connect(self.log_text.clear)

        bot_controls = QHBoxLayout()
        bot_controls.addWidget(self.bot_start_button)
        bot_controls.addWidget(self.bot_stop_button)
        bot_controls.addWidget(self.clear_log_button)
        bot_controls.addStretch()

        layout = QVBoxLayout()
        layout.addWidget(title_label)
        layout.addWidget(self.bot_status_label)
        layout.addLayout(bot_controls)
        layout.addWidget(QLabel("ログ"))
        layout.addWidget(self.log_text)

        central_widget = QWidget()
        central_widget.setLayout(layout)
        self.setCentralWidget(central_widget)
        self.resize(900, 620)
        self.apply_styles()

    def apply_styles(self) -> None:
        """Apply a simple soft-colored style for readability."""
        self.setStyleSheet(
            """
            QMainWindow {
                background-color: #FFF7FB;
            }
            QWidget {
                background-color: #FFF7FB;
                color: #4B2F3B;
                font-size: 13px;
            }
            QLabel#titleLabel {
                color: #6B4253;
                font-size: 22px;
                font-weight: 700;
                padding: 10px 0 4px 0;
            }
            QLabel#statusLabel {
                background-color: #FFF0F6;
                border: 1px solid #E8C7D6;
                border-radius: 10px;
                color: #A35D7F;
                font-size: 15px;
                font-weight: 700;
                padding: 10px 12px;
            }
            QPushButton {
                background-color: #F8D7E6;
                border: 1px solid #E8C7D6;
                border-radius: 12px;
                color: #6B4253;
                font-size: 14px;
                font-weight: 600;
                min-height: 34px;
                padding: 7px 18px;
            }
            QPushButton:hover {
                background-color: #F3C4DA;
            }
            QPushButton#stopButton {
                background-color: #EFB6CC;
                border-color: #E3A6BF;
            }
            QPushButton#stopButton:hover {
                background-color: #E8A8C3;
            }
            QPushButton:disabled {
                background-color: #F2E7EC;
                border-color: #E7D6DE;
                color: #B79AAA;
            }
            QTextEdit {
                background-color: #FFFFFF;
                border: 1px solid #E8C7D6;
                border-radius: 12px;
                color: #3F2A33;
                font-family: Consolas, Menlo, "Yu Gothic UI", "Meiryo", monospace;
                font-size: 12px;
                padding: 10px;
            }
            """
        )

    def start_bot(self) -> None:
        """Start the Discord Bot process unless it is already running."""
        if self.bot_process is not None and self.bot_process.state() != QProcess.NotRunning:
            self.append_log("[Bot] すでに起動中です。")
            return

        python_executable = self.get_python_executable()
        self.bot_process = QProcess(self)
        self.bot_stop_requested = False
        self.bot_process.setProgram(str(python_executable))
        self.bot_process.setArguments(["-X", "utf8", "-m", "bot.main"])
        self.bot_process.setWorkingDirectory(str(PROJECT_ROOT))
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONUNBUFFERED", "1")
        environment.insert("PYTHONUTF8", "1")
        environment.insert("PYTHONIOENCODING", "utf-8")
        environment.insert("LANG", "ja_JP.UTF-8")
        self.bot_process.setProcessEnvironment(environment)

        self.bot_process.started.connect(self.on_bot_started)
        self.bot_process.readyReadStandardOutput.connect(self.read_bot_stdout)
        self.bot_process.readyReadStandardError.connect(self.read_bot_stderr)
        self.bot_process.finished.connect(self.on_bot_finished)
        self.bot_process.errorOccurred.connect(self.on_bot_error)

        self.set_bot_state("起動中…")
        self.append_log("[Bot] 起動処理を開始します。")
        self.append_log(f"[Bot] 起動します: {python_executable} -X utf8 -m bot.main")
        self.bot_process.start()

    def stop_bot(self) -> None:
        """Request the Discord Bot process to stop."""
        if self.bot_stop_requested:
            self.append_log("[Bot] 停止処理はすでに実行中です。")
            return

        if self.bot_process is None or self.bot_process.state() == QProcess.NotRunning:
            self.set_bot_state("停止中")
            self.append_log("[Bot] Botは起動していません。")
            self.cleanup_bot_process()
            return

        self.bot_stop_requested = True
        self.set_bot_state("停止中…")
        self.append_log("[Bot] 停止処理を開始します。")
        self.bot_process.terminate()
        QTimer.singleShot(BOT_STOP_TIMEOUT_MS, self.kill_bot_if_running)

    def kill_bot_if_running(self) -> None:
        """Force-kill the bot if terminate did not stop it in time."""
        if self.bot_process is not None and self.bot_process.state() != QProcess.NotRunning:
            self.append_log("[Bot] Botプロセスを強制終了しました。")
            self.bot_process.kill()
            QTimer.singleShot(1000, self.ensure_stopped_after_kill)

    def ensure_stopped_after_kill(self) -> None:
        """Ensure the UI is not left in a running state after kill()."""
        if self.bot_process is None:
            return
        if self.bot_process.state() == QProcess.NotRunning:
            self.read_bot_stdout()
            self.read_bot_stderr()
            self.set_bot_state("停止中" if self.bot_stop_requested else "異常終了")
            self.cleanup_bot_process()

    def on_bot_started(self) -> None:
        """Handle successful bot process startup."""
        self.set_bot_state("起動中")
        self.append_log("[Bot] Botプロセスが開始されました。")

    def on_bot_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        """Handle bot process completion."""
        self.read_bot_stdout()
        self.read_bot_stderr()
        self.append_log("[Bot] Botプロセスが終了しました。")

        if self.bot_stop_requested or (exit_status == QProcess.NormalExit and exit_code == 0):
            self.set_bot_state("停止中")
            self.append_log(f"[Bot] 停止しました。exit_code={exit_code}")
        else:
            self.set_bot_state("異常終了")
            self.append_log(f"[Bot] 異常終了しました。exit_code={exit_code} exit_status={exit_status}")

        self.cleanup_bot_process()

    def on_bot_error(self, error: QProcess.ProcessError) -> None:
        """Handle bot process errors."""
        self.append_log(f"[Bot] プロセスエラー: {error}")
        if error == QProcess.FailedToStart:
            self.set_bot_state("異常終了")
            self.cleanup_bot_process()

    def read_bot_stdout(self) -> None:
        """Append bot stdout to the log area."""
        if self.bot_process is None:
            return
        output = decode_process_output(bytes(self.bot_process.readAllStandardOutput()))
        if output:
            self.append_log(output.rstrip())

    def read_bot_stderr(self) -> None:
        """Append bot stderr to the log area."""
        if self.bot_process is None:
            return
        output = decode_process_output(bytes(self.bot_process.readAllStandardError()))
        if output:
            self.append_log(output.rstrip())

    def set_bot_state(self, state: str) -> None:
        """Update the bot status label and button enabled states together."""
        self.bot_status_label.setText(f"状態: {state}")
        if state in ("停止中", "異常終了"):
            self.bot_start_button.setEnabled(True)
            self.bot_stop_button.setEnabled(False)
        elif state in ("起動中…", "起動中"):
            self.bot_start_button.setEnabled(False)
            self.bot_stop_button.setEnabled(True)
        elif state == "停止中…":
            self.bot_start_button.setEnabled(False)
            self.bot_stop_button.setEnabled(False)

    def cleanup_bot_process(self) -> None:
        """Release the current QProcess reference after it has stopped or failed."""
        if self.bot_process is not None:
            self.bot_process.deleteLater()
            self.bot_process = None
        self.bot_stop_requested = False

    def append_log(self, text: str) -> None:
        """Append text to the application log area."""
        self.log_text.append(text)

    def get_python_executable(self) -> Path:
        """Return the preferred Python executable for launching the Discord Bot."""
        windows_venv_python = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
        if windows_venv_python.exists():
            return windows_venv_python

        unix_venv_python = PROJECT_ROOT / ".venv" / "bin" / "python"
        if unix_venv_python.exists():
            return unix_venv_python

        return Path(sys.executable)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt method name
        """Stop the bot process before closing the app."""
        if self.bot_process is not None and self.bot_process.state() != QProcess.NotRunning:
            self.bot_stop_requested = True
            self.set_bot_state("停止中…")
            self.append_log("[Bot] アプリ終了のためBotを停止します。")
            self.bot_process.terminate()
            if not self.bot_process.waitForFinished(BOT_STOP_TIMEOUT_MS):
                self.append_log("[Bot] Botプロセスを強制終了しました。")
                self.bot_process.kill()
                self.bot_process.waitForFinished(1000)
            self.read_bot_stdout()
            self.read_bot_stderr()
            self.set_bot_state("停止中")
            self.cleanup_bot_process()
        super().closeEvent(event)


def decode_process_output(data: bytes) -> str:
    """Decode Bot process output with UTF-8 first and cp932 as a fallback."""
    if not data:
        return ""

    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp932", errors="replace")


def main() -> None:
    """Run the PySide6 application."""
    app = QApplication(sys.argv)
    window = BotControllerWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
