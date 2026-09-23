import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


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
