import json
import os
import urllib.error
import urllib.request


class OllamaServiceError(Exception):
    """Ollamaサービス全般のエラー。"""
    pass


class OllamaConnectionError(OllamaServiceError):
    """Ollamaに接続できない場合のエラー。"""
    pass


class OllamaModelNotFoundError(OllamaServiceError):
    """指定モデルが見つからない場合のエラー。"""
    pass


class OllamaResponseError(OllamaServiceError):
    """Ollamaの応答が不正、またはエラー応答だった場合のエラー。"""
    pass


OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
OLLAMA_TIMEOUT_SECONDS = int(os.getenv("OLLAMA_TIMEOUT_SECONDS", "60"))
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "4096"))


SYSTEM_PROMPT = """
あなたは Discord サーバー内で動く「Mぼっと」です。
黒い砂漠モバイル、ギルド活動、オーガタイマー、カルテ作成ツールについて手伝うための日本語AIです。

返答ルール：
- 必ず日本語で返答する
- Discord向けに短めで読みやすく返す
- 1回の返答は基本的に300〜800文字程度
- 箇条書きは必要なときだけ使う
- わからないことは断定せず「わからない」「情報が足りない」と言う
- コードや手順を聞かれたら、PowerShellやPythonの実行コマンドを具体的に出す
- 黒い砂漠モバイルの情報は、与えられた文脈に基づいて答える
- 最新情報やDBonkの実データを見ていない場合は、見たふりをしない
- ユーザーの発言意図を汲み取り、実用的に返す
- キャラ口調は少し親しみやすく。ただしふざけすぎない

禁止：
- 知らない仕様を断定しない
- 長文すぎる返答をしない
- 英語で返さない
- トークンやパスワードなどの秘密情報を表示しない
""".strip()


def _normalize_history(history):
    """
    ai_talk.py から渡される履歴を Ollama /api/chat 用に整える。
    history は [{"role": "user", "content": "..."}] 形式を想定。
    """
    normalized = []

    if not history:
        return normalized

    for item in history:
        if not isinstance(item, dict):
            continue

        role = item.get("role")
        content = item.get("content")

        if role not in ("user", "assistant", "system"):
            continue

        if not isinstance(content, str) or not content.strip():
            continue

        normalized.append(
            {
                "role": role,
                "content": content.strip(),
            }
        )

    return normalized


def chat_with_ollama(message: str, history=None) -> str:
    """
    Ollama /api/chat に問い合わせて返答を返す。

    ai_talk.py 側から
        chat_with_ollama(message, history)
    と呼ばれる前提。

    message: 今回のユーザー発言
    history: 直近会話履歴
    """
    if not isinstance(message, str) or not message.strip():
        raise OllamaResponseError("送信するメッセージが空です。")

    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT,
        }
    ]

    messages.extend(_normalize_history(history))

    messages.append(
        {
            "role": "user",
            "content": message.strip(),
        }
    )

    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": 0.3,
            "top_p": 0.8,
            "repeat_penalty": 1.1,
            "num_ctx": max(4096, OLLAMA_NUM_CTX),
        },
    }

    url = f"{OLLAMA_BASE_URL}/api/chat"

    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=OLLAMA_TIMEOUT_SECONDS) as response:
            raw = response.read().decode("utf-8", errors="replace")

    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")

        if e.code == 404:
            raise OllamaModelNotFoundError(
                f"Ollamaモデルが見つかりません: {OLLAMA_MODEL}"
            ) from e

        raise OllamaResponseError(
            f"OllamaがHTTPエラーを返しました: {e.code} {body}"
        ) from e

    except urllib.error.URLError as e:
        raise OllamaConnectionError(
            f"Ollamaに接続できません。Ollamaが起動しているか確認してください: {e}"
        ) from e

    except TimeoutError as e:
        raise OllamaConnectionError(
            "Ollamaの応答がタイムアウトしました。"
        ) from e

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise OllamaResponseError(
            f"Ollamaの応答JSONを読み取れませんでした: {raw[:300]}"
        ) from e

    if "error" in data:
        error_message = str(data.get("error", ""))

        if "model" in error_message.lower() and "not found" in error_message.lower():
            raise OllamaModelNotFoundError(
                f"Ollamaモデルが見つかりません: {OLLAMA_MODEL}"
            )

        raise OllamaResponseError(f"Ollamaエラー: {error_message}")

    content = (
        data.get("message", {})
        .get("content", "")
    )

    if not isinstance(content, str) or not content.strip():
        raise OllamaResponseError("Ollamaから空の返答が返りました。")

    return content.strip()