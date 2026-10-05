# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

from django.db import migrations, models


class Migration(migrations.Migration):
    """Température du ballon et heure du calcul, figées avec la prévision.

    La prévision d'une chauffe repose sur la température du ballon au
    moment du calcul, parfois des heures avant le départ. En la gardant, on
    peut mesurer de combien le ballon bouge entre les deux.

    Les chauffes déjà enregistrées gardent ces champs vides : la température
    qu'avait le ballon au moment de leur calcul n'a pas été conservée.
    """

    dependencies = [("chauffe_eau", "0003_fin_estimee")]

    operations = [
        migrations.AddField(
            model_name="chauffesession",
            name="prevu_temp",
            field=models.FloatField(
                blank=True, null=True,
                verbose_name="température au moment de la prévision (°C)"),
        ),
        migrations.AddField(
            model_name="chauffesession",
            name="prevu_calcule_a",
            field=models.DateTimeField(
                blank=True, null=True, verbose_name="prévision calculée à"),
        ),
    ]
