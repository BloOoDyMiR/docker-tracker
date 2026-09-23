import httpx
from django.conf import settings


class AgentError(Exception):
    pass


def _get(path, params):
    url = f"{settings.AGENT_URL}{path}"
    headers = {"X-Agent-Token": settings.AGENT_TOKEN}
    try:
        response = httpx.get(url, params=params, headers=headers, timeout=httpx.Timeout(10.0, connect=2.0))
    except httpx.HTTPError as exc:
        raise AgentError("The agent is unreachable.") from exc
    if response.status_code == 404:
        raise AgentError("That container is not on this Docker host.")
    if response.status_code == 503:
        raise AgentError("Docker is not available to the agent.")
    if response.status_code >= 400:
        raise AgentError("The agent refused the request.")
    try:
        return response.json()
    except ValueError as exc:
        raise AgentError("The agent returned an unreadable response.") from exc


def list_containers():
    payload = _get("/containers", None)
    containers = payload.get("containers")
    if not isinstance(containers, list):
        raise AgentError("The agent returned an unreadable response.")
    return containers


def get_container(name):
    return _get("/container", {"name": name})


def get_logs(name, tail=200):
    payload = _get("/container/logs", {"name": name, "tail": tail})
    logs = payload.get("logs")
    if not isinstance(logs, str):
        raise AgentError("The agent returned an unreadable response.")
    return logs
