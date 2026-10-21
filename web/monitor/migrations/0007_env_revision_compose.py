from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("monitor", "0006_project_env_path"),
    ]

    operations = [
        migrations.AddField(
            model_name="envrevision",
            name="compose_project",
            field=models.CharField(blank=True, max_length=80),
        ),
        migrations.AddField(
            model_name="envrevision",
            name="env_path",
            field=models.CharField(blank=True, max_length=200),
        ),
    ]
