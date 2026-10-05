# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

"""Tests du module Tempo : l'objet « tarifs_jour » publié aux autres modules.

Ce qui est vérifié ici, c'est ce que l'objet dit du **lendemain**. D'autres
modules s'en servent pour décider aujourd'hui (reporter une machine à
demain, ou pas) : une couleur supposée, ou un compteur de jours rouges faux,
et la décision l'est aussi.
"""

from datetime import date, timedelta
from unittest import mock

from django.test import TestCase

from .fonctions import api, info

AUJOURD_HUI = str(date.today())
DEMAIN = str(date.today() + timedelta(days=1))
SAISON = {"remaining": {"BLUE": 265, "WHITE": 40, "RED": 22}}


class TarifsDuJour(TestCase):

    def _tarifs(self, couleurs, saison=SAISON):
        with mock.patch.object(info, "_colors", return_value=couleurs), \
                mock.patch.object(
                    api, "get_season_cached", return_value=(saison, None, "")
                ) as compteurs:
            self.compteurs = compteurs
            return info.tarifs_jour()

    def test_couleur_de_demain_publiee(self):
        tarifs = self._tarifs({AUJOURD_HUI: "BLUE", DEMAIN: "RED"})
        self.assertEqual(tarifs["couleur"], "BLUE")
        self.assertEqual(tarifs["couleur_demain"], "RED")

    def test_couleur_de_demain_pas_encore_publiee(self):
        """Elle n'est jamais supposée : ``None`` tant que RTE ne l'a pas donnée."""
        tarifs = self._tarifs({AUJOURD_HUI: "BLUE"})
        self.assertIsNone(tarifs["couleur_demain"])

    def test_jours_restants_de_la_saison(self):
        tarifs = self._tarifs({AUJOURD_HUI: "BLUE"})
        self.assertEqual(
            tarifs["jours_restants"], {"BLUE": 265, "WHITE": 40, "RED": 22}
        )

    def test_plus_aucun_jour_rouge(self):
        saison = {"remaining": {"BLUE": 120, "WHITE": 5, "RED": 0}}
        tarifs = self._tarifs({AUJOURD_HUI: "WHITE"}, saison=saison)
        self.assertEqual(tarifs["jours_restants"]["RED"], 0)

    def test_compteurs_indisponibles(self):
        """Pas de compteurs : ``None``, et surtout pas un zéro qui passerait
        pour « plus aucun jour rouge »."""
        tarifs = self._tarifs({AUJOURD_HUI: "BLUE"}, saison=None)
        self.assertIsNone(tarifs["jours_restants"])
        self.assertEqual(tarifs["couleur"], "BLUE")

    def test_couleur_du_jour_inconnue(self):
        self.assertIsNone(self._tarifs({}))
        self.compteurs.assert_not_called()
