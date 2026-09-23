"""Pure helpers for the Docker agent. No Docker client imports here."""

import re

PROJECT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
CONTAINER_ID_RE = re.compile(r"^[a-f0-9]{12,64}$")
CONTAINER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,200}$")


def visible(labels, project):
    if not project or not isinstance(labels, dict):
        return False
    return labels.get("track.project") == project


def summarize(*, container_id, name, image, state):
    state = state or {}
    health_block = state.get("Health") or {}
    health = health_block.get("Status") if health_block else None
    started = _real_time(state.get("StartedAt"))
    finished = _real_time(state.get("FinishedAt"))
    return {
        "id": container_id,
        "short_id": container_id[:12],
        "name": (name or "").lstrip("/"),
        "image": image or "",
        "status": state.get("Status") or "unknown",
        "health": health,
        "restart_count": state.get("RestartCount") or 0,
        "exit_code": state.get("ExitCode"),
        "oom_killed": bool(state.get("OOMKilled")),
        "started_at": started,
        "finished_at": finished,
    }


def cpu_percent(stats):
    try:
        cpu = stats["cpu_stats"]["cpu_usage"]["total_usage"]
        precpu = stats["precpu_stats"]["cpu_usage"]["total_usage"]
        system = stats["cpu_stats"]["system_cpu_usage"]
        presystem = stats["precpu_stats"]["system_cpu_usage"]
        online = stats["cpu_stats"].get("online_cpus") or len(
            stats["cpu_stats"]["cpu_usage"].get("percpu_usage") or []
        ) or 1
    except (KeyError, TypeError):
        return None
    cpu_delta = cpu - precpu
    system_delta = system - presystem
    if system_delta <= 0:
        return None
    return round((cpu_delta / system_delta) * online * 100.0, 1)


def memory_mb(stats):
    try:
        usage = stats["memory_stats"]["usage"]
        limit = stats["memory_stats"]["limit"]
    except (KeyError, TypeError):
        return None, None
    return round(usage / 1024 / 1024, 1), round(limit / 1024 / 1024, 1)


def _real_time(value):
    if not value or str(value).startswith("0001"):
        return None
    return value
