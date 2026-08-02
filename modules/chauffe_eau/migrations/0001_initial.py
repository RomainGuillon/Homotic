from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    """Historique des chauffes du ballon (sessions + relevés minute)."""

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="ChauffeSession",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name="ID")),
                ("debut", models.DateTimeField(db_index=True, verbose_name="début")),
                ("fin", models.DateTimeField(blank=True, null=True, verbose_name="fin")),
                ("temp_debut", models.FloatField(null=True, verbose_name="température au départ (°C)")),
                ("temp_fin", models.FloatField(null=True, verbose_name="température à l'arrêt (°C)")),
                ("consigne", models.FloatField(null=True, verbose_name="consigne visée (°C)")),
                ("energie_wh", models.FloatField(default=0.0, verbose_name="énergie consommée (Wh)")),
                ("energie_pac_wh", models.FloatField(default=0.0, verbose_name="dont pompe à chaleur (Wh)")),
                ("energie_elec_wh", models.FloatField(default=0.0, verbose_name="dont résistance (Wh)")),
                ("duree_min", models.IntegerField(default=0, verbose_name="durée (min)")),
            ],
            options={
                "verbose_name": "chauffe du ballon",
                "verbose_name_plural": "chauffes du ballon",
                "ordering": ["-debut"],
            },
        ),
        migrations.CreateModel(
            name="ChauffeMesure",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name="ID")),
                ("quand", models.DateTimeField(db_index=True, verbose_name="horodatage")),
                ("temp_milieu", models.FloatField(null=True, verbose_name="température milieu (°C)")),
                ("temp_bas", models.FloatField(null=True, verbose_name="température bas de cuve (°C)")),
                ("consigne", models.FloatField(null=True, verbose_name="consigne (°C)")),
                ("puissance_elec", models.FloatField(null=True, verbose_name="puissance résistance (W)")),
                ("puissance_pac", models.FloatField(null=True, verbose_name="puissance PAC (W)")),
                ("douches_restantes", models.FloatField(null=True, verbose_name="douches restantes")),
                ("litres_chauds", models.FloatField(null=True, verbose_name="eau chaude restante (L)")),
                ("session", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="mesures", to="chauffe_eau.chauffesession",
                    verbose_name="chauffe")),
            ],
            options={
                "verbose_name": "relevé de chauffe",
                "verbose_name_plural": "relevés de chauffe",
                "ordering": ["quand"],
            },
        ),
    ]
