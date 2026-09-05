"""設定ファイル管理

方針:
- ユーザー設定は config.json に保存
- 機微情報（API Key）は .env に保存し、config.json には書き出さない
  - 読み込み時は .env -> 環境変数 -> config.json の順で解決
"""
import json
import os
from typing import Dict

from voice_input_tool.app_paths import APP_DIR

CONFIG_DIR = str(APP_DIR)
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
DOTENV_PATH = os.path.join(CONFIG_DIR, ".env")

DEFAULTS = {
    "use_llm": True,
    "llm_backend": "openrouter",  # "openrouter" | "ollama"
    "openrouter_api_key": "",  # 保持はするが、ファイル保存はしない
    "llm_model": "openai/gpt-oss-120b",
    "llm_provider_order": ["Cerebras"],
    "ollama_base_url": "http://127.0.0.1:11434/v1",
    "ollama_model": "qwen3:8b",
    "ollama_timeout": 60.0,
    "hotkey_record": "<ctrl>+<shift>+<space>",
    "input_device_id": "",
    "llm_prompt": (
        "以下の音声認識結果に、句読点のみを挿入してください。\n"
        "文章の変更・言い換え・要約は一切しないでください。\n"
        "フィラー（あー、えー、まー）の削除のみ許可します。\n"
        "話し言葉を書き言葉に変換しないでください。\n"
        "出力は補正後のテキストのみとし、説明やコメントは一切付けないでください。"
    ),
    # 録音停止後に、その回に入力した全文をまとめて整形し直す
    "final_polish": True,
    "final_polish_prompt": (
        "以下は音声認識の結果です。読みやすい文章に整えてください。\n"
        "- フィラー（あー、えー、まー、そのー）は削除する\n"
        "- 句読点を適切に入れる\n"
        "- 音声認識の明らかな誤変換は前後の文脈から修正する\n"
        "- 言い直しや重複した言い回しは整理する\n"
        "- 内容の追加・要約・意図の変更はしない\n"
        "- 敬体／常体は元の話し方に合わせる\n"
        "- 改行は入れず、1行で出力する\n"
        "出力は整形後のテキストのみとし、説明やコメントは一切付けないでください。"
    ),
}


def _read_dotenv() -> Dict[str, str]:
    """簡易的な .env ローダー（依存を増やさない）"""
    env: Dict[str, str] = {}
    if not os.path.exists(DOTENV_PATH):
        return env
    try:
        with open(DOTENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        # 壊れた .env は無視（ログは呼び出し側で）
        pass
    return env


def _write_dotenv_var(key: str, value: str) -> None:
    """.env の key を value で更新（存在しなければ追記）"""
    lines: list[str] = []
    found = False
    if os.path.exists(DOTENV_PATH):
        with open(DOTENV_PATH, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    else:
        os.makedirs(CONFIG_DIR, exist_ok=True)

    new_lines: list[str] = []
    for line in lines:
        if line.strip().startswith(f"{key}="):
            if value:
                new_lines.append(f"{key}={value}")
            found = True
        else:
            new_lines.append(line)
    if not found and value:
        new_lines.append(f"{key}={value}")

    with open(DOTENV_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(new_lines) + ("\n" if new_lines else ""))


def load_config():
    """設定ファイルを読み込み、デフォルト値とマージして返す"""
    config = dict(DEFAULTS)

    # 1) config.json を読み込み（存在すれば）
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
            config.update(saved)
        except Exception:
            # 壊れた設定は無視してデフォルトで続行
            pass

    # 2) .env を読み込み（依存無しで）
    dotenv = _read_dotenv()
    env_key = dotenv.get("OPENROUTER_API_KEY")

    # 3) 環境変数 > .env > config.json の順で API Key を反映
    api_from_env = os.environ.get("OPENROUTER_API_KEY")
    if api_from_env:
        config["openrouter_api_key"] = api_from_env
    elif env_key:
        config["openrouter_api_key"] = env_key

    # 実行中プロセスの環境にも反映（OpenAI SDK 等が参照）
    if config.get("openrouter_api_key") and not os.environ.get("OPENROUTER_API_KEY"):
        os.environ["OPENROUTER_API_KEY"] = config["openrouter_api_key"]

    return config


def save_config(config):
    """設定ファイルに保存

    - openrouter_api_key は .env に保存し、config.json には書かない
    """
    # .env を更新（値が空なら更新しない＝現状維持）
    api_key = config.get("openrouter_api_key", "")
    if api_key:
        _write_dotenv_var("OPENROUTER_API_KEY", api_key)

    # JSON に保存する辞書からは API Key を除外
    data = {k: v for k, v in config.items() if k != "openrouter_api_key"}

    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
