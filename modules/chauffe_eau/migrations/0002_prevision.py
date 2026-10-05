# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

from django.db import migrations, models


class Migration(migrations.Migration):
    """Prévision figée au démarrage de chaque chauffe, pour la comparer au réel.

    Les chauffes déjà enregistrées gardent ces champs vides : leur prévision
    n'a pas été conservée, elles restent hors comparaison.
    """

    dependencies = [("chauffe_eau", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="chauffesession",
            name="prevu_heure",
            field=models.CharField(blank=True, default="", max_length=5,
                                   verbose_name="heure prévue (HH:MM)"),
        ),
        migrations.AddField(
            model_name="chauffesession",
            name="prevu_duree_min",
            field=models.IntegerField(blank=True, null=True,
                                      verbose_name="durée prévue (min)"),
        ),
        migrations.AddField(
            model_name="chauffesession",
            name="prevu_wh",
            field=models.FloatField(blank=True, null=True,
                                    verbose_name="énergie prévue (Wh)"),
        ),
    ]
