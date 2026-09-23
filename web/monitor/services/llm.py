import json

import httpx
from django.conf import settings

from .redact import redact, redact_obj

SYSTEM_PROMPT = (
    "You explain why a Docker container is unhealthy, restarting, or stopped. "
    "You receive container status, redacted logs, an optional Dockerfile, and env variable names "
    "marked only as set or missing. "
    "Reply with a likely cause, the log lines that support it, and what to change in code, config, "
    "or the Dockerfile. Call the result a hypothesis. Do not ask for secret values."
)


class LLMError(Exception):
    pass


class LLMNotConfigured(Exception):
    pass


def configured():
    base = (settings.LLM_API_BASE or "").rstrip("/")
    if not base:
        return False
    if settings.LLM_API_TOKEN:
        return True
    return base != "https://api.openai.com/v1"


def explain(*, status, logs, dockerfile, env_names):
    if not configured():
        raise LLMNotConfigured()

    safe_env = []
    for item in env_names:
        state = "set" if item.get("state") == "set" else "missing"
        safe_env.append({"name": str(item.get("name", ""))[:120], "state": state})

    payload = {
        "status": redact_obj(status),
        "logs": redact(logs)[-12000:],
        "dockerfile": redact(dockerfile)[-12000:],
        "env": safe_env,
    }
    headers = {"Content-Type": "application/json"}
    if settings.LLM_API_TOKEN:
        headers["Authorization"] = f"Bearer {settings.LLM_API_TOKEN}"
    try:
        response = httpx.post(
            f"{settings.LLM_API_BASE}/chat/completions",
            headers=headers,
            json={
                "model": settings.LLM_MODEL,
                "temperature": settings.LLM_TEMPERATURE,
                "max_tokens": settings.LLM_MAX_TOKENS,
                "effort": settings.LLM_EFFORT,
                "chat_template_kwargs": {"enable_thinking": False},
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(payload)},
                ],
            },
            timeout=60,
        )
    except httpx.HTTPError as exc:
        raise LLMError("The model request failed.") from exc
    if response.status_code >= 400:
        raise LLMError("The model request failed.")
    try:
        content = response.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise LLMError("The model returned an unexpected response.") from exc
    text = (content or "").strip()
    if not text:
        raise LLMError("The model returned an empty response.")
    return text[:8000]
