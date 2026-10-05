# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

"""Tests du module Solaire : les courbes publiées aux autres modules.

``prevision_pv`` et ``prevision_pv_demain`` découpent la même prévision par
journée. Un point rangé dans le mauvais jour, et le module qui planifie
verrait du soleil la nuit — ou reporterait une machine à un lendemain vide.
"""

from datetime import datetime, timedelta
from unittest import mock

from django.test import SimpleTestCase

from .fonctions import api, info

MIDI = datetime.now().astimezone().replace(hour=12, minute=15, second=0, microsecond=0)


def _prevision():
    """Trois jours de prévision : un point à midi, de 1, 2 puis 3 kW."""
    return {"periods": [
        {"time": MIDI + timedelta(days=jour), "pv_kw": float(jour + 1)}
        for jour in range(3)
    ]}


class PrevisionParJournee(SimpleTestCase):

    def test_aujourd_hui_et_demain_sont_separes(self):
        with mock.patch.object(api, "get_forecast", return_value=_prevision()):
            self.assertEqual(info.prevision_pv(), [(MIDI, 1.0)])
            self.assertEqual(
                info.prevision_pv_demain(), [(MIDI + timedelta(days=1), 2.0)]
            )

    def test_prevision_qui_ne_va_pas_jusqu_a_demain(self):
        court = {"periods": _prevision()["periods"][:1]}
        with mock.patch.object(api, "get_forecast", return_value=court):
            self.assertEqual(info.prevision_pv_demain(), [])

    def test_previsions_indisponibles(self):
        with mock.patch.object(api, "get_forecast", side_effect=RuntimeError("panne")):
            self.assertIsNone(info.prevision_pv())
            self.assertIsNone(info.prevision_pv_demain())

    def test_les_deux_courbes_sont_publiees(self):
        noms = {e["nom"]: e for e in info.INFOS}
        for nom in ("prevision_pv", "prevision_pv_demain"):
            self.assertEqual((noms[nom]["type"], noms[nom]["unite"]), ("serie", "kW"))
