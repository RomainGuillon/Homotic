# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

"""Tests du module Climatisation : conversion des states Overkiz en valeurs
de commande.

Le state et la commande globalControl n'utilisent pas le même vocabulaire
(autoCooling contre auto, swing contre une direction...). Une valeur de state
renvoyée telle quelle à la commande est refusée par Hi-Kumo
(INCOMPATIBLE_VALUE) : c'est ce qui empêchait d'allumer les unités.
"""

from types import SimpleNamespace

from django.test import SimpleTestCase

from .fonctions import api


def _appareil(**valeurs):
    """Faux device Overkiz portant les states donnés (clés de STATE_MAP)."""
    states = {
        api.STATE_MAP[cle]: SimpleNamespace(value=valeur)
        for cle, valeur in valeurs.items()
    }
    return SimpleNamespace(label="Chambre", device_url="io://1/2", states=states)


class ConversionMode(SimpleTestCase):

    def test_auto_refroidissement_devient_auto(self):
        self.assertEqual(api.mode_state_to_command("autoCooling"), "auto")

    def test_auto_chauffage_devient_auto(self):
        self.assertEqual(api.mode_state_to_command("autoHeating"), "auto")

    def test_mode_de_commande_inchange(self):
        for valeur in api.MODES_FR:
            self.assertEqual(api.mode_state_to_command(valeur), valeur)

    def test_absence_de_state(self):
        self.assertIsNone(api.mode_state_to_command(None))


class ConversionVentilation(SimpleTestCase):

    def test_abreviations(self):
        self.assertEqual(api.fan_state_to_command("hi"), "high")
        self.assertEqual(api.fan_state_to_command("med"), "medium")
        self.assertEqual(api.fan_state_to_command("lo"), "low")

    def test_valeur_de_commande_inchangee(self):
        for valeur in api.FAN_OPTIONS:
            self.assertEqual(api.fan_state_to_command(valeur), valeur)


class ExtractionEtat(SimpleTestCase):

    def test_etat_ramene_aux_valeurs_de_commande(self):
        statut = api._extract_status(_appareil(
            power="off", temperature=25, fan_mode="hi", mode="autoCooling",
            swing="swing", leave_home="off", room_temperature=18,
        ))
        self.assertEqual(statut["mode"], "auto")
        self.assertEqual(statut["fan_mode"], "high")
        self.assertEqual(statut["swing"], "both")
        self.assertFalse(statut["on"])
