import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("monitor", "0005_env_revision"),
    ]

    operations = [
        migrations.AddField(
            model_name="project",
            name="env_path",
            field=models.CharField(
                blank=True,
                help_text="File inside the projects folder, such as shop/.env. Not a server login.",
                max_length=200,
            ),
        ),
        migrations.AddField(
            model_name="envrevision",
            name="project",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="env_revisions",
                to="monitor.project",
            ),
        ),
        migrations.AlterField(
            model_name="envrevision",
            name="body",
            field=models.TextField(help_text="The .env file after this save. Explain does not receive it."),
        ),
    ]
