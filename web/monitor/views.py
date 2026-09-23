import json
import re

from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.contrib.messages import get_messages
from django.db import close_old_connections
from django.http import Http404, JsonResponse, StreamingHttpResponse
from django.middleware.csrf import get_token
from django.views.decorators.http import require_POST

from .models import ContainerNote
from .present import present_container
from .rendering import render_page
from .services import agent as agent_api
from .services.agent import AgentError
from .services.llm import LLMError, LLMNotConfigured, configured, explain_chunks

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,200}$")


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
            "agent_error": agent_error,
            "sees_all": request.user.is_superuser,
        },
    )


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
        },
    )


@login_required
def container_logs(request, name):
    _require_allowed(request.user, name)
    try:
        logs = agent_api.get_logs(name)
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
