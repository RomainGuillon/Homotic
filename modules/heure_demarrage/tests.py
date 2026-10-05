# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

"""Tests du module Heure de démarrage.

Deux choses sont vérifiées ici :

- le plan des machines : il respecte la priorité du chauffe-eau, compte la
  chauffe à sa puissance de pointe, compare chaque cycle aux heures creuses
  (switch « Optimisé » sur on, ou jour rouge), sait reporter au lendemain un
  cycle qui n'a plus de place, et ne repropose pas une machine déjà lancée ;
- le calcul du chauffe-eau, qui pilote une chauffe réelle : l'arrivée des
  machines dans le module ne doit rien changer à ce qu'il décide ;
- la durée et l'énergie de la chauffe : celles qu'estime le fournisseur
  branché sur ``estimation_chauffe`` quand il répond, et dans tous les
  autres cas les réglages du module, comme avant.
"""

import importlib
import json
from datetime import datetime, timedelta, timezone
from unittest import mock

from django.apps import apps
from django.test import SimpleTestCase, TestCase, modify_settings

from core.models import LogEntry, Module, Setting
from core.services import get_setting, get_variable, set_setting, set_variable
from core.tests import connecte

from .fonctions import api, calcul, info, machines, scenario

# Fuseau fixe pour les tests du planificateur : il ne lit aucune horloge, on
# lui donne « maintenant » — le fuseau n'a qu'à être le même partout.
FUSEAU = timezone(timedelta(hours=2))
MINUIT = datetime(2026, 10, 4, tzinfo=FUSEAU)

PROFILS = {
    "normal": {"type": "normal", "libelle": "Cycle normal", "duree_min": 70,
               "kwh": 0.30, "chauffe_min": 25, "chauffe_kwh": 0.22},
    "court": {"type": "court", "libelle": "Cycle court", "duree_min": 20,
              "kwh": 0.05, "chauffe_min": 5, "chauffe_kwh": 0.03},
}
BLEU = {"hp": 0.1609, "hc": 0.1296, "hc_debut": 22, "hc_fin": 6}
ROUGE = {"hp": 0.7562, "hc": 0.1568, "hc_debut": 22, "hc_fin": 6}


def a(heure, minute=0):
    """Instant du jour de test : ``a(10, 30)`` = 10 h 30."""
    return MINUIT + timedelta(hours=heure, minutes=minute)


def courbe(production, minuit=MINUIT):
    """Prévision au pas de 30 min, datée au milieu du pas, comme Solcast.

    ``production`` : fonction de l'heure (flottante) vers des kW.
    """
    return [
        (minuit + timedelta(minutes=30 * i + 15), production(i / 2 + 0.25))
        for i in range(48)
    ]


def plateau(kw, debut=8, fin=18):
    """Production constante entre deux heures, nulle ailleurs."""
    return courbe(lambda h: kw if debut <= h < fin else 0.0)


DEMAIN = MINUIT + timedelta(days=1)


def lendemain(points=None, tarifs=BLEU, ballon=None):
    """Ce que le planificateur sait de demain (voir ``machines._lendemain``).

    Par défaut : un lendemain bleu et ensoleillé, sans chauffe-eau.
    """
    return {
        "minuit": DEMAIN,
        "tarifs": tarifs,
        "rouge": tarifs is ROUGE,
        "points": courbe(lambda h: 5.0 if 8 <= h < 18 else 0.0, minuit=DEMAIN)
        if points is None else points,
        "ballon": ballon,
        "erreur": "",
    }


def plan(**surcharges):
    """Appelle le planificateur avec des valeurs par défaut raisonnables."""
    arguments = {
        "maintenant": a(7),
        "points": plateau(5.0),
        "profils": PROFILS,
        "demandes": {"normal": 1, "court": 0},
        "pointe_kw": 1.9,
        "talon_kw": 0.3,
        "tarifs": BLEU,
    }
    arguments.update(surcharges)
    return machines.planifier(**arguments)


class PlanDesMachines(SimpleTestCase):
    """Le planificateur seul : une fonction pure, sans base ni horloge."""

    def test_plein_soleil_le_cycle_ne_coute_rien(self):
        cycle, = plan()["cycles"]
        self.assertEqual(cycle["debut"], a(8))
        self.assertEqual(cycle["fin"], a(9, 10))
        self.assertEqual(cycle["import_kwh"], 0.0)
        self.assertAlmostEqual(cycle["solaire_kwh"], 0.30)
        self.assertEqual(cycle["conseil"], "jour")
        self.assertEqual(cycle["heure"], "08:00")

    def test_aucun_depart_dans_le_passe(self):
        cycle, = plan(maintenant=a(10, 3))["cycles"]
        self.assertEqual(cycle["debut"], a(10, 10))

    def test_la_plage_de_lancement_est_respectee(self):
        cycle, = plan(plage=("11:00", "15:00"))["cycles"]
        self.assertEqual(cycle["debut"], a(11))

    # --- Priorité du chauffe-eau ---

    def test_le_chauffe_eau_passe_avant_la_machine(self):
        """3 kW de production : le ballon en prend 2,4, il ne reste pas de
        quoi chauffer l'eau de la machine. Elle attend la fin du ballon."""
        ballon = {"debut": a(9), "fin": a(10), "kw": 2.4}
        cycle, = plan(points=plateau(3.0, debut=9), ballon=ballon)["cycles"]
        self.assertEqual(cycle["debut"], a(10))
        self.assertFalse(cycle["avec_ballon"])
        self.assertEqual(cycle["import_kwh"], 0.0)

    def test_machine_et_ballon_ensemble_si_le_solaire_couvre_les_deux(self):
        ballon = {"debut": a(9), "fin": a(10), "kw": 2.4}
        cycle, = plan(points=plateau(5.0, debut=9), ballon=ballon)["cycles"]
        self.assertEqual(cycle["debut"], a(9))
        self.assertTrue(cycle["avec_ballon"])
        self.assertEqual(cycle["import_kwh"], 0.0)

    def test_sans_soleil_la_machine_ne_tourne_jamais_avec_le_ballon(self):
        """Aucune production : tous les créneaux se valent, mais ceux qui
        chevauchent la chauffe du ballon restent interdits."""
        ballon = {"debut": a(12), "fin": a(13), "kw": 2.4}
        cycle, = plan(
            points=plateau(0.0), ballon=ballon,
            maintenant=a(11, 30), plage=("11:30", "14:00"), tarifs=None,
        )["cycles"]
        self.assertEqual(cycle["debut"], a(13))
        self.assertFalse(cycle["avec_ballon"])
        self.assertAlmostEqual(cycle["import_kwh"], 0.30)

    # --- La chauffe compte à sa puissance de pointe ---

    def test_la_chauffe_est_comptee_a_la_pointe_pas_a_la_moyenne(self):
        """0,95 kW de surplus couvrent largement la *moyenne* du cycle
        (0,26 kW), mais seulement la moitié d'une salve à 1,9 kW : la moitié
        de la chauffe est donc achetée, soit 0,11 kWh."""
        cycle, = plan(points=plateau(1.25))["cycles"]  # 1,25 − 0,30 de talon
        self.assertAlmostEqual(cycle["import_kwh"], 0.11, places=3)
        self.assertAlmostEqual(cycle["solaire_kwh"], 0.19, places=3)

    def test_le_reste_du_cycle_est_couvert_par_un_petit_surplus(self):
        """Sans phase de chauffe, 0,2 kW de surplus suffisent au cycle."""
        froid = {"normal": {**PROFILS["normal"], "chauffe_min": 0, "chauffe_kwh": 0.0}}
        cycle, = plan(points=plateau(0.6), profils=froid)["cycles"]
        self.assertEqual(cycle["import_kwh"], 0.0)

    # --- Comparaison avec les heures creuses ---

    def test_jour_rouge_sans_soleil_les_heures_creuses_gagnent(self):
        cycle, = plan(points=plateau(0.0), tarifs=ROUGE)["cycles"]
        self.assertEqual(cycle["conseil"], "hc")
        self.assertEqual(cycle["heure"], "22:00")
        self.assertAlmostEqual(cycle["cout_jour"], 0.30 * 0.7562)
        self.assertAlmostEqual(cycle["cout_hc"], 0.30 * 0.1568)
        self.assertAlmostEqual(cycle["ecart"], 0.30 * (0.7562 - 0.1568))

    def test_jour_rouge_ensoleille_la_journee_gagne(self):
        cycle, = plan(tarifs=ROUGE)["cycles"]
        self.assertEqual(cycle["conseil"], "jour")
        self.assertEqual(cycle["cout_jour"], 0.0)

    def test_jour_bleu_sans_soleil_les_heures_creuses_gagnent_de_peu(self):
        cycle, = plan(points=plateau(0.0))["cycles"]
        self.assertEqual(cycle["conseil"], "hc")
        self.assertAlmostEqual(cycle["ecart"], 0.30 * (0.1609 - 0.1296))

    def test_sans_tarifs_pas_de_comparaison(self):
        cycle, = plan(points=plateau(0.0), tarifs=None)["cycles"]
        self.assertEqual(cycle["conseil"], "jour")
        self.assertIsNone(cycle["cout_jour"])
        self.assertIsNone(cycle["cout_hc"])

    # --- Plusieurs cycles ---

    def test_deux_cycles_se_suivent_avec_la_pause(self):
        premier, second = plan(demandes={"normal": 2, "court": 0})["cycles"]
        self.assertEqual(premier["debut"], a(8))
        # 70 min de cycle + 30 min de pause
        self.assertEqual(second["debut"], a(9, 40))

    def test_cycles_normaux_et_courts(self):
        cycles = plan(demandes={"normal": 1, "court": 2})["cycles"]
        self.assertEqual(sorted(c["type"] for c in cycles), ["court", "court", "normal"])
        for avant, apres in zip(cycles, cycles[1:]):
            self.assertGreaterEqual(apres["debut"], avant["fin"] + timedelta(minutes=30))

    def test_le_meilleur_creneau_va_au_cycle_qui_consomme_le_plus(self):
        """Une seule heure et demie de soleil : c'est le cycle normal qui la
        prend, le cycle court se contente du reste."""
        cycles = plan(
            points=plateau(5.0, debut=12, fin=13.5),
            demandes={"normal": 1, "court": 1}, tarifs=ROUGE,
        )["cycles"]
        normal = next(c for c in cycles if c["type"] == "normal")
        self.assertEqual(normal["import_kwh"], 0.0)

    def test_plus_assez_de_place_dans_la_journee(self):
        resultat = plan(
            maintenant=a(19, 30), points=plateau(0.0),
            demandes={"normal": 3, "court": 0},
        )
        self.assertEqual(resultat["non_places"], {"normal": 2})
        places = [c for c in resultat["cycles"] if c["debut"]]
        reportes = [c for c in resultat["cycles"] if not c["debut"]]
        self.assertEqual(len(places), 1)
        self.assertEqual(len(reportes), 2)
        self.assertEqual({c["conseil"] for c in reportes}, {"hc"})
        self.assertEqual({c["heure"] for c in reportes}, {"22:00"})

    def test_non_place_sans_tarifs(self):
        resultat = plan(maintenant=a(20, 30), tarifs=None)
        cycle, = resultat["cycles"]
        self.assertIsNone(cycle["debut"])
        self.assertEqual(cycle["conseil"], "aucun")

    # --- Switch « Optimisé » sur off : pas de comparaison ---

    def test_switch_off_la_machine_reste_dans_la_plage(self):
        """Sans soleil, la nuit serait moins chère : off n'en tient pas compte."""
        cycle, = plan(points=plateau(0.0), comparer_hc=False)["cycles"]
        self.assertEqual(cycle["conseil"], "jour")
        self.assertEqual(cycle["heure"], "08:00")
        self.assertAlmostEqual(cycle["cout_jour"], 0.30 * 0.1609)
        # Le coût en heures creuses reste chiffré, pour information
        self.assertAlmostEqual(cycle["cout_hc"], 0.30 * 0.1296)

    def test_switch_off_sans_lendemain_un_cycle_sans_place_va_en_heures_creuses(self):
        cycle, = plan(maintenant=a(21), comparer_hc=False)["cycles"]
        self.assertEqual((cycle["conseil"], cycle["heure"]), ("hc", "22:00"))
        self.assertIsNone(cycle["motif"])

    # --- Switch sur off, plus de place aujourd'hui : ce soir ou demain ? ---

    def _sans_place(self, **surcharges):
        """Un cycle demandé à 21 h : la plage de lancement est finie."""
        arguments = {"maintenant": a(21), "comparer_hc": False}
        arguments.update(surcharges)
        return plan(**arguments)

    def test_demain_rouge_heures_creuses_ce_soir(self):
        resultat = self._sans_place(lendemain=lendemain(tarifs=ROUGE))
        cycle, = resultat["cycles"]
        self.assertEqual((cycle["conseil"], cycle["heure"]), ("hc", "22:00"))
        self.assertEqual(cycle["motif"], "demain_rouge")
        self.assertIsNone(cycle["debut"])
        # Les heures creuses de ce soir sont au tarif d'aujourd'hui (bleu)
        self.assertAlmostEqual(cycle["cout_hc"], 0.30 * 0.1296)
        self.assertEqual(resultat["non_places"], {"normal": 1})

    def test_demain_ensoleille_le_cycle_y_est_reporte(self):
        cycle, = self._sans_place(lendemain=lendemain())["cycles"]
        self.assertEqual((cycle["conseil"], cycle["heure"]), ("demain", "08:00"))
        self.assertEqual(cycle["motif"], "demain_moins_cher")
        self.assertEqual(cycle["debut"], DEMAIN + timedelta(hours=8))
        self.assertEqual(cycle["fin"], DEMAIN + timedelta(hours=9, minutes=10))
        self.assertEqual(cycle["cout_demain"], 0.0)
        self.assertEqual(cycle["import_kwh"], 0.0)
        self.assertIsNone(cycle["cout_jour"])
        self.assertAlmostEqual(cycle["cout_hc"], 0.30 * 0.1296)
        self.assertAlmostEqual(cycle["ecart"], 0.30 * 0.1296)

    def test_demain_sans_soleil_les_heures_creuses_restent_moins_cheres(self):
        nuages = courbe(lambda h: 0.0, minuit=DEMAIN)
        cycle, = self._sans_place(lendemain=lendemain(points=nuages))["cycles"]
        self.assertEqual((cycle["conseil"], cycle["heure"]), ("hc", "22:00"))
        self.assertEqual(cycle["motif"], "demain_plus_cher")
        # Ce que demain aurait coûté est gardé, pour l'explication
        self.assertAlmostEqual(cycle["cout_demain"], 0.30 * 0.1609)
        self.assertEqual(cycle["demain_debut"], DEMAIN + timedelta(hours=8))

    def test_demain_est_chiffre_au_tarif_de_demain(self):
        """Un peu de soleil demain : 0,08 kWh à acheter. En bleu c'est moins
        cher que la nuit ; au tarif d'un jour rouge ce serait l'inverse — or
        c'est bien la grille de demain qui compte, pas celle d'aujourd'hui."""
        voile = courbe(lambda h: 1.25 if 8 <= h < 18 else 0.0, minuit=DEMAIN)
        aujourd_hui_rouge = self._sans_place(
            tarifs=ROUGE, lendemain=lendemain(points=voile)
        )
        cycle, = aujourd_hui_rouge["cycles"]
        self.assertEqual(cycle["conseil"], "demain")
        self.assertAlmostEqual(cycle["cout_demain"], cycle["import_kwh"] * 0.1609)
        self.assertLess(cycle["cout_demain"], 0.30 * 0.1568)

    def test_couleur_de_demain_inconnue_heures_creuses_ce_soir(self):
        cycle, = self._sans_place(lendemain=lendemain(tarifs=None))["cycles"]
        self.assertEqual((cycle["conseil"], cycle["heure"]), ("hc", "22:00"))
        self.assertEqual(cycle["motif"], "demain_inconnu")

    def test_pas_de_prevision_pour_demain_heures_creuses_ce_soir(self):
        cycle, = self._sans_place(lendemain=lendemain(points=[]))["cycles"]
        self.assertEqual(cycle["conseil"], "hc")
        self.assertEqual(cycle["motif"], "demain_sans_prevision")

    def test_le_chauffe_eau_de_demain_passe_avant_la_machine(self):
        """3 kW demain : le ballon (2,4 kW) ne laisse pas de quoi chauffer la
        machine, elle attend qu'il ait fini."""
        soleil = courbe(lambda h: 3.0 if 8 <= h < 18 else 0.0, minuit=DEMAIN)
        ballon = {"debut": DEMAIN + timedelta(hours=8),
                  "fin": DEMAIN + timedelta(hours=9), "kw": 2.4}
        cycle, = self._sans_place(
            lendemain=lendemain(points=soleil, ballon=ballon)
        )["cycles"]
        self.assertEqual((cycle["conseil"], cycle["heure"]), ("demain", "09:00"))
        self.assertFalse(cycle["avec_ballon"])

    def test_deux_cycles_reportes_se_suivent_demain(self):
        cycles = self._sans_place(
            demandes={"normal": 2, "court": 0}, lendemain=lendemain()
        )["cycles"]
        self.assertEqual([c["conseil"] for c in cycles], ["demain", "demain"])
        self.assertEqual([c["heure"] for c in cycles], ["08:00", "09:40"])

    def test_seuls_les_cycles_sans_place_sont_reportes(self):
        """19 h 30 : un cycle tient encore aujourd'hui, les deux autres non."""
        resultat = plan(
            maintenant=a(19, 30), points=plateau(0.0), comparer_hc=False,
            demandes={"normal": 3, "court": 0}, lendemain=lendemain(),
        )
        self.assertEqual(
            [(c["conseil"], c["heure"]) for c in resultat["cycles"]],
            [("jour", "19:30"), ("demain", "08:00"), ("demain", "09:40")],
        )

    def test_sur_on_le_lendemain_n_est_pas_regarde(self):
        """Le report au lendemain est la règle du switch sur off : sur on, le
        planificateur ne reçoit pas de lendemain et le cycle va en heures
        creuses."""
        cycle, = plan(maintenant=a(21))["cycles"]
        self.assertEqual((cycle["conseil"], cycle["heure"]), ("hc", "22:00"))

    # --- Départage à coût égal ---

    def test_ajustement_faible_au_plus_tot_max_au_pic(self):
        """Du soleil à revendre de 9 h à 17 h, avec un pic à 13 h : partout
        le cycle est gratuit, c'est le réglage « ajustement » qui choisit."""
        points = courbe(lambda h: 6.0 - abs(h - 13) if 9 <= h < 17 else 0.0)
        tot, = plan(points=points, ajustement="faible")["cycles"]
        pic, = plan(points=points, ajustement="max")["cycles"]
        self.assertEqual(tot["debut"], a(9))
        self.assertEqual(tot["import_kwh"], 0.0)
        self.assertEqual(pic["import_kwh"], 0.0)
        self.assertTrue(a(12) <= pic["debut"] <= a(13), pic["debut"])

    # --- Cycles déjà lancés ---

    def test_un_cycle_deja_lance_est_conserve(self):
        lance, = plan()["cycles"]  # 08:00 – 09:10
        resultat = plan(
            maintenant=a(9), demandes={"normal": 2, "court": 0}, deja=[lance]
        )
        premier, second = resultat["cycles"]
        self.assertTrue(premier["lancee"])
        self.assertEqual(premier["debut"], a(8))
        self.assertFalse(second["lancee"])
        # La machine n'est libre qu'après le premier cycle et la pause
        self.assertEqual(second["debut"], a(9, 40))

    def test_un_cycle_lance_de_trop_est_oublie(self):
        lance, = plan()["cycles"]
        resultat = plan(
            maintenant=a(9), demandes={"normal": 0, "court": 1}, deja=[lance]
        )
        cycle, = resultat["cycles"]
        self.assertEqual(cycle["type"], "court")


def _besoins(points=None, tarifs=None, demain=None):
    """Remplace ``core.liaisons.lire_besoin`` : fournit prévisions et tarifs.

    ``demain`` : la prévision du lendemain (besoin ``prevision_pv_demain``).
    """
    def lire(_module, nom):
        if nom == "prevision_pv":
            return (points, "") if points is not None else (None, "besoin non branché")
        if nom == "prevision_pv_demain":
            return (demain, "") if demain is not None else (None, "besoin non branché")
        if nom == "tarifs_jour":
            return tarifs, ""
        return None, "besoin inconnu"
    return mock.patch("core.liaisons.lire_besoin", side_effect=lire)


def _tarifs_tempo(couleur="BLUE", demain=None, restants=None):
    """Objet « tarifs_jour » tel que le module Tempo le publie.

    ``demain`` : la couleur du lendemain, ``None`` tant qu'elle n'est pas
    publiée. ``restants`` : jours restant à tirer cette saison, par couleur.
    """
    return {
        "couleur": couleur,
        "couleur_demain": demain,
        "jours_restants": restants,
        "prix": {
            "BLUE": {"HP": 0.1609, "HC": 0.1296},
            "WHITE": {"HP": 0.1894, "HC": 0.1486},
            "RED": {"HP": 0.7562, "HC": 0.1568},
        },
        "libelles": {"BLUE": "Bleu", "WHITE": "Blanc", "RED": "Rouge"},
        "hc_debut": 22,
        "hc_fin": 6,
    }


class Horloge:
    """Fige l'heure du fichier ``machines`` (heure locale du serveur)."""

    def __init__(self, heure, minute=0, jour=4):
        self.instant = datetime(2026, 10, jour, heure, minute).astimezone()

    def __enter__(self):
        self._patch = mock.patch.object(
            machines, "_maintenant", return_value=self.instant
        )
        self._patch.start()
        return self.instant

    def __exit__(self, *exc):
        self._patch.stop()


def _soleil(jour=4):
    """5 kW de 8 h à 18 h, le jour de test, dans le fuseau du serveur."""
    minuit = datetime(2026, 10, jour).astimezone()
    return plateau_local(minuit)


def plateau_local(minuit, kw=5.0, debut=8, fin=18):
    return courbe(lambda h: kw if debut <= h < fin else 0.0, minuit=minuit)


def _ballon(heure="12:00", mode="solaire"):
    """Mémorise un calcul du chauffe-eau, comme l'aurait fait ``calculer``."""
    calcul.memoriser({
        "heure": heure, "mode": mode, "duree_min": 60, "besoin_kwh": 2.4,
        "saison": "ete", "creneau": None, "erreur": "", "detail": [],
    })


class CalculDesMachines(TestCase):
    """Le calcul complet : réglages, mémoire, et lecture du chauffe-eau."""

    def setUp(self):
        api.set_machines_demandees(normal=1, court=0)

    def test_le_plan_est_memorise_et_relu_a_l_identique(self):
        _ballon()
        with Horloge(7), _besoins(_soleil(), _tarifs_tempo()):
            calcule = machines.calculer()
            relu = machines.dernier_resultat()
        self.assertEqual(calcule["cycles"][0]["heure"], "08:00")
        self.assertEqual(relu["cycles"][0]["debut"], calcule["cycles"][0]["debut"])
        self.assertFalse(relu["perime"])
        self.assertFalse(relu["jamais_calcule"])
        self.assertEqual(relu["prochaine"]["heure"], "08:00")
        self.assertEqual(relu["restantes"], 1)
        self.assertEqual(relu["tarifs"]["libelle"], "Bleu")

    def test_le_creneau_du_ballon_est_lu_et_jamais_modifie(self):
        _ballon(heure="08:00")
        avant = get_setting(calcul.CLE_DERNIER, module=api.MODULE)
        # 3 kW : le ballon (2,4 kW) ne laisse pas de quoi chauffer la machine
        points = plateau_local(datetime(2026, 10, 4).astimezone(), kw=3.0)
        with Horloge(7), _besoins(points, _tarifs_tempo()):
            resultat = machines.calculer()
        self.assertEqual(resultat["cycles"][0]["heure"], "09:00")
        self.assertEqual(resultat["ballon"]["kw"], 2.4)
        self.assertEqual(get_setting(calcul.CLE_DERNIER, module=api.MODULE), avant)

    def test_sans_calcul_du_ballon_rien_n_est_retire(self):
        with Horloge(7), _besoins(_soleil(), _tarifs_tempo()):
            resultat = machines.calculer()
        self.assertIsNone(resultat["ballon"])
        self.assertEqual(resultat["cycles"][0]["heure"], "08:00")

    def test_sans_prevision_branchee_les_heures_creuses_sont_conseillees(self):
        with Horloge(7), _besoins(None, _tarifs_tempo()):
            resultat = machines.calculer()
        cycle, = resultat["cycles"]
        self.assertEqual(cycle["conseil"], "hc")
        self.assertIn("non branché", resultat["erreur"])

    def test_recalcul_en_cours_de_journee_garde_la_machine_lancee(self):
        api.set_machines_demandees(normal=2)
        with _besoins(_soleil(), _tarifs_tempo()):
            with Horloge(7):
                matin = machines.calculer()
            with Horloge(8, 30):
                midi = machines.calculer()
        self.assertEqual([c["heure"] for c in matin["cycles"]], ["08:00", "09:40"])
        self.assertEqual([c["heure"] for c in midi["cycles"]], ["08:00", "09:40"])
        self.assertTrue(midi["cycles"][0]["passe"])
        self.assertTrue(midi["cycles"][0]["lancee"])
        self.assertFalse(midi["cycles"][1]["passe"])
        self.assertEqual(midi["restantes"], 1)
        self.assertEqual(midi["prochaine"]["heure"], "09:40")

    def test_une_machine_dont_l_heure_est_passee_n_est_plus_affichee(self):
        """Sans aucun recalcul : c'est l'heure qu'il est qui retire la ligne."""
        api.set_machines_demandees(normal=2)
        with Horloge(7), _besoins(_soleil(), _tarifs_tempo()):
            machines.calculer()
            plan = machines.dernier_resultat()
            self.assertEqual([c["heure"] for c in plan["affichees"]], ["08:00", "09:40"])
            self.assertEqual(plan["nb_passees"], 0)
            self.assertAlmostEqual(plan["total"]["kwh"], 0.60)

        with Horloge(8):  # pile à l'heure : la machine reste à lancer
            plan = machines.dernier_resultat()
            self.assertEqual([c["heure"] for c in plan["affichees"]], ["08:00", "09:40"])

        with Horloge(8, 30):
            plan = machines.dernier_resultat()
            self.assertEqual([c["heure"] for c in plan["affichees"]], ["09:40"])
            self.assertEqual(plan["nb_passees"], 1)
            # Le plan complet est gardé : le calcul en a besoin
            self.assertEqual(len(plan["cycles"]), 2)
            # Les totaux ne comptent plus que ce qui reste à lancer
            self.assertAlmostEqual(plan["total"]["kwh"], 0.30)
            self.assertEqual(info.plan_machines(), "09:40 normal")

        with Horloge(11):
            plan = machines.dernier_resultat()
            self.assertEqual(plan["affichees"], [])
            self.assertEqual(plan["nb_passees"], 2)
            self.assertEqual(plan["restantes"], 0)
            self.assertIsNone(plan["prochaine"])
            self.assertIsNone(plan["total"]["cout"])
            self.assertIsNone(info.plan_machines())

    def test_une_machine_conseillee_en_heures_creuses_reste_affichee(self):
        """Son créneau de jour a beau être passé, elle reste à lancer ce soir."""
        with Horloge(7), _besoins(None, _tarifs_tempo("RED")):
            machines.calculer()
        with Horloge(15):
            plan = machines.dernier_resultat()
        cycle, = plan["affichees"]
        self.assertEqual((cycle["conseil"], cycle["heure"]), ("hc", "22:00"))
        self.assertEqual(plan["nb_passees"], 0)

    def test_tout_replanifier_oublie_les_machines_passees(self):
        api.set_machines_demandees(normal=2)
        with _besoins(_soleil(), _tarifs_tempo()):
            with Horloge(7):
                machines.calculer()
            with Horloge(8, 30):
                resultat = machines.calculer(tout_replanifier=True)
        self.assertEqual([c["heure"] for c in resultat["cycles"]], ["08:30", "10:10"])
        self.assertEqual(resultat["restantes"], 2)

    def test_un_plan_de_la_veille_est_perime(self):
        with Horloge(7), _besoins(_soleil(), _tarifs_tempo()):
            machines.calculer()
        with Horloge(7, jour=5):
            lendemain = machines.dernier_resultat()
            self.assertTrue(lendemain["perime"])
            self.assertIsNone(lendemain["prochaine"])
            self.assertIsNone(info.prochaine_machine())
            self.assertEqual(info.machines_restantes(), 0)
            self.assertIsNone(info.plan_machines())

    def test_un_plan_de_la_veille_ne_compte_pas_comme_lance(self):
        with Horloge(7), _besoins(_soleil(), _tarifs_tempo()):
            machines.calculer()
        with Horloge(12, jour=5), _besoins(_soleil(jour=5), _tarifs_tempo()):
            resultat = machines.calculer()
        cycle, = resultat["cycles"]
        self.assertFalse(cycle["lancee"])
        self.assertEqual(cycle["heure"], "12:00")

    # --- Switch « Optimisé » ---

    def test_le_switch_est_sur_on_par_defaut(self):
        self.assertTrue(api.machines_optimise())
        self.assertFalse(api.set_machines_optimise(False))
        self.assertTrue(api.set_machines_optimise(True))

    def test_switch_off_sans_soleil_la_machine_reste_dans_la_plage(self):
        api.set_machines_optimise(False)
        nuages = plateau_local(datetime(2026, 10, 4).astimezone(), kw=0.0)
        with Horloge(7), _besoins(nuages, _tarifs_tempo()):
            resultat = machines.calculer()
        cycle, = resultat["cycles"]
        self.assertEqual((cycle["conseil"], cycle["heure"]), ("jour", "08:00"))
        self.assertFalse(resultat["optimise"])
        self.assertFalse(resultat["comparer_hc"])
        self.assertIn("sur off", " ".join(resultat["detail"]))
        self.assertNotIn("en heures creuses", " ".join(resultat["detail"][-1:]))

    def test_switch_on_sans_soleil_les_heures_creuses_sont_conseillees(self):
        nuages = plateau_local(datetime(2026, 10, 4).astimezone(), kw=0.0)
        with Horloge(7), _besoins(nuages, _tarifs_tempo()):
            resultat = machines.calculer()
        cycle, = resultat["cycles"]
        self.assertEqual((cycle["conseil"], cycle["heure"]), ("hc", "22:00"))
        self.assertTrue(resultat["comparer_hc"])
        self.assertIn("sur on", " ".join(resultat["detail"]))

    def test_jour_rouge_le_switch_n_est_pas_pris_en_compte(self):
        api.set_machines_optimise(False)
        nuages = plateau_local(datetime(2026, 10, 4).astimezone(), kw=0.0)
        with Horloge(7), _besoins(nuages, _tarifs_tempo("RED")):
            sans_soleil = machines.calculer()
        with Horloge(7), _besoins(_soleil(), _tarifs_tempo("RED")):
            au_soleil = machines.calculer()
        # Le moins cher des deux : la nuit sans soleil, la journée au soleil
        self.assertEqual(sans_soleil["cycles"][0]["conseil"], "hc")
        self.assertEqual(
            (au_soleil["cycles"][0]["conseil"], au_soleil["cycles"][0]["heure"]),
            ("jour", "08:00"),
        )
        for resultat in (sans_soleil, au_soleil):
            self.assertTrue(resultat["rouge"])
            self.assertTrue(resultat["comparer_hc"])
            self.assertIn("Jour rouge", " ".join(resultat["detail"]))
        # L'état enregistré du switch, lui, n'a pas bougé
        self.assertFalse(api.machines_optimise())
        self.assertFalse(au_soleil["optimise"])

    def test_jour_rouge_sans_place_le_lendemain_n_est_pas_regarde(self):
        api.set_machines_optimise(False)
        with Horloge(21), _besoins(
            _soleil(), _tarifs_tempo("RED", demain="BLUE"), demain=_soleil(jour=5)
        ):
            resultat = machines.calculer()
        cycle, = resultat["cycles"]
        self.assertEqual((cycle["conseil"], cycle["heure"]), ("hc", "22:00"))
        self.assertIsNone(resultat["lendemain"])

    # --- Switch sur off, plus de place aujourd'hui ---

    def _sans_place(self, tarifs, demain=None):
        """Calcul lancé à 21 h, switch sur off : la plage est finie."""
        api.set_machines_optimise(False)
        with Horloge(21), _besoins(_soleil(), tarifs, demain=demain):
            return machines.calculer()

    def test_report_a_demain_quand_il_y_fait_soleil(self):
        resultat = self._sans_place(
            _tarifs_tempo(demain="BLUE"), demain=_soleil(jour=5)
        )
        with Horloge(21):
            relu = machines.dernier_resultat()
            cycle, = relu["affichees"]
            self.assertEqual((cycle["conseil"], cycle["heure"]), ("demain", "08:00"))
            self.assertEqual(cycle["debut"], datetime(2026, 10, 5, 8).astimezone())
            self.assertEqual(cycle["cout_c"], 0.0)
            self.assertAlmostEqual(cycle["ecart_c"], 100 * 0.30 * 0.1296)
            # Rien à lancer aujourd'hui : l'heure de demain ne doit pas
            # passer pour celle d'aujourd'hui dans un scénario
            self.assertIsNone(relu["prochaine"])
            self.assertEqual(relu["prochaine_demain"]["heure"], "08:00")
            self.assertEqual((relu["restantes"], relu["reportees"]), (0, 1))
            self.assertIsNone(info.prochaine_machine())
            self.assertEqual(info.machines_restantes(), 0)
            self.assertEqual(info.plan_machines(), "08:00 normal (demain)")
        self.assertEqual(resultat["lendemain"]["tarifs"]["libelle"], "Bleu")
        self.assertIn("lancer demain à 08:00", " ".join(resultat["detail"]))

    def test_le_chauffe_eau_de_demain_est_estime_et_respecte(self):
        """3 kW demain : le ballon prend le premier créneau qui le couvre
        (08:00–09:00, réglages par défaut : 60 min, 2,5 kWh) et la machine
        passe après lui."""
        demain = plateau_local(datetime(2026, 10, 5).astimezone(), kw=3.0)
        resultat = self._sans_place(_tarifs_tempo(demain="BLUE"), demain=demain)
        cycle, = resultat["cycles"]
        self.assertEqual((cycle["conseil"], cycle["heure"]), ("demain", "09:00"))
        ballon = resultat["lendemain"]["ballon"]
        self.assertEqual(ballon["debut"], datetime(2026, 10, 5, 8).astimezone())
        self.assertEqual(ballon["fin"], datetime(2026, 10, 5, 9).astimezone())
        self.assertEqual(ballon["kw"], 2.5)
        self.assertIn("chauffe-eau estimé de 08:00 à 09:00", " ".join(resultat["detail"]))
        self.assertIn("estimation grossière", " ".join(resultat["detail"]))
        # Une estimation, jamais l'heure de chauffe : rien n'est mémorisé ni publié
        self.assertTrue(calcul.dernier_resultat()["jamais_calcule"])
        self.assertFalse(get_variable(calcul.VARIABLE_HEURE))

    def test_demain_rouge_heures_creuses_ce_soir(self):
        resultat = self._sans_place(
            _tarifs_tempo(demain="RED"), demain=_soleil(jour=5)
        )
        cycle, = resultat["cycles"]
        self.assertEqual((cycle["conseil"], cycle["heure"]), ("hc", "22:00"))
        self.assertEqual(cycle["raison"], "demain est rouge")
        self.assertTrue(resultat["lendemain"]["rouge"])
        self.assertIn("Demain est un jour rouge", " ".join(resultat["detail"]))
        self.assertEqual(resultat["prochaine"]["heure"], "22:00")

    def test_couleur_de_demain_pas_encore_publiee(self):
        resultat = self._sans_place(_tarifs_tempo(), demain=_soleil(jour=5))
        cycle, = resultat["cycles"]
        self.assertEqual((cycle["conseil"], cycle["heure"]), ("hc", "22:00"))
        self.assertEqual(cycle["raison"], "couleur de demain pas encore connue")
        self.assertIn("un jour rouge ne peut pas être exclu", " ".join(resultat["detail"]))

    def test_couleur_inconnue_et_des_jours_rouges_restent(self):
        resultat = self._sans_place(
            _tarifs_tempo(restants={"BLUE": 250, "WHITE": 20, "RED": 3}),
            demain=_soleil(jour=5),
        )
        cycle, = resultat["cycles"]
        self.assertEqual((cycle["conseil"], cycle["heure"]), ("hc", "22:00"))
        self.assertEqual(resultat["lendemain"]["rouges_restants"], 3)
        self.assertIn("il reste 3 jours rouges", " ".join(resultat["detail"]))

    def test_couleur_inconnue_mais_plus_aucun_jour_rouge_a_tirer(self):
        """Demain ne peut plus être rouge : le report redevient possible."""
        resultat = self._sans_place(
            _tarifs_tempo(restants={"BLUE": 250, "WHITE": 20, "RED": 0}),
            demain=_soleil(jour=5),
        )
        cycle, = resultat["cycles"]
        self.assertEqual((cycle["conseil"], cycle["heure"]), ("demain", "08:00"))
        grille = resultat["lendemain"]["tarifs"]
        self.assertTrue(grille["supposee"])
        self.assertEqual(grille["libelle"], "Blanc")
        self.assertIn("plus aucun jour rouge", " ".join(resultat["detail"]))

    def test_couleur_inconnue_demain_est_chiffre_au_plus_cher_possible(self):
        """Un peu de soleil demain, 0,08 kWh à acheter : au tarif blanc tant
        qu'il reste des jours blancs, au tarif bleu quand il n'en reste plus."""
        voile = plateau_local(datetime(2026, 10, 5).astimezone(), kw=1.25)
        for blancs, prix_hp in ((20, 0.1894), (0, 0.1609)):
            with self.subTest(blancs=blancs):
                resultat = self._sans_place(
                    _tarifs_tempo(restants={"BLUE": 250, "WHITE": blancs, "RED": 0}),
                    demain=voile,
                )
                cycle, = resultat["cycles"]
                self.assertEqual(cycle["conseil"], "demain")
                self.assertGreater(cycle["import_kwh"], 0.0)
                self.assertAlmostEqual(
                    cycle["cout_demain"], cycle["import_kwh"] * prix_hp
                )

    def test_la_couleur_publiee_prime_sur_le_compteur(self):
        """Compteur à zéro mais demain annoncé rouge (cache en retard) : on
        croit la couleur publiée."""
        resultat = self._sans_place(
            _tarifs_tempo(demain="RED", restants={"BLUE": 250, "WHITE": 20, "RED": 0}),
            demain=_soleil(jour=5),
        )
        self.assertEqual(resultat["cycles"][0]["raison"], "demain est rouge")

    def test_prevision_de_demain_non_branchee(self):
        resultat = self._sans_place(_tarifs_tempo(demain="BLUE"))
        cycle, = resultat["cycles"]
        self.assertEqual(cycle["conseil"], "hc")
        self.assertEqual(cycle["raison"], "pas de prévision pour demain")
        self.assertIn("non branché", resultat["lendemain"]["erreur"])

    def test_un_report_a_demain_est_perime_le_lendemain(self):
        """Le plan vaut pour le jour où il est calculé : demain, on recalcule."""
        self._sans_place(_tarifs_tempo(demain="BLUE"), demain=_soleil(jour=5))
        with Horloge(7, jour=5):
            lendemain = machines.dernier_resultat()
            self.assertTrue(lendemain["perime"])
            self.assertIsNone(lendemain["prochaine_demain"])
            self.assertIsNone(info.plan_machines())
        with Horloge(7, jour=5), _besoins(_soleil(jour=5), _tarifs_tempo()):
            cycle, = machines.calculer()["cycles"]
        self.assertEqual((cycle["conseil"], cycle["heure"]), ("jour", "08:00"))
        self.assertFalse(cycle["lancee"])

    def test_un_plan_d_avant_le_switch_se_relit(self):
        """Un plan mémorisé par l'ancienne version n'a aucune des clés
        nouvelles : il se lit comme un plan « sur on »."""
        with Horloge(7), _besoins(_soleil(), _tarifs_tempo()):
            machines.calculer()
        ancien = json.loads(get_setting(machines.CLE_DERNIER, module=api.MODULE))
        for cle in ("optimise", "rouge", "comparer_hc", "lendemain"):
            del ancien[cle]
        for cycle in ancien["cycles"]:
            for cle in ("motif", "demain_debut", "cout_demain"):
                del cycle[cle]
        set_setting(machines.CLE_DERNIER, json.dumps(ancien), module=api.MODULE)
        with Horloge(7):
            relu = machines.dernier_resultat()
        self.assertTrue(relu["comparer_hc"])
        self.assertFalse(relu["rouge"])
        self.assertEqual(relu["prochaine"]["heure"], "08:00")
        self.assertEqual(relu["reportees"], 0)

    def test_infos_du_plan(self):
        api.set_machines_demandees(normal=1, court=1)
        with Horloge(7), _besoins(_soleil(), _tarifs_tempo()):
            machines.calculer()
            self.assertEqual(info.prochaine_machine(), "08:00")
            self.assertEqual(info.machines_restantes(), 2)
            self.assertEqual(info.plan_machines(), "08:00 court · 08:50 normal")

    def test_action_de_scenario(self):
        with Horloge(7), _besoins(_soleil(), _tarifs_tempo()):
            heure = scenario.recalculer_machines(normales="2", courtes="")
        self.assertEqual(heure, "08:00")
        self.assertEqual(api.machines_demandees(), {"normal": 2, "court": 0})
        self.assertTrue(
            LogEntry.objects.filter(
                module=api.MODULE, message__startswith="Calcul des machines"
            ).exists()
        )

    def test_action_de_scenario_refuse_un_nombre_illisible(self):
        with self.assertRaises(ValueError):
            scenario.recalculer_machines(normales="deux")

    def test_plan_illisible_en_base_ne_plante_pas(self):
        set_setting(machines.CLE_DERNIER, "{pas du json", module=api.MODULE)
        self.assertTrue(machines.dernier_resultat()["jamais_calcule"])


class ReglagesDesMachines(TestCase):

    def test_profils_par_defaut(self):
        normal = api.profil_machine("normal")
        self.assertEqual((normal["duree_min"], normal["kwh"]), (70, 0.30))
        court = api.profil_machine("court")
        self.assertEqual((court["duree_min"], court["kwh"]), (20, 0.05))

    def test_profil_incoherent_ramene_dans_ses_bornes(self):
        set_setting("machine_normal_duree", "40", module=api.MODULE)
        set_setting("machine_normal_chauffe_min", "90", module=api.MODULE)
        set_setting("machine_normal_chauffe_kwh", "5", module=api.MODULE)
        profil = api.profil_machine("normal")
        self.assertEqual(profil["chauffe_min"], 40)
        self.assertEqual(profil["chauffe_kwh"], profil["kwh"])

    def test_type_de_cycle_inconnu(self):
        with self.assertRaises(ValueError):
            api.profil_machine("laine")

    def test_nombre_de_machines_borne(self):
        self.assertEqual(
            api.set_machines_demandees(normal=99, court=-3),
            {"normal": api.MAX_MACHINES, "court": 0},
        )

    def test_nombre_vide_laisse_la_valeur_en_place(self):
        api.set_machines_demandees(normal=2, court=1)
        self.assertEqual(
            api.set_machines_demandees(normal="", court=None),
            {"normal": 2, "court": 1},
        )


class ChauffeEauInchange(TestCase):
    """Le calcul du chauffe-eau ne dépend en rien des machines."""

    POINTS = [(a(10) + timedelta(minutes=30 * i + 15), kw)
              for i, kw in enumerate([1.0, 2.0, 4.0, 4.0, 3.0, 1.0])]

    def test_meilleur_creneau_faible_et_max(self):
        tot = calcul._meilleur_creneau(self.POINTS, 60, 0.3, 2.4, mode="faible")
        pic = calcul._meilleur_creneau(self.POINTS, 60, 0.3, 2.4, mode="max")
        # « faible » : la première heure où le solaire couvre le ballon —
        # 10:30–11:30 produit 3 kWh, soit 2,7 kWh de surplus pour 2,4 de
        # besoin. « max » : le pic, 11:00–12:00, la seule heure à 4 kW.
        self.assertEqual(tot["debut"], a(10, 30))
        self.assertEqual(tot["import_kwh"], 0.0)
        self.assertEqual(pic["debut"], a(11))
        self.assertAlmostEqual(pic["surplus_kwh"], 3.7)

    def test_pas_de_surplus_pas_de_creneau(self):
        points = [(t, 0.2) for t, _kw in self.POINTS]
        self.assertIsNone(calcul._meilleur_creneau(points, 60, 0.3, 2.4))

    def _calculer(self):
        with mock.patch.object(calcul, "_forecast_points", return_value=(self.POINTS, "")), \
                mock.patch.object(calcul, "_tarifs", return_value=(0.1609, 0.1296, "BLUE")):
            return calcul.calculer()

    def test_decision_identique_avec_ou_sans_machines_demandees(self):
        set_setting("optimiser", "oui", module=api.MODULE)
        set_setting("conso_chauffe_eau", "2.40", module=api.MODULE)
        sans = self._calculer()
        api.set_machines_demandees(normal=3, court=2)
        avec = self._calculer()
        for cle in ("heure", "mode", "cout_jour", "cout_nuit", "duree_min"):
            self.assertEqual(sans[cle], avec[cle], cle)
        self.assertEqual(sans["mode"], "solaire")
        self.assertEqual(sans["cout_jour"], 0.0)

    def test_recalcul_du_ballon_replanifie_les_machines(self):
        api.set_machines_demandees(normal=1)
        with mock.patch.object(calcul, "_forecast_points", return_value=([], "")), \
                mock.patch.object(machines, "calculer") as replanifier:
            resultat = api.tache_actualiser()
        replanifier.assert_called_once_with(tracer=True)
        self.assertEqual(resultat["mode"], "nuit")

    def test_recalcul_du_ballon_sans_machine_demandee_ne_planifie_rien(self):
        with mock.patch.object(calcul, "_forecast_points", return_value=([], "")), \
                mock.patch.object(machines, "calculer") as replanifier:
            api.tache_actualiser()
        replanifier.assert_not_called()

    def test_une_panne_des_machines_ne_coute_pas_l_heure_du_ballon(self):
        api.set_machines_demandees(normal=1)
        with mock.patch.object(calcul, "_forecast_points", return_value=([], "")), \
                mock.patch.object(machines, "calculer", side_effect=RuntimeError("boum")):
            resultat = api.tache_actualiser()
        self.assertEqual(resultat["heure"], api.heure_nuit())
        self.assertEqual(get_variable("heure_demarrage_chauffe_eau"), api.heure_nuit())
        self.assertTrue(
            LogEntry.objects.filter(
                module=api.MODULE, level=LogEntry.ERROR, message__contains="boum"
            ).exists()
        )


class PrevisionPubliee(TestCase):
    """Le créneau publié porte la prévision du cycle : durée et énergie.

    C'est ce que le suivi du chauffe-eau fige au démarrage de la chauffe
    pour le rapprocher de ce qu'il mesure.
    """

    def test_l_energie_est_celle_du_calcul_pas_du_reglage(self):
        _ballon("13:30")  # calculé avec 2,4 kWh
        set_setting("conso_chauffe_eau", "3.00", module=api.MODULE)  # modifié après

        creneau = info.creneau_retenu()
        self.assertEqual(
            (creneau["heure"], creneau["duree_min"], creneau["besoin_kwh"]),
            ("13:30", 60, 2.4),
        )

    def test_sans_calcul_le_reglage_tient_lieu_de_prevision(self):
        set_variable("heure_demarrage_chauffe_eau", "04:30")  # saisie à la main
        set_setting("conso_chauffe_eau", "2.10", module=api.MODULE)

        creneau = info.creneau_retenu()
        self.assertEqual((creneau["heure"], creneau["besoin_kwh"]), ("04:30", 2.1))

    def test_aucune_heure_aucune_prevision(self):
        self.assertIsNone(info.creneau_retenu())


def _estimation(annonce, branche=True):
    """Remplace ``core.liaisons.lire_besoin`` : seule l'estimation répond.

    ``annonce`` est ce que publie le fournisseur ; ``branche=False`` simule
    un besoin laissé libre dans Configuration → Liaisons.
    """
    def lire(_module, nom):
        if nom == "estimation_chauffe" and branche:
            return annonce, ""
        return None, "besoin non branché (voir Configuration → Liaisons)"
    return mock.patch("core.liaisons.lire_besoin", side_effect=lire)


ESTIMEE = {"disponible": True, "duree_min": 46, "besoin_kwh": 1.84,
           "temperature": 50.5, "chauffes": 25, "bornee": False, "raison": ""}
MUETTE = {"disponible": False, "duree_min": None, "besoin_kwh": None,
          "temperature": 38.0, "chauffes": 25, "bornee": False,
          "raison": "ballon à 38 °C, hors de la plage connue (44,4 à 54,9 °C)"}


class DureeEstimeeParLeBallon(TestCase):
    """La durée et l'énergie de la chauffe viennent du ballon quand il sait.

    Le garde-fou qui compte : dans tous les cas où l'estimation manque, le
    calcul retombe sur les réglages de saison — le comportement d'avant.
    """

    def setUp(self):
        set_setting("temp_chauffe_ete", "60", module=api.MODULE)
        set_setting("conso_chauffe_eau", "2.40", module=api.MODULE)

    def _calculer(self):
        # Aucune prévision solaire : la décision (heures creuses) n'est pas
        # le sujet, seules la durée et l'énergie retenues le sont.
        with mock.patch.object(calcul, "_forecast_points", return_value=([], "")):
            return calcul.calculer()

    def test_l_estimation_remplace_les_reglages(self):
        with _estimation(ESTIMEE):
            r = self._calculer()

        self.assertEqual((r["duree_min"], r["besoin_kwh"]), (46, 1.84))
        self.assertTrue(r["estimation"]["disponible"])
        self.assertIn(
            "ballon à 50,5 °C → durée de chauffe estimée 46 min ; "
            "besoin du ballon estimé 1.84 kWh (d'après 25 chauffes mesurées)",
            r["detail"][0],
        )

    def test_elle_est_publiee_et_relue_telle_quelle(self):
        """Mémorisée avec le calcul : c'est elle que le suivi comparera au
        réel, et que les machines retirent de la production."""
        with _estimation(ESTIMEE):
            self._calculer()

        creneau = info.creneau_retenu()
        self.assertEqual((creneau["duree_min"], creneau["besoin_kwh"]), (46, 1.84))
        self.assertEqual(info.duree_chauffe_min(), 46)
        self.assertTrue(calcul.dernier_resultat()["estimation"]["disponible"])

    def test_besoin_non_branche_rien_ne_change(self):
        with _estimation(ESTIMEE, branche=False):
            r = self._calculer()

        self.assertEqual((r["duree_min"], r["besoin_kwh"]), (60, 2.4))
        self.assertIsNone(r["estimation"])
        self.assertTrue(r["detail"][0].startswith(
            "Données : saison ete → durée de chauffe 60 min ; "
            "besoin du ballon 2.40 kWh ; talon maison"
        ))

    def test_le_fournisseur_se_tait_on_garde_les_reglages_et_on_dit_pourquoi(self):
        with _estimation(MUETTE):
            r = self._calculer()

        self.assertEqual((r["duree_min"], r["besoin_kwh"]), (60, 2.4))
        self.assertFalse(r["estimation"]["disponible"])
        self.assertIn("saison ete → durée de chauffe 60 min", r["detail"][0])
        self.assertIn("pas d'estimation d'après le ballon — ballon à 38 °C", r["detail"][0])

    def test_le_repli_suit_la_saison(self):
        from core.models import Control

        set_setting("temp_chauffe_hiver", "90", module=api.MODULE)
        Control.objects.create(type=Control.SWITCH, name="hiver", label="Hiver", is_on=True)
        with _estimation(MUETTE):
            r = self._calculer()
        self.assertEqual((r["saison"], r["duree_min"]), ("hiver", 90))

    def test_une_annonce_inutilisable_vaut_un_silence(self):
        """Durée nulle, énergie illisible, mauvais type : jamais de chauffe
        planifiée sur une valeur absurde."""
        inutilisables = (
            None,                                      # rien à répondre
            "46 min",                                  # mauvais type branché
            {},                                        # objet vide
            dict(ESTIMEE, duree_min=0),                # durée nulle
            dict(ESTIMEE, duree_min=-5),
            dict(ESTIMEE, besoin_kwh="beaucoup"),      # énergie illisible
            dict(ESTIMEE, besoin_kwh=0),
        )
        for annonce in inutilisables:
            with self.subTest(annonce=annonce):
                with _estimation(annonce):
                    r = self._calculer()
                self.assertEqual((r["duree_min"], r["besoin_kwh"]), (60, 2.4))

    def test_une_liaison_en_panne_vaut_un_silence(self):
        # « lire_besoin » ne lève jamais : une panne arrive sous cette forme.
        with mock.patch("core.liaisons.lire_besoin",
                        return_value=(None, "lecture en erreur : boum")):
            r = self._calculer()
        self.assertEqual((r["duree_min"], r["besoin_kwh"]), (60, 2.4))

    def test_la_duree_estimee_dimensionne_le_creneau(self):
        """25 min tiennent dans un pas de prévision, 60 min en demandent
        deux : le ballon tiède trouve sa place là où il n'y a qu'une
        demi-heure de soleil."""
        eclaircie = [(a(10) + timedelta(minutes=30 * i + 15), kw)
                     for i, kw in enumerate([0.2, 0.2, 5.0, 0.2, 0.2])]
        with mock.patch.object(calcul, "_forecast_points", return_value=(eclaircie, "")):
            with _estimation(dict(ESTIMEE, duree_min=25, besoin_kwh=1.0)):
                court = calcul.calculer()
            with _estimation(ESTIMEE, branche=False):
                long_ = calcul.calculer()

        self.assertEqual(court["creneau"]["duree_h"], 0.5)
        self.assertEqual(court["creneau"]["import_kwh"], 0.0)
        self.assertEqual(long_["creneau"]["duree_h"], 1.0)
        self.assertGreater(long_["creneau"]["import_kwh"], 0.0)

    def test_sans_calcul_du_jour_la_duree_publiee_est_celle_de_la_saison(self):
        self.assertEqual(info.duree_chauffe_min(), 60)

    def test_un_calcul_d_avant_l_estimation_se_relit(self):
        """Le calcul mémorisé avant cette évolution n'a pas la clé."""
        _ballon("13:30")
        r = calcul.dernier_resultat()
        self.assertIsNone(r.get("estimation"))
        self.assertTrue(calcul.detail_texte(dict(r, talon_kwh_h=0.3))[0].startswith(
            "Données : saison ete → durée de chauffe 60 min"
        ))


class LiaisonDeLEstimation(TestCase):
    """L'estimation est un besoin nouveau : la migration core 0016 le
    branche là où le chauffe-eau lit déjà sa prévision chez ce module."""

    CLE = "besoin_estimation_chauffe"

    def _migration(self):
        return importlib.import_module("core.migrations.0016_liaison_estimation_chauffe")

    def test_le_besoin_est_declare_et_branche(self):
        from core.liaisons import besoins_du_module, liaison

        besoin = next(
            b for b in besoins_du_module(api.MODULE) if b["nom"] == "estimation_chauffe"
        )
        self.assertEqual(besoin["type"], "objet")
        self.assertFalse(besoin["obligatoire"])
        # La base de test sort des migrations : 0014 a branché la prévision
        # du chauffe-eau sur ce module, 0016 a donc branché l'estimation.
        self.assertEqual(
            liaison(api.MODULE, "estimation_chauffe"), "chauffe_eau.estimation_chauffe"
        )

    def test_un_branchement_deja_choisi_n_est_pas_ecrase(self):
        set_setting(self.CLE, "autre.estimation", module=api.MODULE)
        self._migration().brancher(apps, None)
        self.assertEqual(get_setting(self.CLE, module=api.MODULE), "autre.estimation")

    def test_sans_chauffe_eau_branche_sur_ce_module_on_ne_devine_pas(self):
        Setting.objects.filter(module=api.MODULE, key=self.CLE).delete()
        Setting.objects.filter(module="chauffe_eau", key="besoin_prevision_chauffe").delete()
        self._migration().brancher(apps, None)
        self.assertFalse(
            Setting.objects.filter(module=api.MODULE, key=self.CLE).exists()
        )

    def test_debrancher_ne_retire_que_ce_qu_elle_a_pose(self):
        self._migration().debrancher(apps, None)
        self.assertFalse(
            Setting.objects.filter(module=api.MODULE, key=self.CLE).exists()
        )
        set_setting(self.CLE, "autre.estimation", module=api.MODULE)
        self._migration().debrancher(apps, None)
        self.assertEqual(get_setting(self.CLE, module=api.MODULE), "autre.estimation")


class LiaisonDuLendemain(TestCase):
    """La prévision de demain est un besoin nouveau : la migration core 0015
    le branche là où la prévision du jour vient déjà du même fournisseur."""

    CLE = "besoin_prevision_pv_demain"

    def _migration(self):
        return importlib.import_module("core.migrations.0015_liaison_prevision_demain")

    def test_le_besoin_est_declare_et_branche(self):
        from core.liaisons import besoins_du_module, liaison

        besoin = next(
            b for b in besoins_du_module(api.MODULE) if b["nom"] == "prevision_pv_demain"
        )
        self.assertEqual((besoin["type"], besoin["unite"]), ("serie", "kW"))
        self.assertFalse(besoin["obligatoire"])
        # La base de test sort des migrations : 0012 a branché la prévision
        # du jour sur Solaire, 0015 a donc branché celle de demain.
        self.assertEqual(
            liaison(api.MODULE, "prevision_pv_demain"), "solcast.prevision_pv_demain"
        )

    def test_un_branchement_deja_choisi_n_est_pas_ecrase(self):
        set_setting(self.CLE, "autre.prevision", module=api.MODULE)
        self._migration().brancher(apps, None)
        self.assertEqual(get_setting(self.CLE, module=api.MODULE), "autre.prevision")

    def test_sans_solaire_pour_la_prevision_du_jour_on_ne_devine_pas(self):
        Setting.objects.filter(module=api.MODULE, key=self.CLE).delete()
        set_setting("besoin_prevision_pv", "autre.prevision", module=api.MODULE)
        self._migration().brancher(apps, None)
        self.assertFalse(
            Setting.objects.filter(module=api.MODULE, key=self.CLE).exists()
        )


@modify_settings(INSTALLED_APPS={"append": "modules.heure_demarrage"})
class PagesDuModule(TestCase):
    """L'onglet et les blocs du tableau de bord, module activé."""

    URL = "/module/heure_demarrage/"

    def setUp(self):
        connecte(self.client)
        Module.objects.create(
            name="heure_demarrage", label="Heure démarrage", enabled=True
        )

    def _poster(self, **champs):
        with Horloge(7), _besoins(_soleil(), _tarifs_tempo()):
            return self.client.post(self.URL, champs)

    def test_onglet_sans_aucun_calcul(self):
        reponse = self.client.get(self.URL)
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, "Machines — heures de lancement")
        self.assertContains(reponse, "Aucun plan calculé")
        self.assertContains(reponse, "Profil des machines")

    def test_planifier_depuis_l_onglet(self):
        reponse = self._poster(action="machines", machines_normal="1", machines_court="1")
        self.assertRedirects(reponse, self.URL)
        self.assertEqual(api.machines_demandees(), {"normal": 1, "court": 1})

        with Horloge(7):
            page = self.client.get(self.URL)
        self.assertContains(page, "lancer à 08:00")
        self.assertContains(page, "lancer à 08:50")
        self.assertContains(page, "Détail du calcul")

    def test_planifier_depuis_le_tableau_de_bord_y_retourne(self):
        reponse = self._poster(
            action="machines", machines_normal="1", machines_court="0",
            retour="dashboard",
        )
        self.assertRedirects(reponse, "/")

        with Horloge(7):
            page = self.client.get("/")
        self.assertContains(page, "Cycles normaux")
        self.assertContains(page, "08:00")
        self.assertContains(page, "Cycle normal")
        self.assertFalse(LogEntry.objects.filter(level=LogEntry.ERROR).exists())

    # --- Switch « Optimisé » ---

    def test_le_switch_est_sur_le_bloc_et_dans_l_onglet(self):
        bloc = self._bloc_machines()
        self.assertIn('id="machinesOptimiseBloc"', bloc)
        self.assertIn('value="machines_optimise"', bloc)
        self.assertRegex(bloc, r'id="machinesOptimiseBloc"[^>]*checked')
        self.assertContains(self.client.get(self.URL), 'id="machinesOptimiseOnglet"')

    def test_basculer_le_switch_l_enregistre_et_refait_le_plan(self):
        nuages = plateau_local(datetime(2026, 10, 4).astimezone(), kw=0.0)
        with Horloge(7), _besoins(nuages, _tarifs_tempo()):
            self.client.post(self.URL, {"action": "machines", "machines_normal": "1"})
            self.assertIn("heures creuses, à partir de 22:00", self._bloc_machines())

            # Case décochée = champ absent : c'est ainsi qu'un navigateur l'envoie
            reponse = self.client.post(
                self.URL, {"action": "machines_optimise", "retour": "dashboard"}
            )
            self.assertRedirects(reponse, "/")
            self.assertFalse(api.machines_optimise())
            bloc = self._bloc_machines()
            self.assertNotRegex(bloc, r'id="machinesOptimiseBloc"[^>]*checked')
            self.assertNotIn("heures creuses, à partir de 22:00", bloc)
            self.assertIn("08:00", bloc)
            self.assertIn("plage de lancement seule", bloc)

            self.client.post(self.URL, {"action": "machines_optimise", "optimise": "oui"})
            self.assertTrue(api.machines_optimise())
            self.assertIn("heures creuses, à partir de 22:00", self._bloc_machines())
        self.assertTrue(
            LogEntry.objects.filter(
                module=api.MODULE, message__contains="switch « Optimisé » sur off"
            ).exists()
        )
        self.assertFalse(LogEntry.objects.filter(level=LogEntry.ERROR).exists())

    def test_basculer_le_switch_sans_machine_demandee_ne_planifie_rien(self):
        self.client.post(self.URL, {"action": "machines_optimise"})
        self.assertFalse(api.machines_optimise())
        self.assertTrue(machines.dernier_resultat()["jamais_calcule"])

    def test_calculer_et_enregistrer_le_profil_ne_touchent_pas_au_switch(self):
        api.set_machines_optimise(False)
        self._poster(action="machines", machines_normal="1", machines_court="0")
        champs = {k: d for k, d, _t in api.REGLAGES_MACHINES}
        self._poster(action="machines_params", **champs)
        self.assertFalse(api.machines_optimise())

    def test_jour_rouge_le_bloc_le_dit(self):
        api.set_machines_optimise(False)
        with Horloge(7), _besoins(_soleil(), _tarifs_tempo("RED")):
            self.client.post(self.URL, {"action": "machines", "machines_normal": "1"})
            bloc = self._bloc_machines()
        self.assertIn("jour rouge : sans effet aujourd", bloc)
        self.assertNotRegex(bloc, r'id="machinesOptimiseBloc"[^>]*checked')

    def test_un_cycle_reporte_a_demain_s_affiche(self):
        api.set_machines_optimise(False)
        with Horloge(21), _besoins(
            _soleil(), _tarifs_tempo(demain="BLUE"), demain=_soleil(jour=5)
        ):
            reponse = self.client.post(
                self.URL, {"action": "machines", "machines_normal": "1"}, follow=True
            )
            bloc = self._bloc_machines()
        self.assertContains(reponse, "prochain lancement demain à 08:00")
        self.assertContains(reponse, "demain à 08:00</strong>")
        self.assertContains(reponse, "cts de moins qu'en heures creuses ce soir")
        self.assertIn("<strong>demain</strong>, jusqu", bloc)
        self.assertIn("08:00", bloc)
        # Le chauffe-eau de demain n'est qu'estimé : l'écran n'en dit rien
        self.assertNotIn("chauffe-eau", bloc.split("<strong>demain</strong>")[1].split("</div>")[0])
        self.assertFalse(LogEntry.objects.filter(level=LogEntry.ERROR).exists())

    def _bloc_machines(self):
        """HTML du seul bloc « Machines » du tableau de bord."""
        page = self.client.get("/")
        return next(
            b["html"] for b in page.context["blocs"] if b["key"] == "heure_demarrage.1"
        )

    def test_la_ligne_disparait_quand_l_heure_est_passee(self):
        self._poster(action="machines", machines_normal="2", machines_court="0")
        lancer = '<strong style="color:#f59e0b">lancer à {}</strong>'

        with Horloge(7):
            bloc = self._bloc_machines()
        self.assertIn("08:00", bloc)
        self.assertIn("09:40", bloc)
        self.assertNotIn("Tout replanifier", bloc)

        # 08:30 : la machine de 08:00 n'est plus montrée, celle de 09:40 si
        with Horloge(8, 30):
            bloc = self._bloc_machines()
            onglet = self.client.get(self.URL)
        self.assertNotIn("08:00", bloc)
        self.assertIn("09:40", bloc)
        self.assertIn("Tout replanifier", bloc)
        self.assertNotContains(onglet, lancer.format("08:00"))
        self.assertContains(onglet, lancer.format("09:40"))
        self.assertContains(onglet, "supposées lancées et masquées : 1")

        # 11:00 : plus rien à lancer
        with Horloge(11):
            bloc = self._bloc_machines()
            onglet = self.client.get(self.URL)
        self.assertNotIn("09:40", bloc)
        self.assertIn("Plus de machine à lancer", bloc)
        self.assertContains(onglet, "Plus de machine à lancer")
        self.assertContains(onglet, "supposées lancées et masquées : 2")
        self.assertFalse(LogEntry.objects.filter(level=LogEntry.ERROR).exists())

    def test_le_bloc_du_chauffe_eau_garde_sa_cle(self):
        """Le bloc d'origine reste le premier : sa place enregistrée dans la
        disposition du tableau de bord (« heure_demarrage.0 ») est conservée."""
        page = self.client.get("/")
        cles = [b["key"] for b in page.context["blocs"]]
        self.assertEqual(cles, ["heure_demarrage.0", "heure_demarrage.1"])
        titres = [b["titre"] for b in page.context["blocs"]]
        self.assertEqual(titres, ["Heure démarrage", "Machines"])

    def test_enregistrer_le_profil(self):
        champs = {k: d for k, d, _t in api.REGLAGES_MACHINES}
        champs.update(action="machines_params", machine_normal_duree="95",
                      machine_normal_kwh="0,85", machine_pause_min="0",
                      machine_plage_debut="09:30")
        reponse = self.client.post(self.URL, champs)
        self.assertRedirects(reponse, self.URL)
        profil = api.profil_machine("normal")
        self.assertEqual((profil["duree_min"], profil["kwh"]), (95, 0.85))
        self.assertEqual(api.pause_machines_min(), 0)
        self.assertEqual(api.plage_machines(), ("09:30", "20:00"))

    def test_saisie_illisible_garde_l_ancienne_valeur(self):
        champs = {k: d for k, d, _t in api.REGLAGES_MACHINES}
        champs.update(action="machines_params", machine_normal_duree="longtemps")
        self.client.post(self.URL, champs)
        self.assertEqual(api.profil_machine("normal")["duree_min"], 70)

    def test_les_reglages_du_chauffe_eau_s_enregistrent_toujours(self):
        reponse = self.client.post(self.URL, {
            "action": "params", "temp_chauffe_ete": "75", "temp_chauffe_hiver": "90",
            "conso_min_maison": "0,40", "conso_chauffe_eau": "2,40",
            "heure_nuit": "04:00", "ajustement": "max", "optimiser": "on",
        })
        self.assertRedirects(reponse, self.URL)
        self.assertEqual(api.temp_chauffe_ete(), 75)
        self.assertEqual(api.ajustement(), "max")
        self.assertTrue(api.optimiser())
        self.assertEqual(get_variable("conso_chauffe_eau"), "2.40")
