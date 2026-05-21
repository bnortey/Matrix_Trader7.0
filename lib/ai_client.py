import json
import os
import sys

PROVIDERS = [
    {"name": "claude",   "key_env": "ANTHROPIC_API_KEY"},
    {"name": "gemini",   "key_env": "GEMINI_API_KEY"},
    {"name": "deepseek", "key_env": "DEEPSEEK_API_KEY"},
    {"name": "groq",     "key_env": "GROQ_API_KEY"},
    {"name": "ollama",   "key_env": "OLLAMA_BASE_URL"},
]

AVAILABLE_MODELS = [
    # Claude (paid)
    {"provider": "claude",   "model": "claude-opus-4-7",                    "label": "Claude Opus 4.7",              "key_env": "ANTHROPIC_API_KEY"},
    {"provider": "claude",   "model": "claude-sonnet-4-6",                  "label": "Claude Sonnet 4.6",            "key_env": "ANTHROPIC_API_KEY"},
    {"provider": "claude",   "model": "claude-haiku-4-5-20251001",          "label": "Claude Haiku 4.5",             "key_env": "ANTHROPIC_API_KEY"},
    # Gemini (free tier)
    {"provider": "gemini",   "model": "gemini-2.5-pro",                     "label": "Gemini 2.5 Pro (free tier)",   "key_env": "GEMINI_API_KEY"},
    {"provider": "gemini",   "model": "gemini-2.5-flash",                   "label": "Gemini 2.5 Flash (free tier)", "key_env": "GEMINI_API_KEY"},
    {"provider": "gemini",   "model": "gemini-2.0-flash",                   "label": "Gemini 2.0 Flash (free tier)", "key_env": "GEMINI_API_KEY"},
    # DeepSeek (low cost)
    {"provider": "deepseek", "model": "deepseek-chat",                      "label": "DeepSeek Chat",                "key_env": "DEEPSEEK_API_KEY"},
    # Groq (free tier)
    {"provider": "groq",     "model": "qwen/qwen3-32b",                          "label": "Qwen3 32B (Groq free)",        "key_env": "GROQ_API_KEY"},
    {"provider": "groq",     "model": "meta-llama/llama-4-scout-17b-16e-instruct","label": "Llama 4 Scout (Groq free)",    "key_env": "GROQ_API_KEY"},
    {"provider": "groq",     "model": "llama-3.3-70b-versatile",                 "label": "Llama 3.3 70B (Groq free)",    "key_env": "GROQ_API_KEY"},
    {"provider": "groq",     "model": "llama-3.1-8b-instant",                    "label": "Llama 3.1 8B Instant (Groq free)", "key_env": "GROQ_API_KEY"},
    # Local/free. Set OLLAMA_BASE_URL=http://localhost:11434 and pull the model locally.
    {"provider": "ollama",   "model": "llama3.1:8b",                         "label": "Llama 3.1 8B (Ollama local)", "key_env": "OLLAMA_BASE_URL"},
    {"provider": "ollama",   "model": "qwen2.5:7b",                          "label": "Qwen 2.5 7B (Ollama local)", "key_env": "OLLAMA_BASE_URL"},
]

_DEFAULT_SETTINGS = {"provider": "claude", "model": "claude-sonnet-4-6"}
_AI_SETTINGS_PATH = "data/ai_settings.json"

_KEY_ENV = {p["name"]: p["key_env"] for p in PROVIDERS}


def _provider_available(provider: str) -> bool:
    key_env = _KEY_ENV.get(provider, "")
    if provider == "ollama":
        return bool(os.getenv(key_env, "").strip())
    return bool(os.getenv(key_env, "").strip())


def provider_status() -> list[dict]:
    """Return provider availability without exposing secrets."""
    out = []
    for provider in PROVIDERS:
        name = provider["name"]
        key_env = provider["key_env"]
        models = [
            {"model": m["model"], "label": m["label"]}
            for m in AVAILABLE_MODELS
            if m["provider"] == name
        ]
        out.append({
            "provider": name,
            "key_env": key_env,
            "available": _provider_available(name),
            "models": models,
        })
    return out


def load_ai_settings() -> dict:
    try:
        if os.path.exists(_AI_SETTINGS_PATH):
            with open(_AI_SETTINGS_PATH, encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return dict(_DEFAULT_SETTINGS)


def save_ai_settings(settings: dict) -> None:
    os.makedirs("data", exist_ok=True)
    with open(_AI_SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f)


def call_ai(
    system: str,
    user: str,
    max_tokens: int = 512,
    allowed_providers: set[str] | list[str] | tuple[str, ...] | None = None,
) -> str | None:
    settings = load_ai_settings()
    cfg_provider = settings.get("provider")
    cfg_model    = settings.get("model")
    allowed = set(allowed_providers) if allowed_providers else None

    # Try the configured model first
    if cfg_provider and cfg_model and (allowed is None or cfg_provider in allowed):
        api_key = os.getenv(_KEY_ENV.get(cfg_provider, ""), "").strip()
        if api_key:
            fn = _DISPATCH.get(cfg_provider)
            if fn:
                try:
                    return fn(system, user, max_tokens, api_key, cfg_model)
                except Exception as e:
                    print(f"ai_client: {cfg_provider}/{cfg_model} failed: {e}", file=sys.stderr)

    # Fallback chain — skip the already-tried provider
    for provider in PROVIDERS:
        if provider["name"] == cfg_provider:
            continue
        if allowed is not None and provider["name"] not in allowed:
            continue
        api_key = os.getenv(provider["key_env"], "")
        if not api_key:
            continue
        fn = _DISPATCH.get(provider["name"])
        if fn is None:
            continue
        # Use the first model listed for this provider as the fallback default
        default_model = next(
            (m["model"] for m in AVAILABLE_MODELS if m["provider"] == provider["name"]),
            None,
        )
        if not default_model:
            continue
        try:
            return fn(system, user, max_tokens, api_key, default_model)
        except Exception as e:
            print(f"ai_client: {provider['name']} failed: {e}", file=sys.stderr)
    return None


def _call_claude(system: str, user: str, max_tokens: int, api_key: str, model: str) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return message.content[0].text


def _call_gemini(system: str, user: str, max_tokens: int, api_key: str, model: str) -> str:
    import google.generativeai as genai
    genai.configure(api_key=api_key)
    gmodel = genai.GenerativeModel(
        model_name=model,
        generation_config={"max_output_tokens": max_tokens},
    )
    response = gmodel.generate_content(f"{system}\n\n{user}")
    return response.text


def _call_deepseek(system: str, user: str, max_tokens: int, api_key: str, model: str) -> str:
    import openai
    client = openai.OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
    response = client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
    )
    return response.choices[0].message.content


def _call_groq(system: str, user: str, max_tokens: int, api_key: str, model: str) -> str:
    import groq
    client = groq.Groq(api_key=api_key)
    response = client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
    )
    return response.choices[0].message.content


def _call_ollama(system: str, user: str, max_tokens: int, base_url: str, model: str) -> str:
    import requests
    base = base_url.rstrip("/")
    response = requests.post(
        f"{base}/api/chat",
        json={
            "model": model,
            "stream": False,
            "options": {"num_predict": max_tokens},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        },
        timeout=90,
    )
    response.raise_for_status()
    data = response.json()
    return (data.get("message") or {}).get("content") or data.get("response") or ""


_DISPATCH = {
    "claude":   _call_claude,
    "gemini":   _call_gemini,
    "deepseek": _call_deepseek,
    "groq":     _call_groq,
    "ollama":   _call_ollama,
}
