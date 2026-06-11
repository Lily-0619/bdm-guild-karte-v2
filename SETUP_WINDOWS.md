# BDM Guild Karte Tool / Bot セットアップ手順 Windows

## 1. プロジェクトフォルダへ移動

```powershell
cd <bdm-guild-karteを置いた場所>

※ DドライブでもEドライブでも、実際にこのフォルダを置いた場所へ移動する。

2. 古い仮想環境を削除
Remove-Item .venv -Recurse -Force
3. 仮想環境を作成
python -m venv .venv
4. pip更新
.\.venv\Scripts\python.exe -m pip install --upgrade pip
5. 必要ライブラリを入れる
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
6. Playwrightブラウザを入れる

DBonk取得を行うPCでは実行。

.\.venv\Scripts\python.exe -m playwright install chromium
7. 構文チェック
.\.venv\Scripts\python.exe -m compileall src bot
8. カルテアプリ起動
.\.venv\Scripts\python.exe src\app.py
9. Bot管理アプリ起動
.\.venv\Scripts\python.exe src\bot_app.py
10. Bot本体を直接起動
.\.venv\Scripts\python.exe -m bot.main

---

## さらに楽にするなら bat を作る

`setup_windows.bat` を作っておくと、PC変えた時に一発で環境作れます。

```bat
@echo off
cd /d %~dp0

echo === Remove old .venv ===
if exist .venv rmdir /s /q .venv

echo === Create .venv ===
python -m venv .venv

echo === Upgrade pip ===
.\.venv\Scripts\python.exe -m pip install --upgrade pip

echo === Install requirements ===
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

echo === Install Playwright Chromium ===
.\.venv\Scripts\python.exe -m playwright install chromium

echo === Compile check ===
.\.venv\Scripts\python.exe -m compileall src bot

echo === Setup complete ===
pause

ただし、Botだけ動かすPCなら Playwright は不要だから、
将来的には2つに分けてもいいです。

setup_full.bat       → scraper/analyze/make_card/UI/Bot全部
setup_bot_only.bat   → Discord Botだけ
