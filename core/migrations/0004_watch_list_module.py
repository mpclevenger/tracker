# Adds the watch list module switch.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0003_module_settings"),
    ]

    operations = [
        migrations.AddField(
            model_name="modulesettings",
            name="watch_list",
            field=models.BooleanField(default=True),
        ),
    ]
