"""Pure helpers for the Docker agent. No Docker client imports here."""

import os
import re
from pathlib import Path

PROJECT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
CONTAINER_ID_RE = re.compile(r"^[a-f0-9]{12,64}$")
CONTAINER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,200}$")
ENV_PATH_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,120}$")
COMPOSE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,80}$")
MAX_ENV_BYTES = 100_000


def visible(labels, project):
    if not project or not isinstance(labels, dict):
        return False
    return labels.get("track.project") == project


def clean_env_path(value):
    """Relative project file, such as shop/.env. Rejects absolute paths and '..'."""
    text = (value or "").strip().replace("\\", "/")
    if not text or text.startswith("/"):
        return ""
    parts = text.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return ""
    dirs, name = parts[:-1], parts[-1]
    if any(not ENV_PATH_PART.fullmatch(part) for part in dirs):
        return ""
    if name != ".env" and not (name.endswith(".env") and ENV_PATH_PART.fullmatch(name[: -len(".env")])):
        return ""
    return "/".join(parts)


def compose_env_path(working_dir, host_root):
    """`.env` inside a Compose project folder, relative to the projects directory."""
    working = (working_dir or "").replace("\\", "/").rstrip("/")
    root = (host_root or "").replace("\\", "/").rstrip("/")
    if not working or not root:
        return ""
    if working == root:
        relative = ".env"
    elif working.startswith(root + "/"):
        relative = working[len(root) + 1 :] + "/.env"
    else:
        return ""
    return clean_env_path(relative)


def env_file(root, relative):
    cleaned = clean_env_path(relative)
    if not root or not cleaned:
        return None
    base = Path(root).resolve()
    target = (base / cleaned).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        return None
    return target


def read_env_file(root, relative):
    target = env_file(root, relative)
    if target is None:
        raise ValueError("bad path")
    if target.is_dir():
        raise ValueError("bad path")
    if not target.is_file():
        return "", True
    data = target.read_bytes()
    if len(data) > MAX_ENV_BYTES:
        raise ValueError("too large")
    return data.decode("utf-8", "replace"), False


def write_env_file(root, relative, body):
    target = env_file(root, relative)
    if target is None or target.is_dir():
        raise ValueError("bad path")
    if not target.parent.is_dir():
        raise FileNotFoundError(target.parent)
    raw = body.encode("utf-8")
    if len(raw) > MAX_ENV_BYTES:
        raise ValueError("too large")
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_bytes(raw)
    try:
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def summarize(*, container_id, name, image, state, labels=None, host_projects_dir=""):
    state = state or {}
    labels = labels or {}
    project = labels.get("track.project") or ""
    if not PROJECT_RE.fullmatch(project):
        project = ""
    compose_project = labels.get("com.docker.compose.project") or ""
    if not COMPOSE_NAME_RE.fullmatch(compose_project):
        compose_project = ""
    env_path = ""
    if compose_project:
        env_path = compose_env_path(
            labels.get("com.docker.compose.project.working_dir") or "",
            host_projects_dir,
        )
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
        "project": project,
        "compose_project": compose_project,
        "env_path": env_path,
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


def format_env(items):
    lines = []
    for item in items or []:
        if not isinstance(item, str) or "=" not in item:
            continue
        name, value = item.split("=", 1)
        name = name.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            continue
        lines.append(f"{name}={value}")
    lines.sort()
    return "\n".join(lines)


def _real_time(value):
    if not value or str(value).startswith("0001"):
        return None
    return value
