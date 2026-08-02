from django.db import migrations, models


class Migration(migrations.Migration):
    """Hauteur maximale d'un bloc du tableau de bord (0 = automatique)."""

    dependencies = [
        ("core", "0007_dashboardblock"),
    ]

    operations = [
        migrations.AddField(
            model_name="dashboardblock",
            name="height",
            field=models.IntegerField(
                choices=[
                    (0, "Automatique"),
                    (200, "Très courte (200 px)"),
                    (300, "Courte (300 px)"),
                    (420, "Moyenne (420 px)"),
                    (560, "Haute (560 px)"),
                    (760, "Très haute (760 px)"),
                ],
                default=0,
                verbose_name="hauteur maximale",
            ),
        ),
    ]
