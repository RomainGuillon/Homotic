# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

"""Branche la prévision solaire de demain sur l'heure de démarrage.

Le plan des machines peut désormais reporter un cycle au lendemain (besoin
``prevision_pv_demain``). Un besoin nouveau naît débranché : sans cette
migration, le report resterait impossible tant que personne ne passe par
Configuration → Liaisons, et rien ne le signalerait — le cycle irait
simplement en heures creuses.

On ne le branche que là où la prévision du jour vient déjà du même
fournisseur : c'est le signe que c'est bien lui qui prévoit la production
de cette installation. Ailleurs, on ne devine pas — le besoin reste libre.

``get_or_create`` : un branchement déjà choisi n'est jamais écrasé.
"""

from django.db import migrations

MODULE = "heure_demarrage"
CLE = "besoin_prevision_pv_demain"
CIBLE = "solcast.prevision_pv_demain"
TEMOIN = ("besoin_prevision_pv", "solcast.prevision_pv")


def brancher(apps, schema_editor):
    Setting = apps.get_model("core", "Setting")
    cle, valeur = TEMOIN
    if Setting.objects.filter(module=MODULE, key=cle, value=valeur).exists():
        Setting.objects.get_or_create(module=MODULE, key=CLE, defaults={"value": CIBLE})


def debrancher(apps, schema_editor):
    Setting = apps.get_model("core", "Setting")
    Setting.objects.filter(module=MODULE, key=CLE, value=CIBLE).delete()


class Migration(migrations.Migration):

    dependencies = [("core", "0014_liaison_chauffe_eau_prevision")]

    operations = [migrations.RunPython(brancher, debrancher)]
