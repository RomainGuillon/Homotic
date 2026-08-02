"""Branche la première liaison, pour ne rien perdre en migrant.

Le lien « enphase → tempo » existait en dur dans le code (import direct).
En le remplaçant par un besoin déclaré, il devient débranché par défaut :
sans cette migration, le coût de la journée disparaîtrait silencieusement
des installations existantes. On rebranche donc à l'identique, une fois,
et sans écraser un choix déjà fait.
"""

from django.db import migrations


def brancher(apps, schema_editor):
    Setting = apps.get_model("core", "Setting")
    Setting.objects.get_or_create(
        module="enphase",
        key="besoin_tarifs_jour",
        defaults={"value": "tempo.tarifs_jour"},
    )


def debrancher(apps, schema_editor):
    Setting = apps.get_model("core", "Setting").objects.filter(
        module="enphase", key="besoin_tarifs_jour", value="tempo.tarifs_jour"
    ).delete()


class Migration(migrations.Migration):

    dependencies = [("core", "0010_control_order")]

    operations = [migrations.RunPython(brancher, debrancher)]
