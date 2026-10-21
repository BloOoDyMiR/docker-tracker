import json
import re

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.contrib.messages import get_messages
from django.db import close_old_connections
from django.http import Http404, JsonResponse, StreamingHttpResponse
from django.middleware.csrf import get_token
from django.shortcuts import redirect
from django.utils import timezone
from django.views.decorators.http import require_POST

from .envfile import MAX_ENV_CHARS, MAX_REVISIONS, diff_env, parse_env
from .models import SLUG_RE, ContainerNote, EnvRevision, Project
from .present import present_container
from .rendering import render_page
from .services import agent as agent_api
from .services.agent import AgentError
from .services.llm import LLMError, LLMNotConfigured, configured, explain_chunks

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,200}$")
LOG_TAILS = {200, 500, 1000, 2000}


class PageLoginView(LoginView):
    template_name = "login.html"
    redirect_authenticated_user = True

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["csrf_token"] = get_token(self.request)
        context["user"] = self.request.user
        context["messages"] = [str(message) for message in get_messages(self.request)]
        return context


def _check_name(name):
    if not NAME_RE.fullmatch(name or ""):
        raise Http404("Container not found")


def _allowed(user, name):
    if user.is_superuser:
        return True
    return user.container_grants.filter(container_name=name).exists()


def _require_allowed(user, name):
    _check_name(name)
    if not _allowed(user, name):
        raise Http404("Container not found")


def _visible(user, rows):
    if user.is_superuser:
        return rows
    names = set(user.container_grants.values_list("container_name", flat=True))
    return [row for row in rows if row.get("name") in names]


def _note(name):
    return ContainerNote.objects.filter(container_name=name).prefetch_related("env_flags").first()


def _env_payload(note):
    if note is None:
        return []
    return [
        {"name": item.name, "state": "set" if item.is_set else "missing"}
        for item in note.env_flags.all()
    ]


NOT_CONFIGURED = (
    "Set the model server in the .env file, then restart Django. "
    "A local server can leave the token empty.\n"
    "LLM_API_BASE=http://192.168.0.200:8880/v1\n"
    "LLM_API_TOKEN=\n"
    "LLM_MODEL=qwen"
)


def _sse(payload):
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()


def _event_stream(chunks):
    response = StreamingHttpResponse(chunks, content_type="text/event-stream; charset=utf-8")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


def _empty_detail(request, name, error):
    return render_page(
        request,
        "container_detail.html",
        {
            "name": name,
            "container": None,
            "stats": None,
            "logs": "",
            "note": _note(name),
            "error": error,
            "llm_configured": configured(),
            "env_revisions": [],
            "env_rows": [],
            "env_old_id": None,
            "env_new_id": None,
        },
    )


@login_required
def dashboard(request):
    agent_error = None
    containers = []
    try:
        containers = [present_container(row) for row in _visible(request.user, agent_api.list_containers())]
    except AgentError as exc:
        agent_error = str(exc)
    return render_page(
        request,
        "dashboard.html",
        {
            "containers": containers,
            "groups": _groups(containers),
            "agent_error": agent_error,
            "sees_all": request.user.is_superuser,
        },
    )


def _groups(containers):
    buckets = {}
    for container in containers:
        name = container.get("compose_project") or ""
        buckets.setdefault(name, []).append(container)
    groups = [
        {"name": name, "containers": buckets[name]}
        for name in sorted((key for key in buckets if key), key=str.lower)
    ]
    if "" in buckets:
        groups.append({"name": "Other", "containers": buckets[""]})
    return groups


@login_required
def container_detail(request, name):
    _require_allowed(request.user, name)
    try:
        payload = agent_api.get_container(name)
        logs = agent_api.get_logs(name)
    except AgentError as exc:
        return _empty_detail(request, name, str(exc))
    container = payload.get("container") if isinstance(payload, dict) else None
    stats = payload.get("stats") if isinstance(payload, dict) else None
    if not isinstance(container, dict) or container.get("name") != name:
        return _empty_detail(request, name, "The agent returned an unreadable response.")
    if not isinstance(stats, dict):
        stats = {"cpu_percent": None, "mem_usage_mb": None, "mem_limit_mb": None}
    return render_page(
        request,
        "container_detail.html",
        {
            "name": name,
            "container": present_container(container),
            "stats": stats,
            "logs": logs,
            "note": _note(name),
            "error": None,
            "llm_configured": configured(),
            **_env_context(request, container),
        },
    )


def _project_for(container):
    slug = (container or {}).get("project") or ""
    if not SLUG_RE.fullmatch(slug):
        return None
    return Project.objects.filter(slug=slug).first()


def _revision_label(revision):
    local = timezone.localtime(revision.created_at)
    author = revision.created_by.get_username() if revision.created_by_id else "unknown"
    return {
        "id": revision.id,
        "label": f"{local:%Y-%m-%d %H:%M} · {author} · {revision.container_name}",
        "when": f"{local:%Y-%m-%d %H:%M}",
        "who": author,
        "container": revision.container_name,
    }


def _chosen_id(raw, allowed):
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value in allowed else None


def _file_target(container):
    container = container or {}
    compose = container.get("compose_project") or ""
    env_path = container.get("env_path") or ""
    project = _project_for(container)
    if compose and env_path:
        return {"path": env_path, "compose": compose, "project": None}
    if compose and not env_path:
        return {"path": "", "compose": compose, "project": None, "outside": True}
    if project is not None and project.env_path:
        return {"path": project.env_path, "compose": "", "project": project}
    slug = container.get("project") or ""
    return {"path": "", "compose": "", "project": project, "slug": slug}


def _revisions_for(target):
    if target.get("path") and target.get("compose"):
        found = EnvRevision.objects.filter(env_path=target["path"])
    elif target.get("project") is not None:
        found = EnvRevision.objects.filter(project=target["project"])
    else:
        return []
    return list(found.select_related("created_by")[:MAX_REVISIONS])


def _env_context(request, container):
    target = _file_target(container)
    project = target.get("project")
    revisions = _revisions_for(target)
    file_text = ""
    missing = False
    env_error = None
    editable = bool(target.get("path"))
    if target.get("outside"):
        env_error = "This stack's folder is outside the projects directory, so its .env cannot be opened."
        editable = False
    elif target.get("compose") and target.get("path"):
        pass
    elif not (container or {}).get("project") and not target.get("compose"):
        env_error = "This container has no project label, so there is no .env file to open."
        editable = False
    elif project is None and not target.get("path"):
        env_error = "No project matches this container's label."
        editable = False
    elif not target.get("path"):
        env_error = "No .env path is set for this project."
        editable = False
    if editable:
        try:
            file_text, missing = agent_api.read_project_env(target["path"])
        except AgentError as exc:
            env_error = str(exc)
            editable = False
    by_id = {revision.id: revision for revision in revisions}
    new_raw = request.GET.get("new")
    new_id = _chosen_id(new_raw, by_id)
    if editable and (new_raw == "file" or (new_id is None and new_raw in (None, ""))):
        new_id = "file"
    elif new_id is None:
        new_id = "file" if editable else None
    old_id = _chosen_id(request.GET.get("old"), by_id)
    if old_id is None and "old" not in request.GET and revisions and new_id == "file":
        old_id = revisions[0].id
    if old_id == new_id:
        old_id = None
    old_body = by_id[old_id].body if old_id in by_id else ""
    if new_id == "file":
        new_body = file_text
    elif new_id in by_id:
        new_body = by_id[new_id].body
    else:
        new_body = ""
    rows = diff_env(parse_env(old_body), parse_env(new_body)) if editable else []
    labels = [_revision_label(revision) for revision in revisions]
    return {
        "env_project": project,
        "env_compose": target.get("compose") or "",
        "env_path": target.get("path") or "",
        "env_error": env_error,
        "env_missing": missing,
        "env_editable": editable,
        "env_revisions": labels,
        "env_old_id": old_id,
        "env_new_id": new_id,
        "env_rows": rows,
        "live_env": file_text if editable else "",
    }


@login_required
@require_POST
def env_save(request, name):
    _require_allowed(request.user, name)
    try:
        payload = agent_api.get_container(name)
    except AgentError as exc:
        messages.error(request, str(exc))
        return redirect("monitor:container_detail", name=name)
    container = payload.get("container") if isinstance(payload, dict) else None
    target = _file_target(container if isinstance(container, dict) else None)
    if not target.get("path"):
        messages.error(request, "This container has no .env file configured.")
        return redirect("monitor:container_detail", name=name)
    body = request.POST.get("body", "")
    if len(body) > MAX_ENV_CHARS:
        messages.error(request, "That env file is too large.")
        return redirect("monitor:container_detail", name=name)
    if not parse_env(body):
        messages.error(request, "Paste at least one NAME=value line.")
        return redirect("monitor:container_detail", name=name)
    try:
        agent_api.write_project_env(target["path"], body)
    except AgentError as exc:
        messages.error(request, str(exc))
        return redirect("monitor:container_detail", name=name)
    EnvRevision.objects.create(
        project=target.get("project"),
        env_path=target["path"],
        compose_project=target.get("compose") or "",
        container_name=name,
        body=body,
        created_by=request.user,
    )
    scope = (
        EnvRevision.objects.filter(env_path=target["path"])
        if target.get("compose")
        else EnvRevision.objects.filter(project=target.get("project"))
    )
    stale = list(scope.order_by("-created_at", "-id").values_list("id", flat=True)[MAX_REVISIONS:])
    if stale:
        EnvRevision.objects.filter(id__in=stale).delete()
    messages.success(
        request,
        f"Saved {target['path']}. Recreate the stack when you want this file to take effect.",
    )
    return redirect("monitor:container_detail", name=name)


def _log_tail(request):
    try:
        tail = int(request.GET.get("tail", "200"))
    except (TypeError, ValueError):
        return 200
    return tail if tail in LOG_TAILS else 200


@login_required
def container_logs(request, name):
    _require_allowed(request.user, name)
    try:
        logs = agent_api.get_logs(name, tail=_log_tail(request))
    except AgentError as exc:
        return JsonResponse({"error": str(exc)}, status=502)
    return JsonResponse({"logs": logs})


@login_required
@require_POST
def explain_submit(request, name):
    _require_allowed(request.user, name)
    note = _note(name)
    if not configured():
        return _event_stream(iter([_sse({"error": NOT_CONFIGURED})]))
    try:
        payload = agent_api.get_container(name)
        logs = agent_api.get_logs(name)
    except AgentError as exc:
        return _event_stream(iter([_sse({"error": str(exc)})]))
    container = payload.get("container") if isinstance(payload, dict) else {}
    status = container if isinstance(container, dict) else {}
    dockerfile = note.dockerfile if note else ""
    env_names = _env_payload(note)

    def chunks():
        close_old_connections()
        total = 0
        try:
            for piece in explain_chunks(
                status=status,
                logs=logs,
                dockerfile=dockerfile,
                env_names=env_names,
            ):
                if not piece:
                    continue
                room = 8000 - total
                if room <= 0:
                    break
                text = piece[:room]
                total += len(text)
                yield _sse({"text": text})
        except LLMNotConfigured:
            yield _sse({"error": NOT_CONFIGURED})
            return
        except LLMError as exc:
            yield _sse({"error": str(exc)})
            return
        if total == 0:
            yield _sse({"error": "The model returned an empty response."})
            return
        yield _sse({"done": True})

    return _event_stream(chunks())
