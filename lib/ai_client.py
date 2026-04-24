import os
import sys

PROVIDERS = [
    {"name": "claude",   "key_env": "ANTHROPIC_API_KEY"},
    {"name": "gemini",   "key_env": "GEMINI_API_KEY"},
    {"name": "deepseek", "key_env": "DEEPSEEK_API_KEY"},
    {"name": "groq",     "key_env": "GROQ_API_KEY"},
]


def call_ai(system: str, user: str, max_tokens: int = 512) -> str | None:
    for provider in PROVIDERS:
        api_key = os.getenv(provider["key_env"], "")
        if not api_key:
            continue
        fn = _DISPATCH.get(provider["name"])
        if fn is None:
            continue
        try:
            return fn(system, user, max_tokens, api_key)
        except Exception as e:
            print(f"ai_client: {provider['name']} failed: {e}", file=sys.stderr)
    return None


def _call_claude(system: str, user: str, max_tokens: int, api_key: str) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return message.content[0].text


def _call_gemini(system: str, user: str, max_tokens: int, api_key: str) -> str:
    import google.generativeai as genai
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        model_name="gemini-2.0-flash",
        generation_config={"max_output_tokens": max_tokens},
    )
    response = model.generate_content(f"{system}\n\n{user}")
    return response.text


def _call_deepseek(system: str, user: str, max_tokens: int, api_key: str) -> str:
    import openai
    client = openai.OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
    response = client.chat.completions.create(
        model="deepseek-chat",
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
    )
    return response.choices[0].message.content


def _call_groq(system: str, user: str, max_tokens: int, api_key: str) -> str:
    import groq
    client = groq.Groq(api_key=api_key)
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
    )
    return response.choices[0].message.content


_DISPATCH = {
    "claude":   _call_claude,
    "gemini":   _call_gemini,
    "deepseek": _call_deepseek,
    "groq":     _call_groq,
}
