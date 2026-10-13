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


class _Reponse:
    """Réponse ``requests`` minimale : code HTTP et corps JSON."""

    def __init__(self, code, corps=None):
        self.status_code = code
        self._corps = corps or {}
        self.text = ""

    def json(self):
        return self._corps

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(response=self)


def _r429(message):
    return _Reponse(429, {"response_status": {"error_code": "TooManyRequests", "message": message}})


class Erreurs429(SimpleTestCase):
    """Un 429 « serveurs saturés » ne doit pas couper Solcast jusqu'au lendemain.

    Le 13/10/2026, un 429 reçu à 11h après 4 appels sur 10 a bloqué les
    appels jusqu'au 14/10 6h, ce qui faisait aussi sauter le passage de 3h.
    """

    def setUp(self):
        self.reglages = {}
        patches = [
            mock.patch.object(api, "get_setting",
                              side_effect=lambda k, module=None, default=None: self.reglages.get(k, default)),
            mock.patch.object(api, "set_setting",
                              side_effect=lambda k, v, module=None: self.reglages.__setitem__(k, v)),
            mock.patch.object(api, "journal"),
            mock.patch.object(api, "api_key", return_value="cle"),
            mock.patch.object(api.time_mod, "sleep"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        api._ctx.ok = True
        self.addCleanup(setattr, api._ctx, "ok", False)

    def _appeler(self, *reponses):
        with mock.patch.object(api.requests, "get", side_effect=list(reponses)) as get:
            try:
                resultat = api._get("site", "forecasts")
            except Exception as exc:
                resultat = exc
        return resultat, get.call_count

    def test_quota_epuise_bloque_jusqu_a_minuit_utc(self):
        resultat, appels = self._appeler(_r429("You have exceeded your free daily limit."))
        self.assertIsInstance(resultat, Exception)
        self.assertEqual(appels, 1)  # pas de nouvelle tentative
        self.assertTrue(api.compteur_sature())
        bo = api.backoff()
        self.assertEqual(bo["jusqua"], api._remise_a_zero_quota())
        self.assertLess(bo["jusqua"] - datetime.now(), timedelta(hours=24))

    def test_sature_puis_succes(self):
        resultat, appels = self._appeler(_r429("Too busy"), _Reponse(200, {"forecasts": []}))
        self.assertEqual(resultat, {"forecasts": []})
        self.assertEqual(appels, 2)
        self.assertEqual(api.appels_du_jour(), 1)  # le refus n'est pas décompté
        self.assertIsNone(api.backoff())

    def test_sature_jusqu_au_bout_pause_courte(self):
        resultat, appels = self._appeler(_r429(""), _r429(""), _r429(""))
        self.assertIsInstance(resultat, Exception)
        self.assertEqual(appels, 3)
        self.assertEqual(api.appels_du_jour(), 0)
        self.assertFalse(api.compteur_sature())
        self.assertLessEqual(api.backoff()["jusqua"] - datetime.now(),
                             timedelta(seconds=api.BACKOFF_OCCUPE_S))
