"""Dernier couplage traduit : le suivi du chauffe-eau et l'heure prévue.

Ce lien-là ne passait pas par un import mais par le nom d'une variable
globale écrit en dur (``heure_demarrage_chauffe_eau``). Même conséquence :
renommer ou désactiver le module qui la produit arrêtait la surveillance
sans un mot. On le rebranche à l'identique.
"""

from django.db import migrations

MODULE = "chauffe_eau"
CLE = "besoin_heure_chauffe_prevue"
CIBLE = "heure_demarrage.heure_demarrage"


def brancher(apps, schema_editor):
    Setting = apps.get_model("core", "Setting")
    Setting.objects.get_or_create(module=MODULE, key=CLE, defaults={"value": CIBLE})


def debrancher(apps, schema_editor):
    Setting = apps.get_model("core", "Setting")
    Setting.objects.filter(module=MODULE, key=CLE, value=CIBLE).delete()


class Migration(migrations.Migration):

    dependencies = [("core", "0012_liaisons_solaire")]

    operations = [migrations.RunPython(brancher, debrancher)]
