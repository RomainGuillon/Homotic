from django.db import migrations, models


class Migration(migrations.Migration):
    """Hauteur libre (px) au lieu d'une liste de paliers, largeur « un quart »."""

    dependencies = [
        ("core", "0008_dashboardblock_height"),
    ]

    operations = [
        migrations.AlterField(
            model_name="dashboardblock",
            name="height",
            field=models.IntegerField(
                default=0,
                help_text="0 = hauteur du contenu ; sinon hauteur fixe du bloc.",
                verbose_name="hauteur (px, 0 = automatique)",
            ),
        ),
        migrations.AlterField(
            model_name="dashboardblock",
            name="width",
            field=models.IntegerField(
                choices=[
                    (3, "Un quart"),
                    (4, "Un tiers"),
                    (6, "Moitié"),
                    (8, "Deux tiers"),
                    (12, "Pleine largeur"),
                ],
                default=6,
                verbose_name="largeur",
            ),
        ),
    ]
