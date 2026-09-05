"""LLM punctuation correction via OpenRouter or a local Ollama server."""

import logging
import os
import re

from voice_input_tool.log_utils import mask_secret, safe_model_dump, truncate_for_log

log = logging.getLogger("voice_input")

try:
    from openai import OpenAI

    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False


class LLMCorrectionError(RuntimeError):
    pass


BACKEND_OPENROUTER = "openrouter"
BACKEND_OLLAMA = "ollama"
BACKENDS = (BACKEND_OPENROUTER, BACKEND_OLLAMA)

DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434/v1"
DEFAULT_OLLAMA_MODEL = "qwen3:8b"
DEFAULT_OLLAMA_TIMEOUT = 60.0

# OpenRouter は 10 秒で見切る（クラウド側が遅い場合は待つ意味が薄い）
OPENROUTER_TIMEOUT = 10.0
# 録音停止後の全文整形は入力も出力も長くなるため、区間ごとの補正より長く待つ
OPENROUTER_LONG_FORM_TIMEOUT = 30.0

# 区間ごとの補正で許す出力トークンの上限
MAX_OUTPUT_TOKENS = 2048
# 全文整形で許す出力トークンの上限（数分話した内容が一度に入る）
MAX_OUTPUT_TOKENS_LONG_FORM = 8192

# ローカルモデルは推論内容を <think>...</think> で吐くものがあるため取り除く
_THINK_BLOCK_RE = re.compile(r"<(think|thinking)>.*?</\1>", re.DOTALL | re.IGNORECASE)

_CLIENTS = {}
_LLM_PROMPT = ""
_FINAL_POLISH_PROMPT = ""
_LLM_BACKEND = BACKEND_OPENROUTER
_LLM_MODEL = "openai/gpt-oss-120b"
_LLM_PROVIDER_ORDER = ["Cerebras"]
_OPENROUTER_API_KEY = ""
_OLLAMA_BASE_URL = DEFAULT_OLLAMA_BASE_URL
_OLLAMA_MODEL = DEFAULT_OLLAMA_MODEL
_OLLAMA_TIMEOUT = DEFAULT_OLLAMA_TIMEOUT


def normalize_backend(value):
    """設定値をバックエンド名に正規化（未知の値は OpenRouter 扱い）"""
    backend = str(value or "").strip().lower()
    return backend if backend in BACKENDS else BACKEND_OPENROUTER


def normalize_ollama_base_url(value):
    """Ollama の URL を OpenAI 互換エンドポイント（/v1）に正規化"""
    url = str(value or "").strip().rstrip("/")
    if not url:
        return DEFAULT_OLLAMA_BASE_URL
    if not url.startswith(("http://", "https://")):
        url = f"http://{url}"
    if not url.endswith("/v1"):
        url = f"{url}/v1"
    return url


def configure_llm(config):
    global _LLM_PROMPT, _FINAL_POLISH_PROMPT, _LLM_BACKEND, _LLM_MODEL, _LLM_PROVIDER_ORDER
    global _OPENROUTER_API_KEY, _OLLAMA_BASE_URL, _OLLAMA_MODEL, _OLLAMA_TIMEOUT
    _LLM_PROMPT = config["llm_prompt"]
    _FINAL_POLISH_PROMPT = config.get("final_polish_prompt", "") or _LLM_PROMPT
    _LLM_BACKEND = normalize_backend(config.get("llm_backend", _LLM_BACKEND))
    _LLM_MODEL = config.get("llm_model", _LLM_MODEL)
    _LLM_PROVIDER_ORDER = config.get("llm_provider_order", _LLM_PROVIDER_ORDER)
    _OPENROUTER_API_KEY = config.get("openrouter_api_key", "")
    _OLLAMA_BASE_URL = normalize_ollama_base_url(config.get("ollama_base_url", _OLLAMA_BASE_URL))
    _OLLAMA_MODEL = config.get("ollama_model") or DEFAULT_OLLAMA_MODEL
    try:
        _OLLAMA_TIMEOUT = float(config.get("ollama_timeout", _OLLAMA_TIMEOUT))
    except (TypeError, ValueError):
        _OLLAMA_TIMEOUT = DEFAULT_OLLAMA_TIMEOUT


def current_backend():
    return _LLM_BACKEND


def final_polish_prompt():
    return _FINAL_POLISH_PROMPT


def get_client(base_url, api_key, timeout):
    """base_url/api_key/timeout ごとに OpenAI 互換クライアントを再利用"""
    cache_key = (base_url, api_key, timeout)
    client = _CLIENTS.get(cache_key)
    if client is None:
        client = OpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
            max_retries=0,
        )
        _CLIENTS[cache_key] = client
    return client


def get_openrouter_client(api_key, timeout=OPENROUTER_TIMEOUT):
    return get_client("https://openrouter.ai/api/v1", api_key, timeout)


def get_ollama_client(base_url=None, timeout=None):
    # Ollama は認証不要だが OpenAI SDK が空キーを拒否するのでダミーを渡す
    return get_client(
        normalize_ollama_base_url(base_url or _OLLAMA_BASE_URL),
        "ollama",
        timeout if timeout is not None else _OLLAMA_TIMEOUT,
    )


def list_ollama_models(base_url=None, timeout=5.0):
    """Ollama にダウンロード済みのモデル名一覧を返す（失敗時は例外）"""
    if not HAS_OPENAI:
        raise LLMCorrectionError("openai パッケージがインストールされていません")
    client = get_ollama_client(base_url, timeout)
    models = client.models.list()
    return sorted(str(m.id) for m in (getattr(models, "data", None) or []))


def llm_correct(text, api_key=None, prompt=None, long_form=False):
    """テキストを補正する。未補正テキストへのフォールバックはしない。

    prompt を渡すと設定の句読点補正プロンプトの代わりに使う。
    long_form=True は録音停止後の全文整形向けで、待ち時間と出力上限を広げる。
    """
    if not text:
        return ""
    if not HAS_OPENAI:
        raise LLMCorrectionError("openai パッケージがインストールされていません")

    prompt = prompt or _LLM_PROMPT
    if _LLM_BACKEND == BACKEND_OLLAMA:
        return _correct_with_ollama(text, prompt, long_form)
    return _correct_with_openrouter(text, api_key, prompt, long_form)


def _max_tokens_for(text, long_form=False):
    cap = MAX_OUTPUT_TOKENS_LONG_FORM if long_form else MAX_OUTPUT_TOKENS
    # 日本語は1文字あたり1トークンを超えることがあるため、全文整形では余裕を持たせる
    estimated = len(text) * 2 + 256 if long_form else len(text) + 256
    return min(cap, max(1024, estimated))


def _correct_with_openrouter(text, api_key=None, prompt=None, long_form=False):
    key = api_key or _OPENROUTER_API_KEY or os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise LLMCorrectionError("OPENROUTER_API_KEY が設定されていません")

    timeout = OPENROUTER_LONG_FORM_TIMEOUT if long_form else OPENROUTER_TIMEOUT
    client = get_openrouter_client(key, timeout)
    max_tokens = _max_tokens_for(text, long_form)
    extra_body = {
        "data_collection": "deny",
        "zdr": True,
        "provider": {
            "order": _LLM_PROVIDER_ORDER,
            "allow_fallbacks": False,
        },
        "reasoning": {
            "effort": "low",
            "exclude": True,
        },
        "reasoning_effort": "low",
        "chat_template_kwargs": {"enable_thinking": False},
    }

    log.info(
        "LLM補正リクエスト: backend=openrouter model=%s providers=%s long_form=%s text_len=%d key=%s prompt_len=%d max_tokens=%d timeout=%.1f text=%r",
        _LLM_MODEL,
        _LLM_PROVIDER_ORDER,
        long_form,
        len(text),
        mask_secret(key),
        len(prompt),
        max_tokens,
        timeout,
        truncate_for_log(text, 300),
    )

    return _request_correction(
        client,
        model=_LLM_MODEL,
        text=text,
        prompt=prompt,
        max_tokens=max_tokens,
        extra_body=extra_body,
        backend=BACKEND_OPENROUTER,
    )


def _correct_with_ollama(text, prompt=None, long_form=False):
    client = get_ollama_client()
    max_tokens = _max_tokens_for(text, long_form)

    log.info(
        "LLM補正リクエスト: backend=ollama base_url=%s model=%s long_form=%s text_len=%d prompt_len=%d max_tokens=%d timeout=%.1f text=%r",
        _OLLAMA_BASE_URL,
        _OLLAMA_MODEL,
        long_form,
        len(text),
        len(prompt),
        max_tokens,
        _OLLAMA_TIMEOUT,
        truncate_for_log(text, 300),
    )

    return _request_correction(
        client,
        model=_OLLAMA_MODEL,
        text=text,
        prompt=prompt,
        max_tokens=max_tokens,
        # Ollama の OpenAI 互換APIは未知のフィールドを無視するため、
        # OpenRouter 固有のオプションは送らない
        extra_body=None,
        backend=BACKEND_OLLAMA,
    )


def _request_correction(client, model, text, prompt, max_tokens, extra_body, backend):
    try:
        kwargs = {}
        if extra_body:
            kwargs["extra_body"] = extra_body
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": text},
            ],
            max_tokens=max_tokens,
            temperature=0.1,
            **kwargs,
        )
        choices = getattr(response, "choices", []) or []
        if not choices:
            log.error("LLM応答choicesが空: raw=%s", truncate_for_log(safe_model_dump(response)))
            raise LLMCorrectionError("LLM応答choicesが空でした")

        choice = choices[0]
        message = getattr(choice, "message", None)
        corrected = getattr(message, "content", None) if message is not None else None
        finish_reason = getattr(choice, "finish_reason", None)
        response_model = getattr(response, "model", None)
        response_id = getattr(response, "id", None)
        usage = getattr(response, "usage", None)
        content_len = len(corrected) if corrected else 0
        log.info(
            "LLM応答: backend=%s id=%s response_model=%s finish_reason=%s content_len=%d usage=%s content_preview=%r",
            backend,
            response_id,
            response_model,
            finish_reason,
            content_len,
            truncate_for_log(usage, 500),
            truncate_for_log(corrected or "", 500),
        )

        if corrected is None:
            log.error("LLM応答contentがNone: raw=%s", truncate_for_log(safe_model_dump(response)))
            raise LLMCorrectionError("LLM補正結果が空でした")
        corrected = strip_reasoning(corrected)
        if not corrected:
            log.error("LLM応答contentが空: raw=%s", truncate_for_log(safe_model_dump(response)))
            raise LLMCorrectionError("LLM補正結果が空でした")
        return corrected
    except LLMCorrectionError:
        raise
    except Exception as e:
        log.exception(
            "LLM補正API呼び出しエラー: backend=%s model=%s text_len=%d",
            backend,
            model,
            len(text),
        )
        raise LLMCorrectionError(str(e)) from e


def strip_reasoning(content):
    """<think> ブロックを落として前後の空白を除去"""
    return _THINK_BLOCK_RE.sub("", content).strip()
