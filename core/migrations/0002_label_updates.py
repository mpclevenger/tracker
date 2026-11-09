# Label and choice text updates.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="roleassignment",
            name="role",
            field=models.CharField(
                choices=[
                    ("tech_risk", "Tech Risk"),
                    ("owner", "Owner"),
                    ("senior_management", "Senior Management"),
                    ("auditor", "Auditor"),
                ],
                max_length=32,
            ),
        ),
    ]
