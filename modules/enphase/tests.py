# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

"""Tests du module Énergie : les cumuls du jour face aux bascules de source.

L'Envoy rend la main en timeout par intermittence ; la lecture se replie
alors sur un autre chemin, dont les compteurs cumulés ne partent pas du même
zéro. Ce qui est vérifié ici, c'est qu'une bascule ne fait plus repartir la
journée de zéro — le défaut qui, le 11 septembre 2026, a fait afficher
« 0,0 kWh produit » un soir où l'installation en avait produit 29.
"""

import json
from datetime import datetime
from unittest import mock

from django.test import TestCase

from core.services import set_setting

from .fonctions import api


def _releve(source, prod, conso, net, imp=None, exp=None):
    """Relevé brut tel que ``_read_meters`` le rend."""
    releve = {
        "source": source,
        "prod_w": 0.0, "conso_w": 0.0, "net_w": 0.0,
        "prod_life": prod, "conso_life": conso, "net_life": net,
    }
    if imp is not None:
        releve["imp_life"], releve["exp_life"] = imp, exp
    return releve


class CumulsDuJour(TestCase):
    """La journée doit survivre à un changement de source de mesure."""

    def _lire(self, *args, **kwargs):
        releve = _releve(*args, **kwargs)
        with mock.patch.object(api, "_read_meters", return_value=releve):
            return api.get_energy()

    def test_premiere_lecture_du_jour_part_de_zero(self):
        e = self._lire("meters2", 1000.0, 4000.0, 3000.0)
        self.assertEqual(e["production_wh_today"], 0.0)
        self.assertEqual(e["consumption_wh_today"], 0.0)

    def test_cumul_sur_une_seule_source(self):
        self._lire("meters2", 1000.0, 4000.0, 3000.0)
        e = self._lire("meters2", 6000.0, 9000.0, 3000.0)
        self.assertEqual(e["production_wh_today"], 5000.0)
        self.assertEqual(e["consumption_wh_today"], 5000.0)

    def test_bascule_de_source_ne_remet_pas_la_journee_a_zero(self):
        self._lire("meters2", 1000.0, 4000.0, 3000.0)
        self._lire("meters2", 6000.0, 9000.0, 3000.0)

        # production.json compte depuis un tout autre zéro : la référence de
        # cette source doit être calée sur les 5 kWh déjà comptés, pas prise
        # sur son relevé du moment.
        e = self._lire("production_json", 900000.0, 500000.0, 100000.0)
        self.assertEqual(e["production_wh_today"], 5000.0)
        self.assertEqual(e["consumption_wh_today"], 5000.0)

        e = self._lire("production_json", 902000.0, 503000.0, 100000.0)
        self.assertEqual(e["production_wh_today"], 7000.0)

    def test_retour_a_la_source_precedente_reprend_sa_propre_reference(self):
        self._lire("meters2", 1000.0, 4000.0, 3000.0)
        self._lire("production_json", 900000.0, 500000.0, 100000.0)
        e = self._lire("meters2", 7000.0, 10000.0, 3000.0)
        self.assertEqual(e["production_wh_today"], 6000.0)

    def test_import_et_export_suivent_la_pince_reseau(self):
        self._lire("meters2", 1000.0, 4000.0, 3000.0, imp=2000.0, exp=500.0)
        e = self._lire("meters2", 6000.0, 9000.0, 3000.0, imp=2300.0, exp=1700.0)
        self.assertEqual(e["import_wh_today"], 300.0)
        self.assertEqual(e["export_wh_today"], 1200.0)

    def test_etat_a_reference_unique_est_repris(self):
        """Un état écrit par la version précédente ne doit rien casser."""
        set_setting("daily_state", json.dumps({
            "date": datetime.now().strftime("%Y-%m-%d"),
            "base": {"prod": 1000.0, "conso": 4000.0, "net": 3000.0},
            "last": {"prod": 5000.0, "conso": 8000.0, "net": 3000.0},
            "source": "meters2",
        }), module=api.MODULE)

        e = self._lire("meters2", 6000.0, 9000.0, 3000.0)
        self.assertEqual(e["production_wh_today"], 5000.0)
