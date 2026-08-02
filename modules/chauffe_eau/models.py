"""Historique des chauffes du ballon, pour bâtir un modèle de consommation.

Objectif : mesurer l'énergie réellement nécessaire pour amener le ballon
d'une température de départ à sa consigne. Avec ces relevés, l'heure de
démarrage pourra être calculée à partir du besoin réel du jour au lieu d'une
valeur fixe saisie à la main.

Une ligne par minute pendant une chauffe, regroupée en « sessions » : une
session commence quand la chauffe démarre et se termine quand elle s'arrête.
"""

from django.db import models


class ChauffeSession(models.Model):
    """Une chauffe complète, du démarrage à l'arrêt."""

    debut = models.DateTimeField("début", db_index=True)
    fin = models.DateTimeField("fin", null=True, blank=True)

    temp_debut = models.FloatField("température au départ (°C)", null=True)
    temp_fin = models.FloatField("température à l'arrêt (°C)", null=True)
    consigne = models.FloatField("consigne visée (°C)", null=True)

    # Énergie intégrée à partir des puissances relevées chaque minute.
    energie_wh = models.FloatField("énergie consommée (Wh)", default=0.0)
    energie_pac_wh = models.FloatField("dont pompe à chaleur (Wh)", default=0.0)
    energie_elec_wh = models.FloatField("dont résistance (Wh)", default=0.0)
    duree_min = models.IntegerField("durée (min)", default=0)

    class Meta:
        verbose_name = "chauffe du ballon"
        verbose_name_plural = "chauffes du ballon"
        ordering = ["-debut"]

    def __str__(self):
        return f"{self.debut:%d/%m %H:%M} — {self.duree_min} min, {self.energie_wh:.0f} Wh"

    @property
    def delta_temp(self):
        """Élévation de température obtenue, ou None si inconnue."""
        if self.temp_debut is None or self.temp_fin is None:
            return None
        return round(self.temp_fin - self.temp_debut, 1)

    @property
    def wh_par_degre(self):
        """Énergie par degré gagné — la grandeur qui nous intéresse."""
        delta = self.delta_temp
        if not delta or delta <= 0 or not self.energie_wh:
            return None
        return round(self.energie_wh / delta)


class ChauffeMesure(models.Model):
    """Un relevé minute pendant une chauffe.

    Les noms d'états Overkiz correspondants :
    ``modbuslink:MiddleWaterTemperatureState`` (haut de cuve),
    ``core:BottomTankWaterTemperatureState`` (bas de cuve),
    ``modbuslink:PowerHeatElectricalState`` (résistance),
    ``modbuslink:PowerHeatPumpState`` (pompe à chaleur).
    """

    session = models.ForeignKey(
        ChauffeSession, on_delete=models.CASCADE, related_name="mesures",
        verbose_name="chauffe",
    )
    quand = models.DateTimeField("horodatage", db_index=True)

    temp_milieu = models.FloatField("température milieu (°C)", null=True)
    temp_bas = models.FloatField("température bas de cuve (°C)", null=True)
    consigne = models.FloatField("consigne (°C)", null=True)

    puissance_elec = models.FloatField("puissance résistance (W)", null=True)
    puissance_pac = models.FloatField("puissance PAC (W)", null=True)

    douches_restantes = models.FloatField("douches restantes", null=True)
    litres_chauds = models.FloatField("eau chaude restante (L)", null=True)

    class Meta:
        verbose_name = "relevé de chauffe"
        verbose_name_plural = "relevés de chauffe"
        ordering = ["quand"]

    def __str__(self):
        return f"{self.quand:%d/%m %H:%M} — {self.temp_milieu} °C"

    @property
    def puissance_totale(self):
        return (self.puissance_elec or 0.0) + (self.puissance_pac or 0.0)
