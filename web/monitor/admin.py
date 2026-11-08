import re

from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.models import User

from .models import ContainerNote, EnvName, EnvRevision, NoteEnv, Project, set_user_containers
from .services import agent as agent_api
from .services.agent import AgentError

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,200}$")


class EnvNameInline(admin.TabularInline):
    model = EnvName
    extra = 1
    fields = ("name", "is_set")


class NoteEnvInline(admin.TabularInline):
    model = NoteEnv
    extra = 1
    fields = ("name", "is_set")


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
    class Meta:
        model = Project
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        current = self.instance.env_path if self.instance and self.instance.pk else ""
        self.fields["env_path"].widget = forms.Select(choices=env_path_choices(current))
        self.fields["env_path"].help_text = (
            "Choose a folder from the env directory. Leave empty if this project has no file yet."
        )


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    form = ProjectAdminForm
    list_display = ("name", "slug", "env_path")
    search_fields = ("name", "slug", "env_path")
    prepopulated_fields = {"slug": ("name",)}
    filter_horizontal = ("members",)
    inlines = [EnvNameInline]
    fieldsets = (
        (None, {"fields": ("name", "slug", "env_path", "members")}),
        ("Dockerfile", {"fields": ("dockerfile",)}),
    )


@admin.register(EnvName)
class EnvNameAdmin(admin.ModelAdmin):
    list_display = ("name", "project", "is_set")
    list_filter = ("is_set", "project")
    search_fields = ("name", "project__name")


@admin.register(EnvRevision)
class EnvRevisionAdmin(admin.ModelAdmin):
    list_display = ("created_at", "created_by", "compose_project", "container_name", "env_path")
    search_fields = ("container_name", "compose_project", "env_path", "project__name")
    readonly_fields = ("project", "compose_project", "env_path", "container_name", "body", "created_by", "created_at")

    def has_add_permission(self, request):
        return False


@admin.register(ContainerNote)
class ContainerNoteAdmin(admin.ModelAdmin):
    list_display = ("container_name",)
    search_fields = ("container_name",)
    inlines = [NoteEnvInline]


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
