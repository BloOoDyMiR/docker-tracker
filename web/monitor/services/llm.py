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


def _request_body(*, status, logs, dockerfile, env_names):
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
    return {
        "model": settings.LLM_MODEL,
        "temperature": settings.LLM_TEMPERATURE,
        "max_tokens": settings.LLM_MAX_TOKENS,
        "effort": settings.LLM_EFFORT,
        "stream": True,
        "chat_template_kwargs": {"enable_thinking": False},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload)},
        ],
    }


def _headers():
    headers = {"Content-Type": "application/json"}
    if settings.LLM_API_TOKEN:
        headers["Authorization"] = f"Bearer {settings.LLM_API_TOKEN}"
    return headers


def _text_from_payload(data):
    try:
        payload = json.loads(data)
    except json.JSONDecodeError:
        return ""
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return ""
    choice = choices[0]
    delta = choice.get("delta")
    if isinstance(delta, dict) and isinstance(delta.get("content"), str):
        return delta["content"]
    message = choice.get("message")
    if isinstance(message, dict) and isinstance(message.get("content"), str):
        return message["content"]
    if isinstance(choice.get("text"), str):
        return choice["text"]
    return ""


def _iter_model_text(response):
    raw_lines = []
    saw_data = False
    for raw in response.iter_lines():
        line = (raw or "").strip()
        if not line:
            continue
        if line.startswith("data:"):
            saw_data = True
            data = line[5:].strip()
            if data == "[DONE]":
                return
            text = _text_from_payload(data)
            if text:
                yield text
            continue
        raw_lines.append(line)
    if saw_data:
        return
    text = _text_from_payload("\n".join(raw_lines))
    if text:
        yield text


def explain_chunks(*, status, logs, dockerfile, env_names):
    if not configured():
        raise LLMNotConfigured()
    timeout = httpx.Timeout(30.0, read=120.0)
    try:
        with httpx.Client(timeout=timeout) as client:
            with client.stream(
                "POST",
                f"{settings.LLM_API_BASE}/chat/completions",
                headers=_headers(),
                json=_request_body(
                    status=status,
                    logs=logs,
                    dockerfile=dockerfile,
                    env_names=env_names,
                ),
            ) as response:
                if response.status_code >= 400:
                    raise LLMError("The model request failed.")
                yield from _iter_model_text(response)
    except LLMError:
        raise
    except httpx.HTTPError as exc:
        raise LLMError("The model request failed.") from exc


def explain(*, status, logs, dockerfile, env_names):
    parts = []
    for piece in explain_chunks(status=status, logs=logs, dockerfile=dockerfile, env_names=env_names):
        parts.append(piece)
    text = "".join(parts).strip()
    if not text:
        raise LLMError("The model returned an empty response.")
    return text[:8000]
