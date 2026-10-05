# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

"""Branche la prévision de chauffe sur le suivi du chauffe-eau.

Le suivi compare désormais chaque chauffe à ce qui était prévu (besoin
``prevision_chauffe``). Un besoin nouveau naît débranché : sans cette
migration, la comparaison resterait vide tant que personne ne passe par
Configuration → Liaisons.

On ne le branche que là où le suivi lit déjà son heure de chauffe chez le
même fournisseur : c'est le signe que ce module calcule bien la chauffe de
cette installation. Ailleurs, on ne devine pas — le besoin reste libre.

``get_or_create`` : un branchement déjà choisi n'est jamais écrasé.
"""

from django.db import migrations

MODULE = "chauffe_eau"
CLE = "besoin_prevision_chauffe"
CIBLE = "heure_demarrage.creneau_retenu"
TEMOIN = ("besoin_heure_chauffe_prevue", "heure_demarrage.heure_demarrage")


def brancher(apps, schema_editor):
    Setting = apps.get_model("core", "Setting")
    cle, valeur = TEMOIN
    if Setting.objects.filter(module=MODULE, key=cle, value=valeur).exists():
        Setting.objects.get_or_create(module=MODULE, key=CLE, defaults={"value": CIBLE})


def debrancher(apps, schema_editor):
    Setting = apps.get_model("core", "Setting")
    Setting.objects.filter(module=MODULE, key=CLE, value=CIBLE).delete()


class Migration(migrations.Migration):

    dependencies = [("core", "0013_liaison_chauffe_eau_heure")]

    operations = [migrations.RunPython(brancher, debrancher)]
