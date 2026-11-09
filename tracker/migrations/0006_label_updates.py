# Label and choice text updates.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tracker", "0005_us_stage_names"),
    ]

    operations = [
        migrations.AlterField(
            model_name="controlimpact",
            name="visibility",
            field=models.CharField(
                choices=[
                    ("internal", "Tech Risk only"),
                    ("owner", "Owner and Tech Risk"),
                    ("exec", "Executives and audit"),
                ],
                default="exec",
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="gateapproval",
            name="decision",
            field=models.CharField(
                choices=[
                    ("proceed", "Proceed"),
                    ("conditions", "Proceed with conditions"),
                    ("hold", "Hold (do not proceed)"),
                ],
                default="proceed",
                max_length=12,
            ),
        ),
        migrations.AlterField(
            model_name="gateapproval",
            name="stage",
            field=models.PositiveSmallIntegerField(
                choices=[
                    (0, "0. Intake"),
                    (1, "1. Mobilize & Design"),
                    (2, "2. Build & Configure"),
                    (3, "3. Test & Validate"),
                    (4, "4. Cutover & Go-live"),
                    (5, "5. Hypercare & Stabilize"),
                    (6, "6. Close"),
                ]
            ),
        ),
        migrations.AlterField(
            model_name="guidancepage",
            name="stage",
            field=models.PositiveSmallIntegerField(
                choices=[
                    (0, "0. Intake"),
                    (1, "1. Mobilize & Design"),
                    (2, "2. Build & Configure"),
                    (3, "3. Test & Validate"),
                    (4, "4. Cutover & Go-live"),
                    (5, "5. Hypercare & Stabilize"),
                    (6, "6. Close"),
                ]
            ),
        ),
        migrations.AlterField(
            model_name="initiative",
            name="expected_systems",
            field=models.TextField(
                blank=True, help_text="Expected systems and controls affected"
            ),
        ),
        migrations.AlterField(
            model_name="initiative",
            name="why_it_matters",
            field=models.TextField(
                blank=True, help_text="Why it matters to risk and controls"
            ),
        ),
        migrations.AlterField(
            model_name="overlay",
            name="visibility",
            field=models.CharField(
                choices=[
                    ("internal", "Tech Risk only"),
                    ("owner", "Owner and Tech Risk"),
                    ("exec", "Executives and audit"),
                ],
                default="internal",
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="phase",
            name="stage",
            field=models.PositiveSmallIntegerField(
                choices=[
                    (0, "0. Intake"),
                    (1, "1. Mobilize & Design"),
                    (2, "2. Build & Configure"),
                    (3, "3. Test & Validate"),
                    (4, "4. Cutover & Go-live"),
                    (5, "5. Hypercare & Stabilize"),
                    (6, "6. Close"),
                ],
                default=2,
            ),
        ),
        migrations.AlterField(
            model_name="ragoverride",
            name="cleared_by",
            field=models.CharField(
                blank=True,
                choices=[
                    ("manual", "Cleared manually"),
                    ("auto_expired", "Expired (calculated status worsened)"),
                ],
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="riskitem",
            name="visibility",
            field=models.CharField(
                choices=[
                    ("internal", "Tech Risk only"),
                    ("owner", "Owner and Tech Risk"),
                    ("exec", "Executives and audit"),
                ],
                default="exec",
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="stagecheck",
            name="basis",
            field=models.CharField(
                blank=True,
                choices=[
                    ("reviewed", "Evidence reviewed"),
                    ("observed", "Observed by Tech Risk"),
                    ("attested", "Owner attestation"),
                ],
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="stagecheck",
            name="stage",
            field=models.PositiveSmallIntegerField(
                choices=[
                    (0, "0. Intake"),
                    (1, "1. Mobilize & Design"),
                    (2, "2. Build & Configure"),
                    (3, "3. Test & Validate"),
                    (4, "4. Cutover & Go-live"),
                    (5, "5. Hypercare & Stabilize"),
                    (6, "6. Close"),
                ]
            ),
        ),
        migrations.AlterField(
            model_name="update",
            name="visibility",
            field=models.CharField(
                choices=[
                    ("internal", "Tech Risk only"),
                    ("owner", "Owner and Tech Risk"),
                    ("exec", "Executives and audit"),
                ],
                default="exec",
                max_length=16,
            ),
        ),
    ]
