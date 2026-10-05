# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

"""Branche l'estimation de la chauffe sur le calcul de l'heure de démarrage.

Le calcul peut désormais prendre la durée et l'énergie de la chauffe à qui
connaît le ballon (besoin ``estimation_chauffe``), au lieu d'une durée fixe
par saison. Un besoin nouveau naît débranché : sans cette migration, rien
ne changerait tant que personne ne passe par Configuration → Liaisons.

On ne le branche que là où le chauffe-eau lit déjà sa prévision chez ce
module : c'est le signe que les deux travaillent ensemble sur cette
installation. Ailleurs, on ne devine pas — le besoin reste libre.

Brancher n'est pas forcer : tant que le fournisseur n'a pas assez de
chauffes mesurées, ou que le ballon sort de ce qu'il a déjà vu, il se tait
et le calcul garde ses réglages de saison.

``get_or_create`` : un branchement déjà choisi n'est jamais écrasé.
"""

from django.db import migrations

MODULE = "heure_demarrage"
CLE = "besoin_estimation_chauffe"
CIBLE = "chauffe_eau.estimation_chauffe"
TEMOIN = ("chauffe_eau", "besoin_prevision_chauffe", "heure_demarrage.creneau_retenu")


def brancher(apps, schema_editor):
    Setting = apps.get_model("core", "Setting")
    module, cle, valeur = TEMOIN
    if Setting.objects.filter(module=module, key=cle, value=valeur).exists():
        Setting.objects.get_or_create(module=MODULE, key=CLE, defaults={"value": CIBLE})


def debrancher(apps, schema_editor):
    Setting = apps.get_model("core", "Setting")
    Setting.objects.filter(module=MODULE, key=CLE, value=CIBLE).delete()


class Migration(migrations.Migration):

    dependencies = [("core", "0015_liaison_prevision_demain")]

    operations = [migrations.RunPython(brancher, debrancher)]
