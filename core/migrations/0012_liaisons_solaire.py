"""Branche les quatre liaisons restantes, à l'identique des imports retirés.

Même raison qu'en 0011 : un lien qui existait en dur devient débranché par
défaut une fois traduit en besoin. Sans cette migration, l'heure de
démarrage repartirait sur les heures creuses et l'onglet Solaire perdrait
sa courbe « réel » — silencieusement.

``get_or_create`` : un branchement déjà choisi n'est jamais écrasé.
"""

from django.db import migrations

LIAISONS = [
    # (module consommateur, besoin, fournisseur)
    ("heure_demarrage", "besoin_prevision_pv", "solcast.prevision_pv"),
    ("heure_demarrage", "besoin_tarifs_jour", "tempo.tarifs_jour"),
    ("solcast", "besoin_production_reelle", "enphase.production_reelle"),
    ("solcast", "besoin_creneau_chauffe", "heure_demarrage.creneau_retenu"),
]


def brancher(apps, schema_editor):
    Setting = apps.get_model("core", "Setting")
    for module, cle, cible in LIAISONS:
        Setting.objects.get_or_create(
            module=module, key=cle, defaults={"value": cible}
        )


def debrancher(apps, schema_editor):
    Setting = apps.get_model("core", "Setting")
    for module, cle, cible in LIAISONS:
        Setting.objects.filter(module=module, key=cle, value=cible).delete()


class Migration(migrations.Migration):

    dependencies = [("core", "0011_liaison_enphase_tarifs")]

    operations = [migrations.RunPython(brancher, debrancher)]
