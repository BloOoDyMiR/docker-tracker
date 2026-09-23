from django.db import migrations


def create_note(apps, schema_editor):
    ContainerNote = apps.get_model("monitor", "ContainerNote")
    ContainerNote.objects.get_or_create(
        container_name="track-demo",
        defaults={"dockerfile": "FROM nginx:alpine\n"},
    )


def remove_note(apps, schema_editor):
    ContainerNote = apps.get_model("monitor", "ContainerNote")
    ContainerNote.objects.filter(container_name="track-demo").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("monitor", "0003_container_grants"),
    ]

    operations = [
        migrations.RunPython(create_note, remove_note),
    ]
