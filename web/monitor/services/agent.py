import httpx
from django.conf import settings


class AgentError(Exception):
    pass


def _request(method, path, params=None, payload=None, timeout=None, use_detail=False):
    url = f"{settings.AGENT_URL}{path}"
    headers = {"X-Agent-Token": settings.AGENT_TOKEN}
    try:
        response = httpx.request(
            method,
            url,
            params=params,
            json=payload,
            headers=headers,
            timeout=timeout or httpx.Timeout(10.0, connect=2.0),
        )
    except httpx.HTTPError as exc:
        raise AgentError("The agent is unreachable.") from exc
    if response.status_code >= 400:
        detail = ""
        if use_detail:
            try:
                body = response.json()
            except ValueError:
                body = None
            if isinstance(body, dict) and isinstance(body.get("detail"), str):
                detail = body["detail"]
        if response.status_code == 404:
            raise AgentError(detail or "That container is not on this Docker host.")
        if response.status_code == 503:
            raise AgentError(detail or "Docker is not available to the agent.")
        raise AgentError(detail or "The agent refused the request.")
    try:
        return response.json()
    except ValueError as exc:
        raise AgentError("The agent returned an unreadable response.") from exc


def _get(path, params, timeout=None):
    return _request("GET", path, params=params, timeout=timeout)


def list_containers():
    payload = _get("/containers", None)
    containers = payload.get("containers")
    if not isinstance(containers, list):
        raise AgentError("The agent returned an unreadable response.")
    return containers


def get_container(name):
    return _get("/container", {"name": name})


def get_env(name):
    payload = _get("/container/env", {"name": name})
    text = payload.get("env")
    if not isinstance(text, str):
        raise AgentError("The agent returned an unreadable response.")
    return text


def read_project_env(path):
    payload = _request("GET", "/project/env", params={"path": path}, use_detail=True)
    text = payload.get("env")
    missing = payload.get("missing")
    if not isinstance(text, str) or not isinstance(missing, bool):
        raise AgentError("The agent returned an unreadable response.")
    return text, missing


def write_project_env(path, body):
    _request("PUT", "/project/env", payload={"path": path, "body": body}, use_detail=True)


def get_logs(name, tail=200):
    payload = _get(
        "/container/logs",
        {"name": name, "tail": tail},
        timeout=httpx.Timeout(30.0, connect=2.0),
    )
    logs = payload.get("logs")
    if not isinstance(logs, str):
        raise AgentError("The agent returned an unreadable response.")
    return logs
