import re
from urllib.parse import urlencode

from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.template.response import TemplateResponse
from django.urls import reverse

from .models import SLUG_RE, EnvRevision, Project, set_user_containers
from .services import agent as agent_api
from .services.agent import AgentError

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,200}$")


def discovered_stacks():
    """Compose project names the home page already uses as stack headings."""
    try:
        rows = agent_api.list_containers()
    except AgentError as exc:
        return [], str(exc)
    found = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = (row.get("compose_project") or "").strip()
        if not name or name in seen or not SLUG_RE.fullmatch(name):
            continue
        seen.add(name)
        found.append(name)
    found.sort(key=str.lower)
    return found, ""


def env_path_choices(current=""):
    choices = [("", "—")]
    try:
        files = agent_api.list_env_files()
    except AgentError:
        files = []
    for path in files:
        choices.append((path, path))
    if current and current not in {value for value, _label in choices}:
        choices.append((current, current))
    return choices


class ProjectAdminForm(forms.ModelForm):
    stack = forms.ChoiceField(required=False, label="Project")
    manual_name = forms.CharField(
        required=False,
        label="Or type a project name",
        max_length=80,
        help_text="Use this when the stack is not running yet, or it has no Compose name.",
    )

    class Meta:
        model = Project
        fields = ("env_path",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        stacks, warning = discovered_stacks()
        current = self.instance.slug if self.instance and self.instance.pk else ""
        posted = ""
        if self.is_bound:
            posted = (self.data.get(self.add_prefix("stack")) or "").strip()
        initial_stack = (self.initial.get("stack") or "").strip()
        choices = [("", "— Select a running project —")]
        seen = {""}
        for name in stacks:
            choices.append((name, name))
            seen.add(name)
        for name in (current, posted, initial_stack):
            if name and name not in seen and SLUG_RE.fullmatch(name):
                choices.append((name, name))
                seen.add(name)
        self.fields["stack"].choices = choices
        if current:
            self.fields["stack"].initial = current
        if warning:
            self.fields["stack"].help_text = (
                f"{warning} You can still type a project name below."
            )
        else:
            self.fields["stack"].help_text = "These are the stacks on the home page."
        current_env = self.instance.env_path if self.instance and self.instance.pk else ""
        self.fields["env_path"].widget = forms.Select(choices=env_path_choices(current_env))
        self.fields["env_path"].help_text = "Choose the env folder for the project you selected."

    def clean(self):
        cleaned = super().clean()
        stack = (cleaned.get("stack") or "").strip()
        manual = (cleaned.get("manual_name") or "").strip()
        chosen = manual or stack
        if not chosen:
            raise ValidationError("Select a project, or type a name.")
        if not SLUG_RE.fullmatch(chosen):
            self.add_error(
                "manual_name",
                "Use letters, numbers, hyphens, and underscores. Start with a letter or number.",
            )
            return cleaned
        taken = Project.objects.filter(slug=chosen)
        if self.instance.pk:
            taken = taken.exclude(pk=self.instance.pk)
        if taken.exists():
            raise ValidationError("That project is already in the list. Open it and assign the env file there.")
        self.instance.slug = chosen
        self.instance.name = chosen
        return cleaned


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    form = ProjectAdminForm
    change_list_template = "admin/monitor/project/stack_list.html"
    fieldsets = ((None, {"fields": ("stack", "manual_name", "env_path")}),)

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        stack = (request.GET.get("stack") or "").strip()
        if SLUG_RE.fullmatch(stack):
            initial["stack"] = stack
        return initial

    def changelist_view(self, request, extra_context=None):
        if not self.has_view_or_change_permission(request):
            raise PermissionDenied
        stacks, warning = discovered_stacks()
        running = set(stacks)
        saved = {project.slug: project for project in Project.objects.all()}
        rows = []
        for name in sorted(running | set(saved), key=str.lower):
            project = saved.get(name)
            if project is not None:
                url = reverse("admin:monitor_project_change", args=[project.pk])
                env_path = project.env_path
            elif self.has_add_permission(request):
                url = f"{reverse('admin:monitor_project_add')}?{urlencode({'stack': name})}"
                env_path = ""
            else:
                url = ""
                env_path = ""
            rows.append(
                {
                    "name": name,
                    "env_path": env_path,
                    "source": "Running" if name in running else "Added by hand",
                    "url": url,
                }
            )
        context = {
            **self.admin_site.each_context(request),
            "title": "Projects",
            "subtitle": "Select a project, then assign its env file.",
            "rows": rows,
            "warning": warning,
            "opts": self.model._meta,
            "add_url": reverse("admin:monitor_project_add") if self.has_add_permission(request) else "",
        }
        return TemplateResponse(request, self.change_list_template, context)


@admin.register(EnvRevision)
class EnvRevisionAdmin(admin.ModelAdmin):
    list_display = ("created_at", "created_by", "compose_project", "container_name", "env_path")
    search_fields = ("container_name", "compose_project", "env_path", "project__name")
    readonly_fields = ("project", "compose_project", "env_path", "container_name", "body", "created_by", "created_at")

    def has_add_permission(self, request):
        return False


def _container_choices():
    choices = []
    warning = ""
    try:
        for row in agent_api.list_containers():
            name = row.get("name") or ""
            if not NAME_RE.fullmatch(name):
                continue
            status = row.get("status") or "unknown"
            image = row.get("image") or ""
            choices.append((name, f"{name} — {status} — {image}"))
    except AgentError as exc:
        warning = str(exc)
    return choices, warning


admin.site.unregister(User)


@admin.register(User)
class TrackUserAdmin(DjangoUserAdmin):
    change_form_template = "admin/track_user_change_form.html"

    def render_change_form(self, request, context, add=False, change=False, form_url="", obj=None):
        choices, warning = _container_choices()
        known = {value for value, _label in choices}
        if obj is not None:
            for name in obj.container_grants.values_list("container_name", flat=True):
                if name not in known:
                    choices.append((name, f"{name} — saved, not on the host right now"))
                    known.add(name)
        choices.sort(key=lambda item: item[0])
        if request.method == "POST":
            selected = request.POST.getlist("container_grants")
        elif obj is not None:
            selected = list(obj.container_grants.values_list("container_name", flat=True))
        else:
            selected = []
        context.update(
            {
                "container_choices": choices,
                "selected_containers": selected,
                "container_warning": warning,
            }
        )
        return super().render_change_form(request, context, add, change, form_url, obj)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        names = [name for name in request.POST.getlist("container_grants") if NAME_RE.fullmatch(name)]
        set_user_containers(form.instance, names)
