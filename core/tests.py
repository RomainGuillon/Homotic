"""Tests du socle.

Le plus important est ``IndependanceDesModules`` : sans lui, la règle « un
module ne connaît que le socle » se reperdra au premier dépannage pressé.
Un import direct est toujours plus rapide à écrire qu'un besoin déclaré —
c'est justement pour ça qu'il faut une barrière automatique.
"""

import ast
from pathlib import Path

from django.conf import settings
from django.test import TestCase


def _modules_dir():
    return Path(settings.MODULES_DIR)


def connecte(client, identifiant="testeur", mot_de_passe="mot-de-passe-de-test"):
    """Crée un compte et ouvre une session : l'application est fermée par
    défaut depuis l'ajout de ``LoginRequiredMiddleware``."""
    from django.contrib.auth.models import User

    User.objects.create_user(username=identifiant, password=mot_de_passe)
    client.login(username=identifiant, password=mot_de_passe)


def _imports_de_modules(fichier):
    """Noms de modules importés par un fichier : ``modules.<nom>...``."""
    try:
        arbre = ast.parse(fichier.read_text(encoding="utf-8"), filename=str(fichier))
    except SyntaxError:  # signalé ailleurs, pas le sujet de ce test
        return set()

    cibles = set()
    for noeud in ast.walk(arbre):
        if isinstance(noeud, ast.Import):
            for alias in noeud.names:
                cibles.add(alias.name)
        elif isinstance(noeud, ast.ImportFrom) and noeud.module and noeud.level == 0:
            cibles.add(noeud.module)

    return {
        c.split(".")[1]
        for c in cibles
        if c.startswith("modules.") and len(c.split(".")) > 1
    }


class IndependanceDesModules(TestCase):
    """Aucun module ne doit en importer un autre.

    Les échanges passent par les liaisons (BESOINS + INFOS typées), voir
    ``docs/09-liaisons-entre-modules.md``. Un import direct recrée un
    couplage au nom du module, interdit de désactiver le fournisseur, et
    peut refermer un cycle d'imports.
    """

    def test_aucun_import_croise(self):
        fautes = []
        base = _modules_dir()
        for fichier in sorted(base.rglob("*.py")):
            if "__pycache__" in fichier.parts:
                continue
            proprietaire = fichier.relative_to(base).parts[0]
            for cible in _imports_de_modules(fichier):
                if cible != proprietaire:
                    fautes.append(
                        f"{fichier.relative_to(base)} importe « {cible} » — "
                        f"passer par un BESOIN déclaré dans conf.py"
                    )

        self.assertEqual(
            fautes,
            [],
            "Import croisé entre modules :\n  " + "\n  ".join(fautes),
        )


class ApplicationSansModules(TestCase):
    """L'application doit tenir debout sans aucun module, ou avec un module
    disparu du disque alors qu'il est encore activé en base.

    C'est le vrai test d'indépendance : « certaines fonctions ne marchent
    plus, mais rien ne plante et rien n'affiche de trace Python ».
    """

    PAGES = ["/", "/journal/", "/configuration/"]

    def setUp(self):
        """Recrée en base les modules réellement présents sur le disque.

        Sans ça, la base de test est vide de modules et les scénarios de
        panne ne prouveraient rien : on testerait un socle sans rien à
        casser. Les modules sont activés mais non configurés (aucune clé
        d'API dans la base de test) — c'est justement l'état le plus
        exposé.
        """
        from core.models import Module
        from core.modules_registry import scan_modules

        # Toutes les pages exigent une session (LoginRequiredMiddleware) :
        # sans ce compte, ces tests ne verifieraient plus que la redirection
        # vers le formulaire de connexion.
        connecte(self.client)

        for info in scan_modules():
            Module.objects.update_or_create(
                name=info["name"],
                defaults={
                    "label": info["onglet"],
                    "icon": info["icone"],
                    "description": info["description"],
                    "enabled": True,
                },
            )

    def _pages_ok(self, contexte):
        for url in self.PAGES:
            reponse = self.client.get(url)
            self.assertEqual(
                reponse.status_code, 200, f"{url} casse {contexte}"
            )

    def test_aucun_module_actif(self):
        from core.models import Module

        Module.objects.update(enabled=False)
        self._pages_ok("sans aucun module actif")

    def test_module_absent_du_disque(self):
        """Répertoire supprimé, ligne encore activée en base : le cas qui
        casse tout si le socle importe sans filet."""
        from core.models import LogEntry, Module

        Module.objects.create(
            name="fantome", label="Module fantôme", enabled=True
        )
        self._pages_ok("avec un module absent du disque")

        # Son onglet doit répondre une page, pas une erreur serveur
        self.assertEqual(self.client.get("/module/fantome/").status_code, 200)
        self.assertFalse(
            LogEntry.objects.filter(message__icontains="Traceback").exists()
        )

    def test_chaque_module_desactive_a_son_tour(self):
        from core.models import Module

        noms = list(Module.objects.values_list("name", flat=True))
        for nom in noms:
            with self.subTest(module=nom):
                Module.objects.filter(name=nom).update(enabled=False)
                self._pages_ok(f"sans le module « {nom} »")
                Module.objects.filter(name=nom).update(enabled=True)

    def test_onglet_de_module_en_erreur(self):
        """Un onglet qui explose est isolé : page d'erreur, pas de 500."""
        from unittest.mock import patch

        from core.models import LogEntry, Module

        nom = Module.objects.filter(enabled=True).values_list("name", flat=True).first()
        if not nom:
            self.skipTest("aucun module sur le disque")

        cible = f"modules.{nom}.onglet.views.onglet"
        with patch(cible, side_effect=RuntimeError("API injoignable")):
            reponse = self.client.get(f"/module/{nom}/")

        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, "API injoignable")
        self.assertTrue(
            LogEntry.objects.filter(module=nom, level=LogEntry.ERROR).exists()
        )

    def test_scenario_vers_un_module_absent(self):
        """Un scénario qui appelle un module disparu échoue proprement."""
        from core.models import Scenario
        from core.scenarios_engine import run_scenario

        scenario = Scenario.objects.create(
            name="test module absent",
            enabled=True,
            definition={
                "trigger": {"type": "manuel"},
                "conditions": [],
                "actions": [{
                    "type": "fonction", "module": "fantome",
                    "fonction": "fonctions.scenario.faire", "nom": "faire",
                }],
            },
        )
        self.assertFalse(run_scenario(scenario))  # pas d'exception
        scenario.refresh_from_db()
        self.assertIn("erreur", scenario.last_status)

    def test_besoin_dont_le_fournisseur_est_desactive(self):
        """Une liaison vers un module éteint donne une raison, pas un plantage."""
        from core.liaisons import lire_besoin, set_liaison
        from core.models import Module

        Module.objects.update_or_create(
            name="enphase", defaults={"label": "Énergie", "enabled": True}
        )
        Module.objects.update_or_create(
            name="tempo", defaults={"label": "Tempo", "enabled": False}
        )
        set_liaison("enphase", "tarifs_jour", "tempo.tarifs_jour")

        valeur, erreur = lire_besoin("enphase", "tarifs_jour")
        self.assertIsNone(valeur)
        self.assertIn("tempo", erreur)


class LiaisonsDeclarees(TestCase):
    """Tout besoin déclaré doit pouvoir être branché sur quelque chose."""

    def test_type_de_besoin_connu(self):
        from core.liaisons import TYPES, besoins_du_module

        base = _modules_dir()
        for d in sorted(p for p in base.iterdir() if p.is_dir()):
            for besoin in besoins_du_module(d.name):
                self.assertIn(
                    besoin["type"],
                    TYPES,
                    f"{d.name} : type « {besoin['type']} » inconnu "
                    f"pour le besoin « {besoin['nom']} »",
                )


class PurgeDuJournal(TestCase):
    """Le journal doit rester borné : il n'a longtemps eu aucune limite."""

    def _vieillir(self, entree, jours):
        """``created_at`` est en ``auto_now_add`` : on force la date en base."""
        from datetime import timedelta

        from django.utils import timezone

        from core.models import LogEntry

        LogEntry.objects.filter(pk=entree.pk).update(
            created_at=timezone.now() - timedelta(days=jours)
        )

    def test_purge_les_vieilles_entrees_et_garde_les_recentes(self):
        from core.models import LogEntry
        from core.services import journal, purger_journal

        vieille = journal("entrée ancienne")
        recente = journal("entrée récente")
        self._vieillir(vieille, 120)

        purger_journal(jours=90)

        self.assertFalse(LogEntry.objects.filter(pk=vieille.pk).exists())
        self.assertTrue(LogEntry.objects.filter(pk=recente.pk).exists())

    def test_retention_a_zero_ne_supprime_rien(self):
        """0 = purge désactivée, et surtout pas « tout effacer »."""
        from core.models import LogEntry
        from core.services import journal, purger_journal

        vieille = journal("entrée ancienne")
        self._vieillir(vieille, 5000)

        self.assertEqual(purger_journal(jours=0), 0)
        self.assertTrue(LogEntry.objects.filter(pk=vieille.pk).exists())

    def test_reglage_lu_depuis_la_base(self):
        from core.services import journal_jours_conserves, set_setting

        set_setting("journal_jours_conserves", "30")
        self.assertEqual(journal_jours_conserves(), 30)

        set_setting("journal_jours_conserves", "n'importe quoi")
        self.assertEqual(journal_jours_conserves(), 90)  # défaut si illisible

    def test_page_journal_regle_la_conservation(self):
        from core.models import LogEntry
        from core.services import journal, journal_jours_conserves

        connecte(self.client)
        vieille = journal("entrée ancienne")
        self._vieillir(vieille, 60)

        reponse = self.client.post("/journal/conservation/", {"jours": "30"})

        self.assertEqual(reponse.status_code, 302)
        self.assertEqual(journal_jours_conserves(), 30)
        self.assertFalse(LogEntry.objects.filter(pk=vieille.pk).exists())


class PurgeManuelleDuJournal(TestCase):
    """Le bouton de purge de l'onglet Journal.

    Une purge est irréversible : ce qui compte ici est moins qu'elle
    supprime que qu'elle ne supprime *que* ce qui était visé. Un module en
    boucle d'erreur doit pouvoir être nettoyé sans emporter l'historique du
    reste.
    """

    def setUp(self):
        connecte(self.client)

    def _entrees(self):
        from core.models import LogEntry
        from core.services import journal

        return {
            "err": journal("panne", module="enphase", level=LogEntry.ERROR),
            "info": journal("relevé", module="enphase"),
            "autre": journal("bascule", module="tuya"),
        }

    def test_purge_de_la_selection(self):
        from core.models import LogEntry

        e = self._entrees()
        self.client.post("/journal/purger/",
                         {"portee": "selection", "ids": [e["err"].pk, e["autre"].pk]})

        restants = set(LogEntry.objects.values_list("pk", flat=True))
        self.assertEqual(restants, {e["info"].pk})

    def test_purge_du_filtre_courant(self):
        from core.models import LogEntry

        e = self._entrees()
        self.client.post("/journal/purger/",
                         {"portee": "filtre", "module": "enphase", "level": "ERROR"})

        restants = set(LogEntry.objects.values_list("pk", flat=True))
        self.assertEqual(restants, {e["info"].pk, e["autre"].pk})

    def test_purge_totale(self):
        from core.models import LogEntry

        self._entrees()
        self.client.post("/journal/purger/", {"portee": "tout"})

        self.assertEqual(LogEntry.objects.count(), 0,
                         "un journal vidé doit rester vide, sans ligne de compte-rendu")

    def test_selection_vide_ne_supprime_rien(self):
        """Le bouton est désactivé côté navigateur, mais la vue ne doit pas
        interpréter « aucune ligne » comme « toutes les lignes »."""
        from core.models import LogEntry

        self._entrees()
        self.client.post("/journal/purger/", {"portee": "selection"})

        self.assertEqual(LogEntry.objects.count(), 3)

    def test_portee_inconnue_ne_supprime_rien(self):
        from core.models import LogEntry

        self._entrees()
        self.client.post("/journal/purger/", {"portee": "n'importe quoi"})

        self.assertEqual(LogEntry.objects.count(), 3)

    def test_module_purge_reste_selectionnable(self):
        """Le piège de la purge : le module disparaissait du filtre au moment
        précis où l'on voulait vérifier s'il réécrivait."""
        from core.models import LogEntry, Module

        Module.objects.create(name="enphase", label="Énergie", enabled=True)
        LogEntry.objects.all().delete()

        reponse = self.client.get("/journal/")

        noms = [m["nom"] for m in reponse.context["modules"]]
        self.assertIn("enphase", noms)
        self.assertContains(reponse, "enphase (Énergie)")

    def test_purge_refusee_en_get(self):
        from core.models import LogEntry

        self._entrees()
        self.assertEqual(self.client.get("/journal/purger/").status_code, 405)
        self.assertEqual(LogEntry.objects.count(), 3)


class AccesProtege(TestCase):
    """L'application est joignable depuis Internet : rien ne doit répondre
    sans session, et l'oubli d'un décorateur ne doit pas ouvrir une brèche.

    C'est le test qui compte le plus de ce fichier : une régression ici ne
    casse rien de visible, elle rend seulement le chauffe-eau pilotable par
    n'importe qui.
    """

    PUBLIQUES = ["/comptes/login/"]

    def test_visiteur_anonyme_redirige_vers_le_login(self):
        for url in ["/", "/journal/", "/configuration/"]:
            with self.subTest(url=url):
                reponse = self.client.get(url)
                self.assertEqual(reponse.status_code, 302, f"{url} répond sans session")
                self.assertIn("/comptes/login/", reponse["Location"])

    def test_page_de_login_accessible_sans_session(self):
        for url in self.PUBLIQUES:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_acces_une_fois_connecte(self):
        connecte(self.client)
        for url in ["/", "/journal/", "/configuration/"]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_action_en_ecriture_refusee_sans_session(self):
        """Une redirection sur un GET ne prouve rien : ce sont les POST qui
        agissent sur le logement."""
        from core.models import Control
        from core.services import set_control_state

        control = Control.objects.create(
            type=Control.SWITCH, name="chauffe", label="Chauffe-eau"
        )
        set_control_state(control, False)

        reponse = self.client.post(f"/controle/{control.pk}/action/")

        self.assertEqual(reponse.status_code, 302)
        self.assertIn("/comptes/login/", reponse["Location"])
        control.refresh_from_db()
        self.assertFalse(control.is_on, "un anonyme a pu basculer un switch")

    def test_purge_du_journal_refusee_sans_session(self):
        """Effacer les traces est exactement ce que ferait un intrus."""
        from core.models import LogEntry
        from core.services import journal

        journal("trace à conserver")

        reponse = self.client.post("/journal/purger/", {"portee": "tout"})

        self.assertEqual(reponse.status_code, 302)
        self.assertIn("/comptes/login/", reponse["Location"])
        self.assertEqual(LogEntry.objects.count(), 1, "un anonyme a pu vider le journal")

    def test_l_application_reste_fermee_par_defaut(self):
        """La garantie tient au middleware, pas à un décorateur par vue :
        c'est lui qu'on surveille, car il couvre aussi les routes qui
        n'existent pas encore."""
        self.assertIn(
            "django.contrib.auth.middleware.LoginRequiredMiddleware",
            settings.MIDDLEWARE,
            "l'application n'est plus fermée par défaut",
        )

    def test_onglet_de_module_ferme_aussi(self):
        """Les onglets des modules sont des vues comme les autres — mais
        elles sont générées dynamiquement, donc faciles à oublier."""
        reponse = self.client.get("/module/chauffe_eau/")
        self.assertEqual(reponse.status_code, 302)
        self.assertIn("/comptes/login/", reponse["Location"])


class ParenthesageDesConditions(TestCase):
    """« A ET B ET (C OU D) » doit rester faux si A ou B est faux.

    Sans groupe, la liste plate se lit « (A ET B ET C) OU D » : D vrai à lui
    seul déclenchait le scénario, en ignorant tout le reste. C'est le piège
    que ces tests verrouillent — il ne se voit pas à la lecture de l'éditeur,
    seulement le jour où le scénario part au mauvais moment.
    """

    def _switch(self, nom, allume):
        from core.models import Control

        Control.objects.create(name=nom, type=Control.SWITCH, is_on=allume)
        return {"type": "switch", "controle": nom, "etat": "on"}

    def _conditions(self, a, b, c, d):
        """A ET B ET (C OU D)."""
        cond_a = self._switch("a", a)
        cond_b = self._switch("b", b)
        cond_c = self._switch("c", c)
        cond_d = self._switch("d", d)
        cond_b["lien"] = "et"
        return [
            cond_a,
            cond_b,
            {
                "type": "groupe",
                "lien": "et",
                "conditions": [cond_c, dict(cond_d, lien="ou")],
            },
        ]

    def test_table_de_verite(self):
        from core.scenarios_engine import check_conditions

        cas = {
            # (A, B, C, D): résultat attendu
            (True, True, True, False): True,
            (True, True, False, True): True,
            (True, True, True, True): True,
            (True, True, False, False): False,
            (False, True, True, True): False,   # le piège : D vrai ne suffit pas
            (True, False, False, True): False,
        }
        for (a, b, c, d), attendu in cas.items():
            with self.subTest(a=a, b=b, c=c, d=d):
                from core.models import Control

                Control.objects.all().delete()
                ok, _ = check_conditions(self._conditions(a, b, c, d))
                self.assertEqual(ok, attendu)

    def test_groupe_vide_est_neutre(self):
        """Un groupe qu'on vient d'ajouter ne doit pas bloquer le scénario."""
        from core.scenarios_engine import check_conditions

        ok, _ = check_conditions([{"type": "groupe", "conditions": []}])
        self.assertTrue(ok)
