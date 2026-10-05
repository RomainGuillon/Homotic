# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

"""Tests du suivi des chauffes : la prévision rapprochée du réel.

Ce qui est verrouillé ici :

- une chauffe partie à l'heure prévue garde la prévision qui l'a lancée,
  même si le calcul est refait pendant qu'elle tourne ;
- une chauffe partie à un autre moment n'est comparée à rien — sinon un
  boost du soir ferait passer la prévision du jour pour fausse ;
- une prévision absente ou illisible ne coûte jamais l'enregistrement de la
  chauffe ;
- le bilan juge sur l'écart moyen, et se tait tant qu'il a trop peu de
  chauffes.

Les tables du module n'existent que s'il est activé dans la base qui a servi
à lancer les tests : sinon ces tests sont sautés, plutôt que d'échouer sur
une table absente.
"""

from datetime import datetime, timedelta
from unittest import mock, skipUnless

from django.apps import apps
from django.test import TestCase
from django.utils import timezone

from core.liaisons import liaison, set_liaison
from core.models import LogEntry, Module
from core.tests import connecte

from .fonctions import api, suivi

MODULE_ACTIF = apps.is_installed("modules.chauffe_eau")
SANS_MODULE = "module chauffe_eau non activé : ses tables n'existent pas"

# Forme publiée par le fournisseur — voir docs/09-liaisons-entre-modules.md.
PREVISION = {"heure": "13:30", "duree_min": 60, "besoin_kwh": 2.5,
             "mode": "solaire", "forcee": False, "perime": False}


def a(heure, minute=0):
    """Instant du jour de test, en heure locale du serveur."""
    return timezone.make_aware(datetime(2026, 10, 5, heure, minute))


def _statut(puissance, temperature=40.0):
    """Statut du ballon tel que le rend ``api.get_status_cached``."""
    return {
        "heating": "on" if puissance else "off",
        "raw": {
            "modbuslink:MiddleWaterTemperatureState": temperature,
            "core:TargetDHWTemperatureState": 62.0,
            "modbuslink:PowerHeatElectricalState": puissance,
            "modbuslink:PowerHeatPumpState": 0,
        },
    }


def _besoins(prevision=PREVISION, heure="13:30"):
    """Remplace ``core.liaisons.lire_besoin`` : heure et prévision de chauffe."""
    def lire(_module, nom):
        if nom == "heure_chauffe_prevue":
            return heure, ""
        if nom == "prevision_chauffe":
            return prevision, ""
        return None, "besoin inconnu"
    return mock.patch("core.liaisons.lire_besoin", side_effect=lire)


def _releve(instant, puissance, temperature=40.0):
    """Un passage de la tâche de suivi, à l'instant donné."""
    with mock.patch("django.utils.timezone.now", return_value=instant), \
            mock.patch.object(api, "get_status_cached",
                              return_value=(_statut(puissance, temperature), None, "")):
        suivi.tache_suivi()


def _chauffer(debut, minutes=60, puissance=2400):
    """Rejoue une chauffe : un relevé par minute, puis le relevé d'arrêt."""
    for i in range(minutes):
        _releve(debut + timedelta(minutes=i), puissance, 20.0 + 40.0 * i / minutes)
    _releve(debut + timedelta(minutes=minutes), 0, 60.0)


def _session():
    from .models import ChauffeSession

    return ChauffeSession.objects.get()


class LiaisonBranchee(TestCase):
    """Le nouveau besoin naît branché là où l'heure de chauffe l'est déjà."""

    def test_la_migration_branche_la_prevision(self):
        self.assertEqual(
            liaison("chauffe_eau", "heure_chauffe_prevue"),
            "heure_demarrage.heure_demarrage",
        )
        self.assertEqual(
            liaison("chauffe_eau", "prevision_chauffe"),
            "heure_demarrage.creneau_retenu",
        )


@skipUnless(MODULE_ACTIF, SANS_MODULE)
class PrevisionFigeeAuDemarrage(TestCase):
    """Chaque chauffe partie à l'heure prévue emporte sa prévision."""

    def test_chauffe_a_l_heure_prevue(self):
        with _besoins():
            _chauffer(a(13, 31))

        s = _session()
        self.assertEqual(
            (s.prevu_heure, s.prevu_duree_min, s.prevu_wh), ("13:30", 60, 2500.0)
        )
        # 60 relevés d'une minute à 2 400 W
        self.assertAlmostEqual(s.energie_wh, 2400.0, places=1)
        self.assertEqual(s.duree_min, 60)
        self.assertEqual((s.ecart_wh, s.ecart_pct, s.ecart_duree_min), (-100.0, -4, 0))
        self.assertTrue(
            LogEntry.objects.filter(
                module="chauffe_eau",
                message__contains="prévu 2500 Wh : écart -100 Wh (-4 %)",
            ).exists()
        )

    def test_chauffe_plus_gourmande_que_prevu(self):
        with _besoins():
            _chauffer(a(13, 30), minutes=75)

        s = _session()
        self.assertAlmostEqual(s.energie_wh, 3000.0, places=1)
        self.assertEqual((s.ecart_wh, s.ecart_pct, s.ecart_duree_min), (500.0, 20, 15))

    def test_chauffe_hors_de_l_heure_prevue(self):
        """Un boost du soir n'a jamais été prévu : rien à comparer."""
        with _besoins():
            _chauffer(a(19, 0), minutes=30)

        s = _session()
        self.assertEqual((s.prevu_heure, s.prevu_duree_min, s.prevu_wh), ("", None, None))
        self.assertGreater(s.energie_wh, 0)
        self.assertIsNone(s.ecart_wh)
        self.assertIsNone(s.ecart_pct)
        self.assertIsNone(s.ecart_duree_min)

    def test_un_recalcul_pendant_la_chauffe_ne_change_rien(self):
        with _besoins():
            _releve(a(13, 30), 2400)
        with _besoins(dict(PREVISION, heure="15:00", besoin_kwh=3.0), heure="15:00"):
            _releve(a(13, 31), 2400)
            _releve(a(13, 32), 0)

        self.assertEqual(_session().prevu_wh, 2500.0)
        self.assertEqual(_session().prevu_heure, "13:30")

    def test_prevision_absente_ou_illisible(self):
        """Dans tous les cas la chauffe est enregistrée, sans prévision."""
        from .models import ChauffeSession

        illisibles = (
            None,                                   # calcul jamais lancé
            "13:30",                                # mauvais type branché
            {"heure": "midi", "besoin_kwh": 2.5},   # heure illisible
            {"heure": "25:70", "besoin_kwh": 2.5},  # heure impossible
        )
        for prevision in illisibles:
            with self.subTest(prevision=prevision):
                ChauffeSession.objects.all().delete()
                with _besoins(prevision):
                    _chauffer(a(13, 30), minutes=5)
                s = _session()
                self.assertIsNotNone(s.fin)
                self.assertIsNone(s.prevu_wh)
                self.assertIsNone(s.ecart_wh)

    def test_prevision_partielle(self):
        """Une énergie illisible n'empêche pas de comparer la durée."""
        with _besoins({"heure": "13:30", "duree_min": "60", "besoin_kwh": "?"}):
            _chauffer(a(13, 30), minutes=50)

        s = _session()
        self.assertIsNone(s.prevu_wh)
        self.assertIsNone(s.ecart_wh)
        self.assertEqual((s.prevu_duree_min, s.ecart_duree_min), (60, -10))

    def test_liaison_en_panne(self):
        with mock.patch("core.liaisons.lire_besoin", side_effect=RuntimeError("boum")):
            self.assertEqual(suivi.prevision_de_la_chauffe(a(13, 30)), {})

    def test_puissance_non_relevee(self):
        """Énergie nulle = puissance non relevée, pas une chauffe gratuite :
        l'écart serait de −100 % et ne dirait rien de la prévision."""
        from .models import ChauffeSession

        s = ChauffeSession.objects.create(
            debut=a(13, 30), fin=a(14, 30), duree_min=60,
            prevu_heure="13:30", prevu_duree_min=60, prevu_wh=2500.0,
        )
        self.assertFalse(s.comparable)
        self.assertIsNone(s.ecart_wh)
        self.assertEqual(suivi.bilan_prevision()["nombre"], 0)


@skipUnless(MODULE_ACTIF, SANS_MODULE)
class BilanDeLaPrevision(TestCase):
    """La moyenne des dernières chauffes dit dans quel sens corriger."""

    def _chauffes(self, *reels_wh, prevu_wh=2500.0, jour=1):
        from .models import ChauffeSession

        for i, reel in enumerate(reels_wh):
            debut = a(13, 30) + timedelta(days=jour + i)
            ChauffeSession.objects.create(
                debut=debut, fin=debut + timedelta(minutes=55), duree_min=55,
                energie_wh=reel, energie_elec_wh=reel,
                prevu_heure="13:30" if prevu_wh else "",
                prevu_duree_min=60 if prevu_wh else None, prevu_wh=prevu_wh,
            )

    def test_aucune_chauffe_comparee(self):
        bilan = suivi.bilan_prevision()
        self.assertEqual(bilan["nombre"], 0)
        self.assertIsNone(bilan["verdict"])

    def test_pas_de_verdict_avant_trois_chauffes(self):
        self._chauffes(1500.0, 1600.0)
        bilan = suivi.bilan_prevision()
        self.assertEqual(bilan["nombre"], 2)
        self.assertEqual(bilan["ecart_pct"], -38)
        self.assertIsNone(bilan["verdict"])

    def test_prevision_trop_haute(self):
        self._chauffes(2000.0, 2100.0, 1900.0)
        bilan = suivi.bilan_prevision()
        self.assertEqual(bilan["verdict"], "trop_haute")
        self.assertEqual((bilan["prevu_kwh"], bilan["reel_kwh"]), (2.5, 2.0))
        self.assertEqual((bilan["ecart_kwh"], bilan["ecart_pct"]), (-0.5, -20))
        self.assertEqual((bilan["reel_min_kwh"], bilan["reel_max_kwh"]), (1.9, 2.1))
        self.assertEqual(
            (bilan["duree_prevue_min"], bilan["duree_reelle_min"], bilan["ecart_duree_min"]),
            (60, 55, -5),
        )

    def test_prevision_trop_basse(self):
        self._chauffes(3000.0, 3100.0, 2900.0)
        bilan = suivi.bilan_prevision()
        self.assertEqual(bilan["verdict"], "trop_basse")
        self.assertEqual((bilan["ecart_kwh"], bilan["ecart_pct"]), (0.5, 20))

    def test_juste_en_moyenne_malgre_la_dispersion(self):
        """Des écarts dans les deux sens se compensent : la prévision est
        bonne, c'est le besoin qui varie d'un jour à l'autre."""
        self._chauffes(2000.0, 3000.0, 2500.0)
        bilan = suivi.bilan_prevision()
        self.assertEqual(bilan["verdict"], "juste")
        self.assertEqual(bilan["ecart_kwh"], 0.0)
        self.assertEqual(bilan["ecart_absolu_kwh"], 0.33)

    def test_les_chauffes_sans_prevision_ne_comptent_pas(self):
        self._chauffes(2400.0, 2500.0, 2600.0)
        self._chauffes(900.0, 800.0, prevu_wh=None, jour=10)
        bilan = suivi.bilan_prevision()
        self.assertEqual(bilan["nombre"], 3)
        self.assertEqual(bilan["verdict"], "juste")

    def test_seules_les_chauffes_recentes_comptent(self):
        self._chauffes(*[5000.0] * 5, jour=1)                          # anciennes
        self._chauffes(*[2500.0] * suivi.BILAN_CHAUFFES_MAX, jour=10)  # récentes
        bilan = suivi.bilan_prevision()
        self.assertEqual(bilan["nombre"], suivi.BILAN_CHAUFFES_MAX)
        self.assertEqual(bilan["verdict"], "juste")


@skipUnless(MODULE_ACTIF, SANS_MODULE)
class OngletDuSuivi(TestCase):
    """Le tableau de suivi affiche le prévu, le réel et l'écart."""

    URL = "/module/chauffe_eau/"

    def setUp(self):
        connecte(self.client)
        Module.objects.create(name="chauffe_eau", label="Chauffe-eau", enabled=True)
        Module.objects.create(
            name="heure_demarrage", label="Heure démarrage", enabled=True
        )

    def _page(self):
        # Pas d'identifiants Cozytouch en test : aucun relevé du ballon.
        with mock.patch.object(api, "get_status_cached", return_value=(None, None, "")):
            return self.client.get(self.URL)

    def test_sans_chauffe_comparee(self):
        page = self._page()
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Prévu contre réel")
        self.assertContains(page, "Aucune chauffe comparée pour l'instant")

    def test_besoin_non_branche(self):
        set_liaison("chauffe_eau", "prevision_chauffe", "")
        self.assertContains(self._page(), "n'est pas branché")

    def test_tableau_et_bilan(self):
        with _besoins():
            _chauffer(a(13, 31))             # prévue : 2 400 Wh pour 2 500
            _chauffer(a(19, 0), minutes=30)  # boost du soir : hors prévision
        page = self._page()

        self.assertContains(page, "prévue 13:30")
        self.assertContains(page, "2500 Wh")
        self.assertContains(page, "-100 Wh")
        self.assertContains(page, "-4 %")
        # Une seule chauffe comparée : le bilan chiffre, mais ne juge pas
        self.assertContains(page, "il en faut 3 pour")
        self.assertNotContains(page, "La prévision est")
        self.assertNotIn("suivi_erreur", page.context)

    def test_verdict_et_ou_corriger(self):
        with _besoins():
            for jour in range(3):
                _chauffer(a(13, 30) + timedelta(days=jour), minutes=45)
        page = self._page()

        self.assertEqual(page.context["suivi"]["bilan"]["verdict"], "trop_haute")
        self.assertContains(page, "trop haute")
        self.assertContains(page, "<strong>Heure démarrage</strong>")
