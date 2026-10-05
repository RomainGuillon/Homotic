# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

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

    # La passerelle ne repousse l'état du ballon que toutes les dix minutes :
    # l'arrêt est donc vu en retard, et ``duree_min`` (du début au relevé de
    # clôture) arrondit la chauffe à la dizaine supérieure. Durée réelle
    # estimée à la clôture d'après la montée du bas de cuve — vide quand
    # elle n'a pas pu l'être (voir fonctions/releves.py). Les énergies
    # ci-dessus sont intégrées jusqu'à cette fin estimée.
    duree_estimee_min = models.FloatField("durée estimée (min)", null=True, blank=True)
    # De l'eau a été tirée pendant la chauffe (le bas de cuve est
    # redescendu) : elle a duré plus longtemps que sa température de départ
    # ne le laissait prévoir, le modèle de durée l'écarte.
    tirage = models.BooleanField("eau tirée pendant la chauffe", default=False)
    # Partie autour de l'heure prévue par le calcul ? Seules celles-là
    # règlent le modèle de durée : une chauffe lancée à la main (veille d'un
    # retour d'absence) ou une relance du ballon n'est pas ce que le calcul
    # aura à prévoir. Vide pour les chauffes antérieures à ce champ, que le
    # modèle garde faute de pouvoir les distinguer.
    planifiee = models.BooleanField("partie à l'heure prévue", null=True, blank=True)

    # Ce que la prévision annonçait pour cette chauffe, figé à son
    # démarrage : la prévision peut être refaite dans la journée, et c'est
    # celle qui a lancé la chauffe qu'on veut juger. Vides pour une chauffe
    # partie en dehors de l'heure prévue (lancée à la main, relance du
    # ballon) : il n'y avait alors rien à quoi la comparer.
    prevu_heure = models.CharField("heure prévue (HH:MM)", max_length=5,
                                   blank=True, default="")
    prevu_duree_min = models.IntegerField("durée prévue (min)", null=True, blank=True)
    prevu_wh = models.FloatField("énergie prévue (Wh)", null=True, blank=True)

    class Meta:
        verbose_name = "chauffe du ballon"
        verbose_name_plural = "chauffes du ballon"
        ordering = ["-debut"]

    def __str__(self):
        return f"{self.debut:%d/%m %H:%M} — {self.duree_min} min, {self.energie_wh:.0f} Wh"

    @property
    def duree_reelle_min(self):
        """Durée de la chauffe : l'estimée si elle existe, sinon la relevée."""
        if self.duree_estimee_min is not None:
            return self.duree_estimee_min
        return self.duree_min

    @property
    def apprend(self):
        """Vrai si cette chauffe peut régler le modèle de durée.

        Terminée, mesurée, d'au moins 2 °C, partie à l'heure prévue (ou
        avant qu'on sache le dire) et sans eau tirée en cours de route.
        """
        delta = self.delta_temp
        return bool(
            self.fin and self.energie_wh and delta and delta >= 2.0
            and not self.tirage and self.planifiee is not False
            and self.duree_reelle_min and self.duree_reelle_min > 0
        )

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

    # --- Prévu contre réel -------------------------------------------
    # Tous les écarts se lisent « réel − prévu » : positif, la chauffe a
    # demandé plus que prévu (prévision trop basse) ; négatif, moins.

    @property
    def comparable(self):
        """Vrai si la chauffe est terminée, mesurée, et avait une prévision.

        Une énergie nulle n'est pas une chauffe gratuite mais une chauffe
        dont la puissance n'a pas été relevée : la comparer afficherait un
        écart de −100 % qui ne dirait rien de la prévision.
        """
        return bool(self.fin and self.prevu_wh and self.energie_wh)

    @property
    def ecart_wh(self):
        """Énergie consommée en plus (+) ou en moins (−) du prévu, en Wh."""
        if not self.comparable:
            return None
        return round(self.energie_wh - self.prevu_wh, 1)

    @property
    def ecart_pct(self):
        """Le même écart, en pourcentage de l'énergie prévue."""
        ecart = self.ecart_wh
        if ecart is None:
            return None
        return round(100 * ecart / self.prevu_wh)

    @property
    def ecart_duree_min(self):
        """Minutes de chauffe en plus (+) ou en moins (−) du prévu."""
        if not self.fin or not self.prevu_duree_min:
            return None
        return round(self.duree_reelle_min - self.prevu_duree_min)


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
