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
from datetime import date, datetime, timedelta
from unittest import mock

from django.test import RequestFactory, TestCase

from core.services import get_setting, set_setting

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


class ParametresConserves(TestCase):
    """Un champ de formulaire laissé vide ne doit pas effacer la valeur en place.

    Le paramétrage cloud d'Enphase ne se retrouve pas d'un clic : la clé API
    et le client ID se vont chercher dans le compte développeur. Un
    enregistrement à vide qui les effaçait en silence coûtait bien plus cher
    que le confort d'un champ qu'on peut vider.
    """

    CHAMPS = {
        "cloud_api_key": ("API key", False),
        "cloud_client_secret": ("client secret", True),
    }

    def _enregistrer(self, donnees):
        from .onglet.views import _enregistrer

        return _enregistrer(RequestFactory().post("/", donnees), self.CHAMPS)

    def test_champ_renseigne_est_ecrit(self):
        self._enregistrer({"cloud_api_key": "cle-abc"})
        self.assertEqual(get_setting("cloud_api_key", module=api.MODULE), "cle-abc")

    def test_champ_vide_conserve_la_valeur_precedente(self):
        self._enregistrer({"cloud_api_key": "cle-abc"})
        conserves = self._enregistrer({"cloud_api_key": "   "})
        self.assertEqual(get_setting("cloud_api_key", module=api.MODULE), "cle-abc")
        self.assertIn("API key", conserves)

    def test_champ_vide_sans_valeur_precedente_ne_signale_rien(self):
        self.assertEqual(self._enregistrer({"cloud_api_key": ""}), [])

    def test_le_secret_reste_marque_sensible(self):
        from core.models import Setting

        self._enregistrer({"cloud_client_secret": "chut"})
        self.assertTrue(
            Setting.objects.get(module=api.MODULE, key="cloud_client_secret").secret
        )


class CloudQuota(TestCase):
    """Le cloud Enlighten est plafonné : chaque appel évité compte.

    Le 15 septembre 2026, le module a épuisé les 1 000 requêtes mensuelles du
    plan Watt en quelques heures — six appels par relevé dont quatre en
    double, aucune pause après échec, et un relevé déclenché à chaque
    affichage de page. Ces tests fixent les trois garde-fous.
    """

    def setUp(self):
        from .fonctions import cloud

        self.cloud = cloud
        for cle, valeur in (
            ("cloud_api_key", "cle"), ("cloud_client_id", "id"),
            ("cloud_client_secret", "secret"), ("cloud_system_id", "42"),
        ):
            set_setting(cle, valeur, module=api.MODULE)
        self._reset()

    def _reset(self):
        self.cloud._ECHEC.update({"jusqu_a": None, "message": ""})
        self.cloud._DERNIER_APPEL["a"] = None

    @staticmethod
    def _payloads():
        prod = {"intervals": [{"end_at": 1000, "wh_del": 250},
                              {"end_at": 2000, "wh_del": 500}]}
        cons = {"intervals": [{"end_at": 1000, "enwh": 500},
                              {"end_at": 2000, "enwh": 250}]}
        return [prod, cons]

    def test_un_releve_ne_coute_que_deux_appels(self):
        with mock.patch.object(self.cloud, "_get",
                               side_effect=self._payloads()) as get:
            jour = self.cloud.get_cloud_day()
        self.assertEqual(get.call_count, 2)
        self.assertEqual(jour["production_wh_today"], 750.0)
        self.assertEqual(jour["consumption_wh_today"], 750.0)
        self.assertEqual(jour["import_wh_today"], 250.0)
        self.assertEqual(jour["export_wh_today"], 250.0)
        self.assertEqual([p["kw"] for p in jour["production_curve"]], [1.0, 2.0])
        self.assertEqual([p["kw"] for p in jour["consumption_curve"]], [2.0, 1.0])

    def test_cumuls_et_courbes_partagent_le_meme_releve(self):
        """Trois lectures d'affilée, deux appels réseau en tout."""
        with mock.patch.object(self.cloud, "_get",
                               side_effect=self._payloads()) as get:
            totaux, _t, err = self.cloud.get_daily_totals_cached()
            prod, _p, _e1 = self.cloud.get_production_curve_cached()
            cons, _c, _e2 = self.cloud.get_consumption_curve_cached()
        self.assertEqual(get.call_count, 2)
        self.assertEqual(err, "")
        self.assertEqual(totaux["production_wh_today"], 750.0)
        self.assertEqual(len(prod), 2)
        self.assertEqual(len(cons), 2)

    def test_le_cache_tient_pendant_l_intervalle(self):
        with mock.patch.object(self.cloud, "_get", side_effect=self._payloads()):
            self.cloud.jour_cached()
        self._reset()  # le délai minimal ne doit pas masquer le vrai test
        with mock.patch.object(self.cloud, "_get",
                               side_effect=AssertionError("appel interdit")) as get:
            data, _ts, err = self.cloud.jour_cached()
        self.assertEqual(get.call_count, 0)
        self.assertEqual(err, "")
        self.assertEqual(data["production_wh_today"], 750.0)

    def test_apres_un_echec_on_ne_reessaie_pas_tout_de_suite(self):
        """Le vrai brûleur de quota : un échec n'écrit aucun cache."""
        with mock.patch.object(self.cloud, "_get", side_effect=RuntimeError("429")):
            _d, _t, err = self.cloud.jour_cached()
        self.assertIn("429", err)
        self.assertIsNotNone(self.cloud._ECHEC["jusqu_a"])

        self.cloud._DERNIER_APPEL["a"] = None  # seul le backoff doit retenir
        with mock.patch.object(self.cloud, "_get",
                               side_effect=AssertionError("appel interdit")) as get:
            self.cloud.jour_cached()
        self.assertEqual(get.call_count, 0)

    def test_deux_appels_reels_ne_peuvent_pas_se_suivre(self):
        """Même forcé, un clic répété sur « Actualiser » ne vide pas le quota."""
        with mock.patch.object(self.cloud, "_get", side_effect=self._payloads()):
            self.cloud.jour_cached(force=True)
        with mock.patch.object(self.cloud, "_get",
                               side_effect=AssertionError("appel interdit")) as get:
            self.cloud.jour_cached(force=True)
        self.assertEqual(get.call_count, 0)

    def test_un_429_suspend_longtemps_et_survit_au_redemarrage(self):
        """Le quota est mensuel : une pause d'un quart d'heure n'y suffit pas."""
        with mock.patch.object(self.cloud, "_get",
                               side_effect=self.cloud._QuotaDepasse("plan Watt")):
            _d, _t, err = self.cloud.jour_cached()
        self.assertIn("Quota mensuel", err)

        fin = self.cloud._pause_quota()
        self.assertIsNotNone(fin)  # en base, pas seulement en mémoire
        self.assertGreater(fin - datetime.now(),
                           timedelta(hours=self.cloud.BACKOFF_QUOTA_H - 1))

        self._reset()  # comme après un redémarrage du service
        with mock.patch.object(self.cloud, "_get",
                               side_effect=AssertionError("appel interdit")) as get:
            self.cloud.jour_cached()
        self.assertEqual(get.call_count, 0)

    def test_un_forcage_ne_contourne_pas_la_pause_quota(self):
        """Cliquer « Actualiser » ne doit pas creuser le trou du mois suivant."""
        set_setting(self.cloud.PAUSE_QUOTA,
                    (datetime.now() + timedelta(hours=6)).isoformat(),
                    module=api.MODULE)
        with mock.patch.object(self.cloud, "_get",
                               side_effect=AssertionError("appel interdit")) as get:
            _d, _t, err = self.cloud.jour_cached(force=True)
        self.assertEqual(get.call_count, 0)
        self.assertIn("Quota mensuel", err)

    def test_un_succes_leve_la_pause_quota(self):
        set_setting(self.cloud.PAUSE_QUOTA,
                    (datetime.now() - timedelta(minutes=1)).isoformat(),
                    module=api.MODULE)
        with mock.patch.object(self.cloud, "_get", side_effect=self._payloads()):
            _d, _t, err = self.cloud.jour_cached()
        self.assertEqual(err, "")
        self.assertIsNone(self.cloud._pause_quota())

    def test_l_intervalle_a_un_plancher(self):
        set_setting("cloud_intervalle_minutes", "1", module=api.MODULE)
        self.assertEqual(self.cloud.intervalle_minutes(),
                         self.cloud.INTERVALLE_PLANCHER_MIN)
        set_setting("cloud_intervalle_minutes", "240", module=api.MODULE)
        self.assertEqual(self.cloud.intervalle_minutes(), 240)


class CloudJeton(TestCase):
    """Un 401 doit renouveler le jeton, pas se répéter indéfiniment.

    Le 15 septembre 2026, la clé API a été régénérée : le jeton d'accès est
    devenu invalide bien avant la fin de son TTL. Le code ne renouvelant que
    sur l'âge, il a repassé le même jeton mort pendant des heures — quatre
    redémarrages du service n'y ont rien changé.
    """

    def setUp(self):
        from .fonctions import cloud

        self.cloud = cloud
        for cle, valeur in (
            ("cloud_api_key", "cle"), ("cloud_client_id", "id"),
            ("cloud_client_secret", "secret"), ("cloud_system_id", "42"),
        ):
            set_setting(cle, valeur, module=api.MODULE)

    def test_un_401_renouvelle_le_jeton_et_rejoue(self):
        appels = [self.cloud._NonAutorise("Not Authorized"), {"ok": 1}]
        with mock.patch.object(self.cloud, "_get_access_token", return_value="mort"), \
             mock.patch.object(self.cloud, "_refresh_tokens",
                               return_value={"access_token": "neuf"}) as refresh, \
             mock.patch.object(self.cloud, "_appel", side_effect=appels) as appel:
            resultat = self.cloud._get("/summary")
        self.assertEqual(resultat, {"ok": 1})
        self.assertEqual(refresh.call_count, 1)
        self.assertEqual(appel.call_count, 2)
        self.assertEqual(appel.call_args_list[1].args[2], "neuf")

    def test_un_401_apres_renouvellement_dit_quoi_faire(self):
        with mock.patch.object(self.cloud, "_get_access_token", return_value="mort"), \
             mock.patch.object(self.cloud, "_refresh_tokens",
                               return_value={"access_token": "neuf"}), \
             mock.patch.object(self.cloud, "_appel",
                               side_effect=self.cloud._NonAutorise("Not Authorized")):
            with self.assertRaises(RuntimeError) as ctx:
                self.cloud._get("/summary")
        self.assertIn("autorisation OAuth", str(ctx.exception))


class CloudSecrets(TestCase):
    """Rien de sensible ne doit atteindre le journal, lisible depuis le web."""

    def setUp(self):
        from .fonctions import cloud

        self.masquer = cloud._masquer

    def test_la_cle_api_est_masquee(self):
        url = "https://api.enphaseenergy.com/api/v4/systems/42/summary?key=abc123def"
        masque = self.masquer(url)
        self.assertNotIn("abc123def", masque)
        self.assertIn("key=***", masque)

    def test_le_refresh_token_et_le_code_sont_masques(self):
        texte = "POST /oauth/token?grant_type=refresh_token&refresh_token=rt-secret&code=c-secret"
        masque = self.masquer(texte)
        self.assertNotIn("rt-secret", masque)
        self.assertNotIn("c-secret", masque)

    def test_le_reste_du_message_est_conserve(self):
        masque = self.masquer("429 Too Many Requests sur .../summary?key=abc&granularity=day")
        self.assertIn("429 Too Many Requests", masque)
        self.assertIn("granularity=day", masque)


class CourbeReelle(TestCase):
    """La courbe « réel » ne doit pas attendre le relevé cloud suivant.

    Le 5 octobre 2026 à 11h26, la courbe bleue du module Solaire s'arrêtait
    vers 9h30 : dès que le compte cloud est lié, ``production_reelle`` ne
    rendait que la courbe du cloud, relevée toutes les deux heures pour tenir
    le quota. L'Envoy, lui, avait mesuré la production toutes les 5 minutes
    entre-temps — ces mesures restaient inutilisées.
    """

    def setUp(self):
        from .fonctions import cloud, historique, info

        self.cloud, self.historique, self.info = cloud, historique, info
        self.jour = date.today()

    def _a(self, heure, minute=0):
        return datetime(self.jour.year, self.jour.month, self.jour.day,
                        heure, minute).astimezone()

    def _local(self, points):
        """Historique de l'Envoy : {"HH:MM": kW produits}."""
        set_setting(
            self.historique.CLE,
            json.dumps({"jour": str(self.jour),
                        "points": {k: [v, 0.5, 0.0] for k, v in points.items()}}),
            module=api.MODULE,
        )

    def _cloud(self, fins):
        """Courbe cloud : {(heure, minute) de fin de pas: kW}."""
        return [{"end_at": self._a(h, m).timestamp(), "kw": kw}
                for (h, m), kw in sorted(fins.items())]

    def _lire(self, maintenant, courbe_cloud=None):
        with mock.patch.object(self.cloud, "cloud_configured",
                               return_value=courbe_cloud is not None), \
             mock.patch.object(self.cloud, "get_production_curve_cached",
                               return_value=(courbe_cloud, None, "")), \
             mock.patch.object(self.historique, "_maintenant",
                               return_value=maintenant):
            return self.info.production_reelle()

    def test_l_envoy_prolonge_la_courbe_du_cloud(self):
        # Cloud relevé à 9h50 : dernier pas clos à 9h45. Il est 11h26.
        cloud = self._cloud({(9, 30): 0.4, (9, 45): 0.6})
        self._local({
            "09:30": 0.5, "09:35": 0.6, "09:40": 0.7,   # déjà dans le cloud
            "09:45": 0.8, "09:50": 0.9, "09:55": 1.0,   # pas 9h45-10h00
            "10:00": 1.2, "10:05": 1.2, "10:10": 1.2,   # pas 10h00-10h15
            "11:00": 2.0, "11:05": 2.1, "11:10": 2.2,   # pas 11h00-11h15
            "11:15": 2.3, "11:20": 2.3, "11:25": 2.3,   # pas en cours
        })
        points = self._lire(self._a(11, 26), cloud)
        self.assertEqual(
            [(t.strftime("%H:%M:%S"), kw) for t, kw in points],
            [
                ("09:22:30", 0.4), ("09:37:30", 0.6),               # cloud
                ("09:52:30", 0.9), ("10:07:30", 1.2), ("11:07:30", 2.1),  # Envoy
            ],
        )

    def test_le_pas_en_cours_n_est_pas_rendu(self):
        cloud = self._cloud({(9, 45): 0.6})
        self._local({"11:15": 2.3, "11:20": 2.3, "11:25": 2.3})
        self.assertEqual(len(self._lire(self._a(11, 29), cloud)), 1)
        # À 11h30 pile, le pas 11h15-11h30 est clos : il entre dans la courbe.
        points = self._lire(self._a(11, 30), cloud)
        self.assertEqual(points[-1], (self._a(11, 22) + timedelta(seconds=30), 2.3))

    def test_le_pas_reste_de_15_minutes_de_bout_en_bout(self):
        # Le tableau horaire du module Solaire calcule les kWh en kW × pas,
        # le pas étant déduit des horodatages : il doit rester uniforme.
        cloud = self._cloud({(9, 15): 0.2, (9, 30): 0.4, (9, 45): 0.6})
        self._local({f"{h:02d}:{m:02d}": 1.0
                     for h in (9, 10) for m in range(0, 60, 5)})
        points = self._lire(self._a(11, 0), cloud)
        ecarts = {(b[0] - a[0]).total_seconds() for a, b in zip(points, points[1:])}
        self.assertEqual(ecarts, {900.0})
        self.assertEqual(len(points), 3 + 5)  # 9h45 → 11h00 : cinq pas de l'Envoy

    def test_un_cloud_a_jour_n_est_pas_modifie(self):
        cloud = self._cloud({(11, 0): 2.0, (11, 15): 2.2})
        self._local({"11:00": 9.0, "11:05": 9.0, "11:10": 9.0, "11:15": 9.0})
        points = self._lire(self._a(11, 20), cloud)
        self.assertEqual([kw for _t, kw in points], [2.0, 2.2])

    def test_sans_cloud_l_historique_local_sert_seul(self):
        self._local({"09:00": 0.3, "09:05": 0.4})
        points = self._lire(self._a(9, 7))
        self.assertEqual(points, [(self._a(9, 0), 0.3), (self._a(9, 5), 0.4)])

    def test_cloud_lie_mais_vide_repli_sur_l_historique_local(self):
        self._local({"09:00": 0.3, "09:05": 0.4})
        points = self._lire(self._a(9, 7), courbe_cloud=[])
        self.assertEqual([kw for _t, kw in points], [0.3, 0.4])
