from django.db import migrations, models


def numeroter(apps, schema_editor):
    """Donne un ordre initial aux contrôles existants, par date de création."""
    Control = apps.get_model("core", "Control")
    for position, control in enumerate(Control.objects.order_by("created_at")):
        control.order = position
        control.save(update_fields=["order"])


class Migration(migrations.Migration):
    """Ordre d'affichage des boutons et switchs du tableau de bord."""

    dependencies = [
        ("core", "0009_dashboardblock_hauteur_libre"),
    ]

    operations = [
        migrations.AddField(
            model_name="control",
            name="order",
            field=models.IntegerField(default=0, verbose_name="ordre"),
        ),
        migrations.AlterField(
            model_name="control",
            name="group",
            field=models.CharField(
                blank=True, default="", max_length=50,
                help_text="Les contrôles d'un même groupe sont affichés ensemble sous "
                          "son nom. Pour des switchs, un seul du groupe peut être ON.",
                verbose_name="groupe",
            ),
        ),
        migrations.AlterModelOptions(
            name="control",
            options={
                "ordering": ["order", "created_at"],
                "verbose_name": "contrôle",
                "verbose_name_plural": "contrôles",
            },
        ),
        migrations.RunPython(numeroter, migrations.RunPython.noop),
    ]
