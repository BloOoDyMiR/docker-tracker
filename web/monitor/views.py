import re

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.contrib.messages import get_messages
from django.http import Http404, JsonResponse
from django.middleware.csrf import get_token
from django.views.decorators.http import require_POST
from django.shortcuts import redirect

from .models import ContainerNote
from .present import present_container
from .rendering import render_page
from .services import agent as agent_api
from .services.agent import AgentError
from .services.llm import LLMError, LLMNotConfigured, configured, explain

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


def _session_key(name):
    return f"explain:{name}"


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
    key = _session_key(name)
    note = _note(name)
    if not configured():
        request.session[key] = {"configured": False, "answer": "", "error": ""}
        return redirect("monitor:explain", name=name)
    try:
        payload = agent_api.get_container(name)
        logs = agent_api.get_logs(name)
    except AgentError as exc:
        request.session[key] = {"configured": True, "answer": "", "error": str(exc)}
        return redirect("monitor:explain", name=name)
    container = payload.get("container") if isinstance(payload, dict) else {}
    try:
        answer = explain(
            status=container if isinstance(container, dict) else {},
            logs=logs,
            dockerfile=note.dockerfile if note else "",
            env_names=_env_payload(note),
        )
    except LLMNotConfigured:
        request.session[key] = {"configured": False, "answer": "", "error": ""}
    except LLMError as exc:
        request.session[key] = {"configured": True, "answer": "", "error": str(exc)}
    else:
        request.session[key] = {"configured": True, "answer": answer, "error": ""}
    return redirect("monitor:explain", name=name)


@login_required
def explain_result(request, name):
    _require_allowed(request.user, name)
    return render_page(
        request,
        "explain.html",
        {
            "name": name,
            "saved": request.session.get(_session_key(name)),
            "llm_model": settings.LLM_MODEL,
        },
    )
