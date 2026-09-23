from django.db import migrations


def create_demo(apps, schema_editor):
    Project = apps.get_model("monitor", "Project")
    Project.objects.get_or_create(
        slug="demo",
        defaults={
            "name": "Demo",
            "dockerfile": "FROM nginx:alpine\n",
        },
    )


def remove_demo(apps, schema_editor):
    Project = apps.get_model("monitor", "Project")
    Project.objects.filter(slug="demo").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("monitor", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_demo, remove_demo),
    ]
