import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
ENV_PATH_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,120}$")


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


class Project(models.Model):
    name = models.CharField(max_length=120)
    slug = models.SlugField(
        max_length=80,
        unique=True,
        help_text="Must match the track.project label on the container.",
    )
    dockerfile = models.TextField(
        blank=True,
        help_text="Paste the Dockerfile from git or CI. The portal does not read it from the container.",
    )
    env_path = models.CharField(
        max_length=200,
        blank=True,
        help_text="File inside the projects folder, such as shop/.env. Not a server login.",
    )
    members = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="projects",
        help_text="These users can open the project. Superusers can open every project.",
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def clean(self):
        if not SLUG_RE.fullmatch(self.slug or ""):
            raise ValidationError(
                {
                    "slug": "Use letters, numbers, hyphens, and underscores. Start with a letter or number."
                }
            )
        if self.env_path:
            cleaned = clean_env_path(self.env_path)
            if not cleaned:
                raise ValidationError({"env_path": "Use a relative path such as shop/.env."})
            self.env_path = cleaned


class EnvName(models.Model):
    project = models.ForeignKey(Project, related_name="env_names", on_delete=models.CASCADE)
    name = models.CharField(max_length=120)
    is_set = models.BooleanField(
        default=False,
        help_text="Checked when CI reports this variable as present. Store the name only.",
    )

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["project", "name"], name="unique_env_name_per_project")
        ]
        verbose_name = "env name"

    def __str__(self):
        return self.name

    def clean(self):
        if not ENV_NAME_RE.fullmatch(self.name or ""):
            raise ValidationError({"name": "Use an environment variable name such as DATABASE_URL."})


class UserContainer(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="container_grants",
        on_delete=models.CASCADE,
    )
    container_name = models.CharField(max_length=255)

    class Meta:
        ordering = ["container_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "container_name"],
                name="unique_user_container",
            )
        ]

    def __str__(self):
        return self.container_name


class ContainerNote(models.Model):
    container_name = models.CharField(
        max_length=255,
        unique=True,
        help_text="Exact Docker container name, such as track-demo.",
    )
    dockerfile = models.TextField(
        blank=True,
        help_text="Paste the Dockerfile from git or CI. The portal does not read it from the container.",
    )

    class Meta:
        ordering = ["container_name"]

    def __str__(self):
        return self.container_name


class EnvRevision(models.Model):
    project = models.ForeignKey(
        Project,
        null=True,
        blank=True,
        related_name="env_revisions",
        on_delete=models.SET_NULL,
    )
    container_name = models.CharField(max_length=255)
    env_path = models.CharField(max_length=200, blank=True)
    compose_project = models.CharField(max_length=80, blank=True)
    body = models.TextField(help_text="The .env file after this save. Explain does not receive it.")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="env_revisions",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.container_name} @ {self.created_at:%Y-%m-%d %H:%M}"


class NoteEnv(models.Model):
    note = models.ForeignKey(ContainerNote, related_name="env_flags", on_delete=models.CASCADE)
    name = models.CharField(max_length=120)
    is_set = models.BooleanField(
        default=False,
        help_text="Checked when CI reports this variable as present. Store the name only.",
    )

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["note", "name"], name="unique_note_env_name")
        ]
        verbose_name = "env name"

    def __str__(self):
        return self.name

    def clean(self):
        if not ENV_NAME_RE.fullmatch(self.name or ""):
            raise ValidationError({"name": "Use an environment variable name such as DATABASE_URL."})


def set_user_containers(user, names):
    cleaned = []
    seen = set()
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        cleaned.append(name)
    UserContainer.objects.filter(user=user).exclude(container_name__in=cleaned).delete()
    existing = set(
        UserContainer.objects.filter(user=user).values_list("container_name", flat=True)
    )
    UserContainer.objects.bulk_create(
        [
            UserContainer(user=user, container_name=name)
            for name in cleaned
            if name not in existing
        ]
    )
