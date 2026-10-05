# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

"""Tests du module Heure de démarrage.

Deux choses sont vérifiées ici :

- le plan des machines : il respecte la priorité du chauffe-eau, compte la
  chauffe à sa puissance de pointe, compare chaque cycle aux heures creuses,
  et ne repropose pas une machine déjà lancée ;
- le calcul du chauffe-eau, qui pilote une chauffe réelle : l'arrivée des
  machines dans le module ne doit rien changer à ce qu'il décide.
"""

from datetime import datetime, timedelta, timezone
from unittest import mock

from django.test import SimpleTestCase, TestCase, modify_settings

from core.models import LogEntry, Module
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


def _besoins(points=None, tarifs=None):
    """Remplace ``core.liaisons.lire_besoin`` : fournit prévision et tarifs."""
    def lire(_module, nom):
        if nom == "prevision_pv":
            return (points, "") if points is not None else (None, "besoin non branché")
        if nom == "tarifs_jour":
            return tarifs, ""
        return None, "besoin inconnu"
    return mock.patch("core.liaisons.lire_besoin", side_effect=lire)


def _tarifs_tempo(couleur="BLUE"):
    """Objet « tarifs_jour » tel que le module Tempo le publie."""
    return {
        "couleur": couleur,
        "prix": {
            "BLUE": {"HP": 0.1609, "HC": 0.1296},
            "RED": {"HP": 0.7562, "HC": 0.1568},
        },
        "libelles": {"BLUE": "Bleu", "RED": "Rouge"},
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
