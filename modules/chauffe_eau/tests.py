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
  chauffes ;
- la fin de chauffe est estimée : la passerelle ne rafraîchit le ballon
  que toutes les dix minutes, la durée et l'énergie relevées telles quelles
  seraient arrondies à la dizaine supérieure ;
- le modèle de durée n'apprend que des chauffes planifiées et sans eau
  tirée, et se tait plutôt que de deviner (trop peu de chauffes, ballon
  hors de la plage connue) ;
- la température du ballon au moment du calcul est gardée avec la chauffe,
  pour mesurer de combien il bouge avant le départ ;
- à partir de dix chauffes mesurées, cet écart moyen est retiré de la
  température lue avant d'estimer — jamais avant, jamais si le réglage le
  coupe, et c'est toujours la température lue qui est publiée et gardée.

Les tables du module n'existent que s'il est activé dans la base qui a servi
à lancer les tests : sinon ces tests sont sautés, plutôt que d'échouer sur
une table absente.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import mock, skipUnless

from django.apps import apps
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from core.liaisons import liaison, set_liaison
from core.models import LogEntry, Module
from core.services import set_setting
from core.tests import connecte

from .fonctions import api, modele, releves, suivi

MODULE_ACTIF = apps.is_installed("modules.chauffe_eau")
SANS_MODULE = "module chauffe_eau non activé : ses tables n'existent pas"

# Forme publiée par le fournisseur — voir docs/09-liaisons-entre-modules.md.
PREVISION = {"heure": "13:30", "duree_min": 60, "besoin_kwh": 2.5,
             "mode": "solaire", "forcee": False, "perime": False}


def a(heure, minute=0):
    """Instant du jour de test, en heure locale du serveur."""
    return timezone.make_aware(datetime(2026, 10, 5, heure, minute))


def _statut(puissance, temperature=40.0, bas=None):
    """Statut du ballon tel que le rend ``api.get_status_cached``."""
    raw = {
        "modbuslink:MiddleWaterTemperatureState": temperature,
        "core:TargetDHWTemperatureState": 62.0,
        "modbuslink:PowerHeatElectricalState": puissance,
        "modbuslink:PowerHeatPumpState": 0,
    }
    if bas is not None:
        raw["core:BottomTankWaterTemperatureState"] = bas
    return {"heating": "on" if puissance else "off", "raw": raw}


def _besoins(prevision=PREVISION, heure="13:30"):
    """Remplace ``core.liaisons.lire_besoin`` : heure et prévision de chauffe."""
    def lire(_module, nom):
        if nom == "heure_chauffe_prevue":
            return heure, ""
        if nom == "prevision_chauffe":
            return prevision, ""
        return None, "besoin inconnu"
    return mock.patch("core.liaisons.lire_besoin", side_effect=lire)


def _releve(instant, puissance, temperature=40.0, bas=None):
    """Un passage de la tâche de suivi, à l'instant donné."""
    with mock.patch("django.utils.timezone.now", return_value=instant), \
            mock.patch.object(api, "get_status_cached",
                              return_value=(_statut(puissance, temperature, bas), None, "")):
        suivi.tache_suivi()


def _chauffer(debut, minutes=60, puissance=2400):
    """Rejoue une chauffe : un relevé par minute, puis le relevé d'arrêt."""
    for i in range(minutes):
        _releve(debut + timedelta(minutes=i), puissance, 20.0 + 40.0 * i / minutes)
    _releve(debut + timedelta(minutes=minutes), 0, 60.0)


def _session():
    from .models import ChauffeSession

    return ChauffeSession.objects.get()


# Une chauffe telle que la passerelle la montre vraiment (relevée le
# 19/09/2026) : les valeurs ne bougent que toutes les dix minutes. Par
# palier : (minute, milieu de cuve, bas de cuve). Au dernier, la résistance
# est vue éteinte — alors qu'elle s'est arrêtée quelque part avant.
PALIERS = [(0, 46.9, 40.1), (10, 52.8, 44.3), (20, 57.9, 49.9), (30, 60.7, 55.2),
           (40, 62.7, 58.7), (50, 64.8, 61.4), (60, 64.8, 62.7)]


def _par_paliers(debut, paliers=PALIERS):
    """Relevés minute d'une chauffe dont l'état n'avance que par paliers.

    Rend ``[(instant, puissance, milieu, bas), …]`` : entre deux paliers la
    même valeur est relue, comme en production.
    """
    fin = paliers[-1][0]
    lignes = []
    for minute in range(fin + 1):
        _m, milieu, bas = [p for p in paliers if p[0] <= minute][-1]
        lignes.append((debut + timedelta(minutes=minute),
                       0 if minute == fin else 2400, milieu, bas))
    return lignes


def _chauffer_par_paliers(debut, paliers=PALIERS):
    for instant, puissance, milieu, bas in _par_paliers(debut, paliers):
        _releve(instant, puissance, milieu, bas)


def _mesures(debut, paliers=PALIERS):
    """Les mêmes relevés, en objets nus pour les fonctions de ``releves``."""
    return [
        SimpleNamespace(quand=instant, temp_milieu=milieu, temp_bas=bas,
                        puissance_elec=float(puissance), puissance_pac=0.0)
        for instant, puissance, milieu, bas in _par_paliers(debut, paliers)
    ]


class LectureDesReleves(SimpleTestCase):
    """Ce que la série de relevés dit de la chauffe, malgré les paliers."""

    def test_la_fin_est_situee_entre_les_deux_derniers_rafraichissements(self):
        # Le bas de cuve montait de 2,7 °C en dix minutes (58,7 → 61,4) ; il
        # lui restait 1,3 °C à prendre : un peu moins de cinq minutes.
        fin = releves.fin_estimee(_mesures(a(13, 30)))
        self.assertEqual(fin.replace(second=0, microsecond=0), a(14, 24))

    def test_l_energie_s_arrete_a_la_fin_estimee(self):
        lu = releves.bilan(_mesures(a(13, 30)), a(13, 30))
        self.assertAlmostEqual(lu["duree_estimee_min"], 54.8, places=1)
        # 54,8 min à 2 400 W, et non les 60 min relevées (2 400 Wh).
        self.assertAlmostEqual(lu["elec_wh"], 2400 * 54.8 / 60, delta=5)
        self.assertEqual(lu["pac_wh"], 0.0)
        self.assertFalse(lu["tirage"])

    def test_l_estimation_ne_sort_jamais_de_l_intervalle(self):
        """Un bas de cuve qui bondit au dernier palier ne repousse pas la
        fin après le relevé de clôture, ni avant le dernier rafraîchissement."""
        tard = PALIERS[:-1] + [(60, 64.8, 75.0)]
        tot = PALIERS[:-1] + [(60, 64.8, 61.4)]
        self.assertEqual(releves.fin_estimee(_mesures(a(13, 30), tard)), a(14, 30))
        self.assertEqual(releves.fin_estimee(_mesures(a(13, 30), tot)), a(14, 20))

    def test_sans_bas_de_cuve_la_fin_relevee_fait_foi(self):
        mesures = _mesures(a(13, 30))
        for m in mesures:
            m.temp_bas = None
        lu = releves.bilan(mesures, a(13, 30))
        self.assertIsNone(lu["duree_estimee_min"])
        self.assertAlmostEqual(lu["elec_wh"], 2400.0, places=1)

    def test_trop_peu_de_paliers_pour_estimer(self):
        self.assertIsNone(releves.fin_estimee(_mesures(a(13, 30), PALIERS[:1] + [(8, 50.0, 42.0)])))
        self.assertIsNone(releves.fin_estimee([]))

    def test_bas_de_cuve_qui_ne_monte_plus(self):
        plat = PALIERS[:4] + [(40, 62.7, 55.2), (50, 64.8, 55.2), (60, 64.8, 55.3)]
        self.assertIsNone(releves.fin_estimee(_mesures(a(13, 30), plat)))

    def test_eau_tiree_pendant_la_chauffe(self):
        """Relevé le 09/09/2026 : le bas de cuve redescend de 44,1 à 40,2 °C."""
        tirage = [(0, 48.6, 43.8), (9, 51.4, 44.1), (19, 51.6, 40.2), (30, 55.3, 43.8),
                  (39, 58.6, 49.5), (49, 60.8, 54.9), (59, 63.0, 58.6), (70, 64.9, 61.4),
                  (79, 64.9, 62.6)]
        self.assertTrue(releves.eau_tiree(_mesures(a(13, 30), tirage)))
        self.assertFalse(releves.eau_tiree(_mesures(a(13, 30))))

    def test_un_trou_dans_les_releves_ne_cree_pas_d_energie(self):
        mesures = _mesures(a(13, 30), [(0, 50.0, 45.0), (1, 51.0, 46.0)])
        mesures[-1].quand = a(15, 30)  # service arrêté deux heures
        elec, _pac = releves.energie_wh(mesures)
        self.assertAlmostEqual(elec, 2400 * 5 / 60, places=1)


@skipUnless(MODULE_ACTIF, SANS_MODULE)
class ClotureDeLaChauffe(TestCase):
    """La chauffe enregistrée porte sa durée réelle, pas la durée relevée."""

    def test_duree_et_energie_corrigees(self):
        with _besoins():
            _chauffer_par_paliers(a(13, 30))

        s = _session()
        self.assertEqual(s.duree_min, 60)                    # ce qui a été relevé
        self.assertAlmostEqual(s.duree_estimee_min, 54.8, places=1)
        self.assertAlmostEqual(s.duree_reelle_min, 54.8, places=1)
        self.assertAlmostEqual(s.energie_wh, 2400 * 54.8 / 60, delta=5)
        self.assertFalse(s.tirage)
        # La prévision (60 min, 2 500 Wh) est jugée sur la durée réelle.
        self.assertEqual(s.ecart_duree_min, -5)
        self.assertTrue(
            LogEntry.objects.filter(
                module="chauffe_eau", message__contains="arrêt réel estimé à 55 min"
            ).exists()
        )

    def test_chauffe_relevee_a_la_minute_rien_a_corriger(self):
        """Si un jour la passerelle pousse chaque minute, l'estimation se
        confond avec la fin relevée."""
        with _besoins():
            for i in range(30):
                _releve(a(13, 30) + timedelta(minutes=i), 2400, 50 + i * 0.5, 45 + i * 0.5)
            _releve(a(14, 0), 0, 65.0, 60.0)

        s = _session()
        self.assertAlmostEqual(s.duree_estimee_min, 30.0, places=1)
        self.assertAlmostEqual(s.energie_wh, 1200.0, places=0)

    def test_une_chauffe_a_l_heure_prevue_est_planifiee(self):
        with _besoins():
            _chauffer_par_paliers(a(13, 31))
        self.assertIs(_session().planifiee, True)
        self.assertTrue(_session().apprend)

    def test_une_chauffe_lancee_a_la_main_ne_l_est_pas(self):
        """Veille d'un retour d'absence, relance du ballon : mesurée, mais
        elle ne règle pas le modèle."""
        with _besoins():
            _chauffer_par_paliers(a(19, 0))
        s = _session()
        self.assertIs(s.planifiee, False)
        self.assertGreater(s.energie_wh, 0)
        self.assertFalse(s.apprend)

    def test_planifiee_meme_sans_prevision_a_comparer(self):
        """L'heure prévue suffit : la prévision détaillée peut manquer."""
        with _besoins(prevision=None):
            _chauffer_par_paliers(a(13, 30))
        s = _session()
        self.assertEqual(s.prevu_heure, "")
        self.assertIs(s.planifiee, True)

    def test_eau_tiree_la_chauffe_est_marquee(self):
        tirage = [(0, 48.6, 43.8), (10, 51.4, 44.1), (20, 51.6, 40.2), (30, 55.3, 43.8),
                  (40, 58.6, 49.5), (50, 60.8, 54.9), (60, 64.9, 61.4), (70, 64.9, 62.6)]
        with _besoins():
            _chauffer_par_paliers(a(13, 30), tirage)
        s = _session()
        self.assertTrue(s.tirage)
        self.assertFalse(s.apprend)
        self.assertTrue(
            LogEntry.objects.filter(
                module="chauffe_eau", message__contains="eau tirée pendant la chauffe"
            ).exists()
        )


@skipUnless(MODULE_ACTIF, SANS_MODULE)
class EntreLeCalculEtLaChauffe(TestCase):
    """La prévision est faite à une température, la chauffe part à une autre."""

    # Calculée à 11 h avec un ballon à 51,5 °C, pour une chauffe à 13 h 30.
    AVEC_TEMPERATURE = dict(PREVISION, temperature=51.5,
                            calcule_a="2026-10-05T11:00:00")

    def test_la_temperature_et_l_heure_du_calcul_sont_gardees(self):
        with _besoins(self.AVEC_TEMPERATURE):
            _chauffer_par_paliers(a(13, 30))     # départ réel à 46,9 °C

        s = _session()
        self.assertEqual(s.prevu_temp, 51.5)
        self.assertEqual(s.prevu_calcule_a, a(11, 0))
        self.assertEqual(s.baisse_avant_chauffe, 4.6)
        self.assertEqual(s.delai_avant_chauffe_min, 150)
        self.assertTrue(
            LogEntry.objects.filter(
                module="chauffe_eau",
                message__contains="prévision faite avec un ballon à 51.5 °C, 2 h 30 plus tôt",
            ).exists()
        )

    def test_un_recalcul_pendant_la_chauffe_ne_change_rien(self):
        with _besoins(self.AVEC_TEMPERATURE):
            _releve(a(13, 30), 2400, 46.9)
        with _besoins(dict(self.AVEC_TEMPERATURE, temperature=60.0,
                           calcule_a="2026-10-05T13:31:00")):
            _releve(a(13, 31), 2400, 46.9)
            _releve(a(13, 32), 0, 47.0)
        self.assertEqual(_session().prevu_temp, 51.5)
        self.assertEqual(_session().prevu_calcule_a, a(11, 0))

    def test_prevision_sans_temperature(self):
        """Durée venue des réglages, ancien fournisseur : rien à garder."""
        with _besoins():
            _chauffer_par_paliers(a(13, 30))
        s = _session()
        self.assertIsNone(s.prevu_temp)
        self.assertIsNone(s.prevu_calcule_a)
        self.assertIsNone(s.baisse_avant_chauffe)
        self.assertIsNone(s.delai_avant_chauffe_min)

    def test_valeurs_illisibles_la_chauffe_est_enregistree_quand_meme(self):
        from .models import ChauffeSession

        for champs in ({"temperature": "tiède", "calcule_a": "ce matin"},
                       {"temperature": None, "calcule_a": ""},
                       {"temperature": 51.5, "calcule_a": 12}):
            with self.subTest(champs=champs):
                ChauffeSession.objects.all().delete()
                with _besoins(dict(PREVISION, **champs)):
                    _chauffer_par_paliers(a(13, 30))
                s = _session()
                self.assertEqual(s.prevu_wh, 2500.0)
                self.assertIsNone(s.prevu_calcule_a)
                self.assertIsNone(s.delai_avant_chauffe_min)

    def test_chauffe_hors_de_l_heure_prevue_rien_n_est_garde(self):
        with _besoins(self.AVEC_TEMPERATURE):
            _chauffer_par_paliers(a(19, 0))
        self.assertIsNone(_session().prevu_temp)

    # --- le bilan ---

    def _mesurees(self, *departs, estimee=52.0, delai_min=150, jour=1, **champs):
        """Chauffes planifiées, estimées à ``estimee`` °C ``delai_min`` avant."""
        for i, depart in enumerate(departs):
            debut = a(13, 30) + timedelta(days=jour + i)
            _chauffe(jour + i, depart, 200 - 3 * depart, prevu_heure="13:30",
                     **{"prevu_temp": estimee,
                        "prevu_calcule_a": debut - timedelta(minutes=delai_min),
                        **champs})

    def test_aucune_mesure(self):
        bilan = suivi.bilan_depart()
        self.assertEqual(bilan["nombre"], 0)
        self.assertFalse(bilan["suffisant"])

    def test_ecart_moyen_et_delai(self):
        self._mesurees(50.0, 49.0, 51.0)         # partis 2, 3 et 1 °C plus froids
        bilan = suivi.bilan_depart()
        self.assertEqual(bilan["nombre"], 3)
        self.assertFalse(bilan["suffisant"])
        self.assertEqual(
            (bilan["ecart_moyen"], bilan["ecart_min"], bilan["ecart_max"]),
            (-2.0, -3.0, -1.0),
        )
        self.assertEqual(bilan["delai_moyen"], "2 h 30")
        self.assertIsNone(bilan["minutes"])      # pas encore de modèle fiable

    def test_l_ecart_est_traduit_en_minutes_de_chauffe(self):
        """2 °C plus froid au départ, à 3 min par degré : 6 min de plus."""
        self._mesurees(*[45.0 + i for i in range(12)], estimee=None, jour=1)
        from .models import ChauffeSession

        for s in ChauffeSession.objects.all():   # estimée 2 °C au-dessus du départ
            s.prevu_temp = s.temp_debut + 2.0
            s.save()
        bilan = suivi.bilan_depart()
        self.assertTrue(bilan["suffisant"])
        self.assertEqual(bilan["ecart_moyen"], -2.0)
        self.assertEqual(bilan["minutes"], 6)

    def test_un_ballon_parti_plus_chaud_se_lit_en_positif(self):
        self._mesurees(53.0, 53.0)
        self.assertEqual(suivi.bilan_depart()["ecart_moyen"], 1.0)

    def test_une_prevision_d_un_autre_jour_ne_compte_pas(self):
        """Calcul non refait : 26 h de délai ne disent rien du délai habituel."""
        self._mesurees(50.0, 50.0)
        self._mesurees(40.0, delai_min=26 * 60, jour=10)
        self._mesurees(40.0, delai_min=-30, jour=11)     # calculée après le départ
        self.assertEqual(suivi.bilan_depart()["nombre"], 2)

    def test_la_chauffe_de_nuit_decidee_la_veille_compte(self):
        self._mesurees(50.0, delai_min=14 * 60)
        bilan = suivi.bilan_depart()
        self.assertEqual((bilan["nombre"], bilan["delai_moyen"]), (1, "14 h 00"))

    def test_seules_les_chauffes_planifiees_comptent(self):
        self._mesurees(50.0)
        self._mesurees(30.0, jour=5, planifiee=False)
        self._mesurees(30.0, jour=6, planifiee=None)
        self.assertEqual(suivi.bilan_depart()["nombre"], 1)

    def test_seules_les_chauffes_recentes_comptent(self):
        self._mesurees(*[40.0] * 5, jour=1)                              # anciennes
        self._mesurees(*[50.0] * suivi.DEPART_CHAUFFES_MAX, jour=10)     # récentes
        bilan = suivi.bilan_depart()
        self.assertEqual(bilan["nombre"], suivi.DEPART_CHAUFFES_MAX)
        self.assertEqual(bilan["ecart_moyen"], -2.0)

    def test_delais_lisibles(self):
        self.assertEqual(suivi._duree_lisible(45), "45 min")
        self.assertEqual(suivi._duree_lisible(125), "2 h 05")


def _chauffe(jour, temperature, duree, **champs):
    """Une chauffe déjà close, pour le modèle : départ, durée, 2 400 W."""
    from .models import ChauffeSession

    debut = a(13, 30) + timedelta(days=jour)
    valeurs = dict(
        debut=debut, fin=debut + timedelta(minutes=duree), duree_min=round(duree),
        duree_estimee_min=duree, temp_debut=temperature, temp_fin=65.0,
        energie_wh=40.0 * duree, energie_elec_wh=40.0 * duree, planifiee=True,
    )
    valeurs.update(champs)
    return ChauffeSession.objects.create(**valeurs)


def _droite(nombre=15, premier_jour=0):
    """``nombre`` chauffes sur la droite durée = 200 − 3 × température,
    départs de 45 à 55 °C."""
    for i in range(nombre):
        temperature = 45.0 + 10.0 * i / (nombre - 1)
        _chauffe(premier_jour + i, temperature, 200 - 3 * temperature)


def _droite_mesuree(nombre=12, ecart=-2.0):
    """``nombre`` chauffes planifiées sur la droite durée = 200 − 3 × départ
    (départs de 45 °C, de degré en degré), dont la prévision avait été faite
    2 h 30 plus tôt avec un ballon ``ecart`` degrés plus loin : parti 2 °C
    plus froid que prévu, par défaut."""
    for i in range(nombre):
        depart = 45.0 + i
        debut = a(13, 30) + timedelta(days=i)
        _chauffe(i, depart, 200 - 3 * depart, prevu_heure="13:30",
                 prevu_temp=depart - ecart,
                 prevu_calcule_a=debut - timedelta(minutes=150))


@skipUnless(MODULE_ACTIF, SANS_MODULE)
class CorrectionDuDepart(TestCase):
    """L'écart entre le calcul et la chauffe, retiré avant d'estimer."""

    def test_a_partir_de_dix_chauffes_l_ecart_est_retire(self):
        _droite_mesuree()
        e = modele.estimation(52.0)
        # Lu à 52 °C, attendu à 50 °C au départ : 200 − 3 × 50 = 50 min, et
        # non les 44 min que donnerait la température lue.
        self.assertTrue(e["disponible"])
        self.assertEqual((e["duree_min"], e["besoin_kwh"]), (50, 2.0))
        self.assertEqual(
            (e["temperature"], e["temperature_depart"], e["ecart_depart"]),
            (52.0, 50.0, -2.0),
        )
        self.assertEqual(e["chauffes_ecart"], 12)

    def test_la_temperature_publiee_reste_celle_qui_est_lue(self):
        """Sinon le suivi mesurerait l'écart de la correction, pas celui du
        ballon, et la correction se corrigerait elle-même."""
        _droite_mesuree()
        self.assertEqual(modele.estimation(52.0)["temperature"], 52.0)

    def test_neuf_chauffes_ne_suffisent_pas(self):
        _droite(nombre=15, premier_jour=40)      # de quoi faire un modèle
        _droite_mesuree(nombre=9)
        correction = modele.correction_depart()
        self.assertEqual((correction["appliquee"], correction["chauffes"]), (False, 9))
        self.assertEqual(correction["ecart"], -2.0)   # mesuré, pas appliqué
        e = modele.estimation(52.0)
        self.assertEqual(e["duree_min"], 44)
        self.assertEqual((e["temperature_depart"], e["ecart_depart"]), (52.0, None))
        self.assertEqual(e["chauffes_ecart"], 0)

    def test_la_dixieme_chauffe_declenche_la_correction(self):
        _droite(nombre=15, premier_jour=40)
        _droite_mesuree(nombre=10)
        self.assertTrue(modele.correction_depart()["appliquee"])
        self.assertEqual(modele.estimation(52.0)["duree_min"], 50)

    def test_sans_aucune_mesure(self):
        _droite()
        correction = modele.correction_depart()
        self.assertEqual(
            (correction["appliquee"], correction["ecart"], correction["chauffes"]),
            (False, None, 0),
        )
        self.assertEqual(modele.estimation(50.0)["temperature_depart"], 50.0)

    def test_le_reglage_coupe_la_correction(self):
        _droite_mesuree()
        set_setting("modele_corriger_depart", "non", module=api.MODULE)
        correction = modele.correction_depart()
        self.assertEqual((correction["active"], correction["appliquee"]), (False, False))
        self.assertEqual(modele.estimation(52.0)["duree_min"], 44)

    def test_un_ballon_qui_part_plus_chaud_raccourcit(self):
        _droite_mesuree(ecart=+1.0)
        e = modele.estimation(50.0)
        self.assertEqual((e["temperature_depart"], e["duree_min"]), (51.0, 47))

    def test_c_est_la_temperature_attendue_qui_doit_etre_dans_la_plage(self):
        """Plage connue 45–56 °C. Lu à 46,5 °C — dans la plage — mais attendu
        à 43,5 °C au départ : ce serait de l'extrapolation."""
        _droite_mesuree(ecart=-3.0)
        e = modele.estimation(46.5)
        self.assertFalse(e["disponible"])
        self.assertIn("ballon à 46,5 °C, attendu à 43,5 °C au départ, hors de la plage", e["raison"])
        # Et l'inverse : lu au-dessus de la plage, attendu dedans.
        self.assertTrue(modele.estimation(58.5)["disponible"])

    def test_la_correction_suit_les_dernieres_chauffes(self):
        """L'écart est une moyenne glissante : il suit les habitudes."""
        _droite_mesuree(nombre=12, ecart=-2.0)
        for i in range(suivi.DEPART_CHAUFFES_MAX):
            depart = 45.0 + i % 10
            debut = a(13, 30) + timedelta(days=30 + i)
            _chauffe(30 + i, depart, 200 - 3 * depart, prevu_heure="13:30",
                     prevu_temp=depart + 4.0,
                     prevu_calcule_a=debut - timedelta(minutes=150))
        self.assertEqual(modele.correction_depart()["ecart"], -4.0)

    def test_une_panne_du_bilan_ne_coute_pas_l_estimation(self):
        _droite()
        with mock.patch.object(suivi, "bilan_depart", side_effect=RuntimeError("boum")):
            e = modele.estimation(50.0)
        self.assertEqual((e["disponible"], e["duree_min"]), (True, 50))
        self.assertIsNone(e["ecart_depart"])


@skipUnless(MODULE_ACTIF, SANS_MODULE)
class ModeleDeDuree(TestCase):
    """La durée de la prochaine chauffe, d'après la température du ballon."""

    def test_la_droite_est_retrouvee(self):
        _droite()
        m = modele.modele()
        self.assertTrue(m["fiable"])
        self.assertEqual(m["nombre"], 15)
        self.assertAlmostEqual(m["pente"], -3.0, places=2)
        self.assertAlmostEqual(m["constante"], 200.0, places=0)
        self.assertEqual((m["temp_min"], m["temp_max"]), (45.0, 55.0))
        self.assertEqual(m["puissance_w"], 2400)
        self.assertEqual(m["erreur_min"], 0.0)

    def test_estimation_dans_la_plage(self):
        _droite()
        e = modele.estimation(50.0)
        self.assertTrue(e["disponible"])
        # 200 − 3 × 50 = 50 min ; à 2 400 W, 2 kWh.
        self.assertEqual((e["duree_min"], e["besoin_kwh"]), (50, 2.0))
        self.assertEqual((e["temperature"], e["chauffes"], e["bornee"]), (50.0, 15, False))

    def test_trop_peu_de_chauffes(self):
        _droite(nombre=11)
        e = modele.estimation(50.0)
        self.assertFalse(e["disponible"])
        self.assertIsNone(e["duree_min"])
        self.assertIsNone(e["besoin_kwh"])
        self.assertIn("11 chauffes exploitables, il en faut 12", e["raison"])

    def test_aucune_chauffe(self):
        e = modele.estimation(50.0)
        self.assertFalse(e["disponible"])
        self.assertIn("0 chauffe exploitable, il en faut 12", e["raison"])

    def test_ballon_hors_de_la_plage_connue(self):
        """Le retour d'absence : bien plus froid que tout ce qui a été vu."""
        _droite()
        for temperature in (30.0, 43.9, 56.1, 62.0):
            with self.subTest(temperature=temperature):
                e = modele.estimation(temperature)
                self.assertFalse(e["disponible"])
                self.assertIn("hors de la plage connue (45 à 55 °C)", e["raison"])

    def test_la_marge_tolere_un_degre(self):
        _droite()
        self.assertTrue(modele.estimation(44.0)["disponible"])
        self.assertTrue(modele.estimation(56.0)["disponible"])
        set_setting("modele_marge_degres", "0", module=api.MODULE)
        self.assertFalse(modele.estimation(44.0)["disponible"])

    def test_temperature_inconnue(self):
        _droite()
        with mock.patch.object(api, "get_status_cached", return_value=(None, None, "")):
            e = modele.estimation()
        self.assertFalse(e["disponible"])
        self.assertEqual(e["raison"], "température du ballon inconnue")

    def test_temperature_lue_dans_le_statut(self):
        _droite()
        with mock.patch.object(api, "get_status_cached",
                               return_value=({"temperature": "52.0"}, None, "")):
            e = modele.estimation()
        self.assertEqual((e["temperature"], e["duree_min"]), (52.0, 44))

    def test_la_duree_reste_dans_les_bornes(self):
        _droite()
        set_setting("modele_duree_max", "40", module=api.MODULE)
        e = modele.estimation(46.0)      # la droite donne 62 min
        self.assertEqual((e["duree_min"], e["bornee"]), (40, True))
        self.assertEqual(e["besoin_kwh"], 1.6)
        set_setting("modele_duree_min", "55", module=api.MODULE)
        set_setting("modele_duree_max", "120", module=api.MODULE)
        e = modele.estimation(54.0)      # la droite donne 38 min
        self.assertEqual((e["duree_min"], e["bornee"]), (55, True))

    def test_seules_les_bonnes_chauffes_apprennent(self):
        """Chauffes à la main, avec eau tirée, trop courtes ou non mesurées :
        enregistrées, mais sans effet sur la droite."""
        _droite()
        _chauffe(20, 25.0, 170.0, planifiee=False)   # veille d'un retour d'absence
        _chauffe(21, 48.0, 110.0, tirage=True)       # douche pendant la chauffe
        _chauffe(22, 64.0, 90.0)                     # 1 °C gagné : pas une chauffe
        _chauffe(23, 50.0, 90.0, energie_wh=0.0)     # puissance non relevée
        _chauffe(24, 50.0, 90.0, fin=None)           # encore en cours
        m = modele.modele()
        self.assertEqual(m["nombre"], 15)
        self.assertAlmostEqual(m["pente"], -3.0, places=2)
        self.assertEqual(m["temp_min"], 45.0)

    def test_les_chauffes_d_avant_le_champ_planifiee_sont_gardees(self):
        for i in range(15):
            temperature = 45.0 + 10.0 * i / 14
            _chauffe(i, temperature, 200 - 3 * temperature, planifiee=None)
        self.assertTrue(modele.modele()["fiable"])

    def test_sans_duree_estimee_la_duree_relevee_sert(self):
        for i in range(15):
            temperature = 45.0 + 10.0 * i / 14
            duree = round(200 - 3 * temperature)
            _chauffe(i, temperature, duree, duree_estimee_min=None)
        m = modele.modele()
        self.assertTrue(m["fiable"])
        self.assertAlmostEqual(m["pente"], -3.0, places=1)

    def test_seules_les_dernieres_chauffes_comptent(self):
        """La fenêtre glisse : c'est ce qui fait suivre la saison."""
        for i in range(25):                           # anciennes, deux fois plus longues
            temperature = 45.0 + 10.0 * i / 24
            _chauffe(i, temperature, 2 * (200 - 3 * temperature))
        _droite(nombre=25, premier_jour=30)           # récentes
        m = modele.modele()
        self.assertEqual(m["nombre"], 25)
        self.assertAlmostEqual(m["pente"], -3.0, places=2)
        set_setting("modele_chauffes_max", "50", module=api.MODULE)
        self.assertEqual(modele.modele()["nombre"], 50)

    def test_une_droite_qui_monte_n_est_pas_un_modele(self):
        for i in range(15):
            _chauffe(i, 45.0 + i, 30.0 + i)           # plus chaud, plus long : absurde
        e = modele.estimation(50.0)
        self.assertFalse(e["disponible"])
        self.assertIn("ne baisse pas quand le ballon part plus chaud", e["raison"])

    def test_toutes_les_chauffes_a_la_meme_temperature(self):
        for i in range(15):
            _chauffe(i, 50.0, 45.0 + i % 3)
        e = modele.estimation(50.0)
        self.assertFalse(e["disponible"])
        self.assertIn("même température", e["raison"])

    def test_reglage_illisible_le_defaut_tient(self):
        set_setting("modele_chauffes_min", "douze", module=api.MODULE)
        self.assertEqual(modele.reglage("modele_chauffes_min"), 12)

    def test_infos_publiees(self):
        from .fonctions import info

        _droite()
        with mock.patch.object(api, "get_status_cached",
                               return_value=({"temperature": 50.0}, None, "")):
            self.assertEqual(info.estimation_chauffe()["duree_min"], 50)
            self.assertEqual(info.duree_chauffe_estimee(), 50)
            self.assertEqual(info.energie_chauffe_estimee(), 2.0)
        with mock.patch.object(api, "get_status_cached",
                               return_value=({"temperature": 20.0}, None, "")):
            self.assertIsNone(info.duree_chauffe_estimee())
            self.assertIsNone(info.energie_chauffe_estimee())

    def test_l_info_est_proposee_aux_liaisons(self):
        from . import conf

        entree = next(i for i in conf.INFOS if i["nom"] == "estimation_chauffe")
        self.assertEqual(entree["type"], "objet")
        self.assertEqual(entree["fonction"], "fonctions.info.estimation_chauffe")


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

    def test_estimation_affichee(self):
        _droite()
        with mock.patch.object(api, "get_status_cached",
                               return_value=({"temperature": 50.0}, None, "")):
            page = self.client.get(self.URL)

        self.assertContains(page, "Prochaine chauffe, d'après la température du ballon")
        self.assertContains(page, "50 min")
        self.assertContains(page, "2,00 kWh")
        self.assertContains(page, "15 chauffes parties à l'heure prévue")
        self.assertNotIn("erreur", page.context["suivi"]["modele"])

    def test_pas_d_estimation_la_raison_est_dite(self):
        page = self._page()
        self.assertContains(page, "Pas d'estimation")
        self.assertContains(page, "0 chauffe exploitable, il en faut 12")

    def test_le_tableau_montre_la_duree_reelle(self):
        with _besoins():
            _chauffer_par_paliers(a(13, 30))
        page = self._page()
        self.assertContains(page, "55 min")
        self.assertContains(page, "(relevé 60)")

    def test_ecart_entre_le_calcul_et_la_chauffe(self):
        page = self._page()
        self.assertContains(page, "Entre le calcul et la chauffe")
        self.assertContains(page, "s'affichera ici dès la première")

        prevision = dict(PREVISION, temperature=51.5, calcule_a="2026-10-05T11:00:00")
        with _besoins(prevision):
            _chauffer_par_paliers(a(13, 30))     # départ réel à 46,9 °C
        page = self._page()
        self.assertContains(page, "-4,6 °C")
        self.assertContains(page, "2 h 30")
        self.assertContains(page, "prévision faite à 51,5 °C")
        self.assertContains(page, "1 chauffe mesurée :")
        self.assertContains(page, "il en faut 10 pour que l'écart soit pris en compte")
        self.assertNotContains(page, "Cet écart est pris en compte")

    def test_la_correction_appliquee_se_voit(self):
        _droite_mesuree()
        with mock.patch.object(api, "get_status_cached",
                               return_value=({"temperature": 52.0}, None, "")):
            page = self.client.get(self.URL)
        self.assertContains(page, "Cet écart est pris en compte")
        self.assertContains(page, "attendu à 50,0 °C")
        self.assertContains(page, "50 min")          # et non 44 : estimé à 50 °C

    def test_la_correction_coupee_se_voit(self):
        _droite_mesuree()
        self.client.post(self.URL, {"action": "params", "modele_corriger_depart": "non"})
        page = self._page()
        self.assertContains(page, "pas pris en compte")
        self.assertNotContains(page, "Cet écart est pris en compte")

    def test_les_reglages_du_modele_s_enregistrent(self):
        self.client.post(self.URL, {
            "action": "params", "modele_chauffes_max": "15", "modele_chauffes_min": "8",
            "modele_duree_min": "20", "modele_duree_max": "90", "modele_marge_degres": "0",
        })
        self.assertEqual(modele.reglages(), {
            "modele_chauffes_max": 15, "modele_chauffes_min": 8, "modele_duree_min": 20,
            "modele_duree_max": 90, "modele_marge_degres": 0,
        })
        # Un formulaire qui ne porte pas le choix ne coupe pas la correction.
        self.assertTrue(modele.correction_active())
        self.client.post(self.URL, {"action": "params", "modele_corriger_depart": "non"})
        self.assertFalse(modele.correction_active())
        self.client.post(self.URL, {"action": "params", "modele_corriger_depart": "oui"})
        self.assertTrue(modele.correction_active())

    def test_verdict_et_ou_corriger(self):
        with _besoins():
            for jour in range(3):
                _chauffer(a(13, 30) + timedelta(days=jour), minutes=45)
        page = self._page()

        self.assertEqual(page.context["suivi"]["bilan"]["verdict"], "trop_haute")
        self.assertContains(page, "trop haute")
        self.assertContains(page, "<strong>Heure démarrage</strong>")
