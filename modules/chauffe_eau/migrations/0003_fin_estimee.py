# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

from django.db import migrations, models


def relire_les_chauffes(apps, schema_editor):
    """Rejoue la lecture des relevés sur les chauffes déjà enregistrées.

    Tant que leurs relevés minute existent (ils sont purgés après 60 jours),
    on peut leur donner ce que les nouvelles reçoivent à la clôture : la
    durée réelle estimée, l'énergie intégrée jusque-là, et le signalement
    d'une eau tirée en cours de chauffe. Sans cela le modèle de durée
    partirait d'un historique arrondi à la dizaine de minutes.

    Les chauffes dont les relevés ont été purgés restent telles quelles, y
    compris celle que la purge a coupée en deux : relue sur ses seuls
    relevés restants, elle perdrait l'énergie du début de chauffe.
    ``planifiee`` reste vide partout : on ne sait plus dire, après coup, si
    une ancienne chauffe est partie à l'heure prévue.
    """
    from datetime import timedelta

    from modules.chauffe_eau.fonctions import releves

    ChauffeSession = apps.get_model("chauffe_eau", "ChauffeSession")
    for session in ChauffeSession.objects.filter(fin__isnull=False):
        mesures = list(session.mesures.order_by("quand"))
        if len(mesures) < 2:
            continue
        if mesures[0].quand - session.debut > timedelta(minutes=2):
            continue  # relevés du début purgés : série incomplète
        lu = releves.bilan(mesures, session.debut)
        session.duree_estimee_min = lu["duree_estimee_min"]
        session.tirage = lu["tirage"]
        session.energie_elec_wh = lu["elec_wh"]
        session.energie_pac_wh = lu["pac_wh"]
        session.energie_wh = round(lu["elec_wh"] + lu["pac_wh"], 1)
        session.save()


class Migration(migrations.Migration):
    """Fin de chauffe estimée, eau tirée, chauffe planifiée.

    Trois champs dont le modèle de durée a besoin pour apprendre des bonnes
    chauffes, avec leur vraie durée — voir fonctions/releves.py et
    fonctions/modele.py.
    """

    dependencies = [("chauffe_eau", "0002_prevision")]

    operations = [
        migrations.AddField(
            model_name="chauffesession",
            name="duree_estimee_min",
            field=models.FloatField(blank=True, null=True,
                                    verbose_name="durée estimée (min)"),
        ),
        migrations.AddField(
            model_name="chauffesession",
            name="tirage",
            field=models.BooleanField(default=False,
                                      verbose_name="eau tirée pendant la chauffe"),
        ),
        migrations.AddField(
            model_name="chauffesession",
            name="planifiee",
            field=models.BooleanField(blank=True, null=True,
                                      verbose_name="partie à l'heure prévue"),
        ),
        migrations.RunPython(relire_les_chauffes, migrations.RunPython.noop),
    ]
