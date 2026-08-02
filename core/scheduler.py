"""Scheduler de l'application (APScheduler).

Rôle : exécuter les tâches périodiques déclarées par les modules actifs
(actualisation des datas), et à l'étape 6 les scénarios horaires.

Contrat de module : dans le ``conf.py`` du module, déclarer

    TACHES = [
        # périodique (toutes les X minutes)
        {"nom": "actualiser", "fonction": "fonctions.api.tache_actualiser", "minutes": 30},
        # ou à heures fixes (API à quota strict : nombre d'appels/jour connu d'avance)
        {"nom": "previsions", "fonction": "fonctions.api.tache_actualiser",
         "heures": [3, 7, 11, 15, 17]},
    ]

- ``fonction`` : chemin relatif au répertoire du module
- ``minutes`` : périodicité, surchargeable en base via le réglage
  ``tache_<nom>_minutes`` du module (0 = tâche désactivée). Première
  exécution ~10 s après le démarrage.
- ``heures`` : liste d'heures fixes (minute 0), surchargeable via le réglage
  ``tache_<nom>_heures`` (« 3,7,11 », vide = tâche désactivée). Aucune
  exécution au démarrage : le nombre d'appels par jour reste exactement
  celui de la liste, ce qui est indispensable face à un quota journalier.

Seules les erreurs sont journalisées (pas les exécutions réussies).
"""

import importlib
from datetime import datetime, timedelta

from django.conf import settings

_scheduler = None
_start_error = None  # message d'erreur si le démarrage a échoué (diagnostic)

# Au-delà de ce nombre de minutes sans battement, on considère que
# l'application n'a pas tourné et on le journalise.
SEUIL_INTERRUPTION = 3

# Tolérance de rattrapage par défaut des déclencheurs horaires, en minutes.
RATTRAPAGE_DEFAUT = 10


def rattrapage_min(defaut=RATTRAPAGE_DEFAUT):
    """Tolérance de rattrapage des déclencheurs horaires, en minutes.

    Réglable via le réglage ``rattrapage_min`` du module « scenarios ».
    0 = pas de rattrapage (comportement strict : à la minute près).
    """
    from .services import get_setting

    try:
        return max(0, int(get_setting("rattrapage_min", module="scenarios", default=defaut)))
    except (TypeError, ValueError):
        return defaut


def _retard_minutes(heure, minute):
    """Minutes écoulées depuis l'heure prévue (0 si à l'heure ou en avance)."""
    now = datetime.now()
    retard = (now.hour * 60 + now.minute) - (heure * 60 + minute)
    if retard < -720:  # rattrapage survenu après minuit
        retard += 1440
    return max(0, retard)


def parse_horaires(valeur):
    """Analyse une liste d'horaires « 3, 7:30, 12:00 » -> [(3, 0), (7, 30), (12, 0)].

    Accepte une chaîne (réglage en base) ou une liste (défaut du conf.py),
    des heures seules ou des « HH:MM ». Les entrées invalides sont ignorées,
    le résultat est trié et dédoublonné.
    """
    if valeur is None:
        return []
    morceaux = valeur if isinstance(valeur, (list, tuple)) else str(valeur).split(",")

    horaires = set()
    for morceau in morceaux:
        texte = str(morceau).strip().replace("h", ":")
        if not texte:
            continue
        heure, _, minute = texte.partition(":")
        try:
            h, mi = int(heure), int(minute or 0)
        except ValueError:
            continue
        if 0 <= h <= 23 and 0 <= mi <= 59:
            horaires.add((h, mi))
    return sorted(horaires)


def horaire_texte(horaire):
    """(7, 30) ou « 7:30 » ou 7 -> « 07:30 » / « 07:00 »."""
    if isinstance(horaire, (list, tuple)):
        h, mi = horaire
    else:
        analyse = parse_horaires([horaire])
        if not analyse:
            return ""
        h, mi = analyse[0]
    return f"{h:02d}:{mi:02d}"


def horaires_texte(horaires):
    """Liste d'horaires -> « 03:00,07:00,11:00 » (format stocké en base)."""
    return ",".join(horaire_texte(h) for h in horaires)


def _wrap(module_name, tache_nom, dotted):
    """Fabrique le callable d'une tâche : import tardif + erreurs journalisées."""

    def run():
        from core.models import LogEntry
        from core.services import journal

        try:
            mod_path, func_name = dotted.rsplit(".", 1)
            func = getattr(
                importlib.import_module(f"modules.{module_name}.{mod_path}"),
                func_name,
            )
            func()
        except Exception as exc:
            journal(
                f"Tâche « {tache_nom} » en erreur : {exc}",
                module=module_name,
                level=LogEntry.ERROR,
            )

    return run


def start():
    """Démarre le scheduler, en mémorisant l'erreur en cas d'échec."""
    global _start_error
    try:
        sched = _start()
    except Exception as exc:
        _start_error = f"{type(exc).__name__} : {exc}"
        raise
    _start_error = None
    return sched


def _start():
    """Démarre le scheduler et enregistre les tâches des modules actifs."""
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    from apscheduler.schedulers.background import BackgroundScheduler

    from core.models import Module
    from core.services import get_setting, journal

    sched = BackgroundScheduler(timezone=settings.TIME_ZONE)
    count = 0

    for m in Module.objects.filter(enabled=True):
        try:
            conf = importlib.import_module(f"modules.{m.name}.conf")
        except ImportError:
            continue
        for t in getattr(conf, "TACHES", []):
            nom = t.get("nom", "tache")

            # Tâche à heures fixes : pas d'exécution au démarrage, le nombre
            # de passages par jour est exactement celui de la liste.
            if t.get("heures"):
                defaut = ",".join(horaire_texte(h) for h in t["heures"])
                horaires = parse_horaires(
                    get_setting(f"tache_{nom}_heures", module=m.name, default=defaut)
                )
                if not horaires:  # vide = désactivée
                    continue
                # Un job cron par minute distincte : « 3:00, 7:00, 12:30 »
                # devient un job à 0 min (3h, 7h) et un job à 30 min (12h).
                par_minute = {}
                for h, mi in horaires:
                    par_minute.setdefault(mi, []).append(h)
                for i, (mi, heures) in enumerate(sorted(par_minute.items())):
                    sched.add_job(
                        _wrap(m.name, nom, t["fonction"]),
                        "cron",
                        hour=",".join(str(h) for h in sorted(set(heures))),
                        minute=mi,
                        id=f"{m.name}.{nom}" + (f".{i + 1}" if i else ""),
                        max_instances=1,
                        coalesce=True,
                        # Rattrapage : une veille de la machine à l'heure
                        # prévue ne doit pas faire sauter l'actualisation.
                        # « coalesce » garantit un seul appel de rattrapage,
                        # le quota journalier de l'API est donc préservé.
                        misfire_grace_time=max(1, rattrapage_min() * 60),
                    )
                    count += 1
                continue

            try:
                minutes = int(
                    get_setting(
                        f"tache_{nom}_minutes",
                        module=m.name,
                        default=t.get("minutes", 60),
                    )
                )
            except (TypeError, ValueError):
                minutes = int(t.get("minutes", 60))
            if minutes <= 0:  # 0 = désactivée
                continue
            sched.add_job(
                _wrap(m.name, nom, t["fonction"]),
                "interval",
                minutes=minutes,
                id=f"{m.name}.{nom}",
                next_run_time=datetime.now() + timedelta(seconds=10),
                max_instances=1,
                coalesce=True,
            )
            count += 1

    # Tâches du socle : les seules qui ne viennent pas d'un module.
    sched.add_job(
        battement, "interval", minutes=1, id="core.battement",
        next_run_time=datetime.now() + timedelta(seconds=5),
        max_instances=1, coalesce=True,
    )
    count += 1

    # Purge du journal, une fois par nuit. À 3h17 plutôt qu'à 3h00 : les
    # heures rondes sont déjà chargées (actualisations des modules), et une
    # suppression n'a aucune raison de tomber au même moment.
    sched.add_job(
        purge_journal, "cron", hour=3, minute=17, id="core.purge_journal",
        max_instances=1, coalesce=True,
        # Serveur éteint à 3h17 : on purge au réveil plutôt que d'attendre le
        # lendemain. Une purge est idempotente, la rattraper ne coûte rien.
        misfire_grace_time=6 * 3600,
    )
    count += 1

    sched.start()
    _scheduler = sched
    nb_scenarios = refresh_scenarios()
    journal(
        f"Scheduler démarré : {count} tâche(s) périodique(s), "
        f"{nb_scenarios} scénario(s) horaire(s)"
    )
    return sched


def battement():
    """Marque que l'application est vivante, et repère les interruptions.

    Une minute sans battement signifie que le processus ne tournait pas :
    machine en veille, serveur arrêté, session fermée. Sans cette trace, un
    trou dans les courbes est indiscernable d'une panne de capteur — on en
    était réduit à déduire l'absence à partir de l'absence de données.

    Les interruptions de plus de ``SEUIL_INTERRUPTION`` minutes sont
    journalisées en avertissement, avec leur durée.
    """
    from datetime import datetime as dt

    from .models import LogEntry
    from .services import get_setting, journal, set_setting

    maintenant = dt.now()
    precedent = get_setting("battement", default="")
    if precedent:
        try:
            depuis = dt.fromisoformat(precedent)
            minutes = round((maintenant - depuis).total_seconds() / 60)
            if minutes >= SEUIL_INTERRUPTION:
                journal(
                    f"Interruption de {minutes} min : aucune activité entre "
                    f"{depuis:%d/%m %H:%M} et {maintenant:%H:%M}. "
                    f"Les mesures de cette période sont perdues.",
                    level=LogEntry.WARNING,
                )
        except ValueError:
            pass
    set_setting("battement", maintenant.isoformat())


def purge_journal():
    """Tâche de nuit : borne la taille du journal (voir ``services``)."""
    from .models import LogEntry
    from .services import journal, purger_journal

    try:
        purger_journal()
    except Exception as exc:
        journal(f"Purge du journal en erreur : {exc}", level=LogEntry.ERROR)


def dernier_battement():
    """Date du dernier battement, ou None."""
    from datetime import datetime as dt

    from .services import get_setting

    try:
        return dt.fromisoformat(get_setting("battement", default="") or "")
    except ValueError:
        return None


def refresh_scenarios():
    """(Re)enregistre les jobs cron des scénarios « à heure fixe ».

    Appelé au démarrage et après chaque création/modification/suppression
    de scénario, pour une prise en compte à chaud. Retourne le nombre de
    scénarios horaires enregistrés.
    """
    if _scheduler is None:
        return 0

    from .models import Scenario
    from .scenarios_engine import run_scenario

    for job in list(_scheduler.get_jobs()):
        if job.id.startswith("scenario."):
            _scheduler.remove_job(job.id)

    from .scenarios_engine import (
        heure_du_declencheur,
        in_time_window,
        libelle_source,
        run_scenario_async,
        valeur_du_declencheur,
    )

    def _make_heure_calculee(pk):
        """Vérifie chaque minute si l'heure calculée est atteinte.

        Un seul déclenchement par jour, même si la valeur reste sur la même
        heure ou si l'heure calculée change ensuite.

        Rattrapage : on déclenche aussi quand l'heure cible vient de passer
        (jusqu'à ``rattrapage_min`` minutes de retard, 10 par défaut,
        réglable via le réglage ``rattrapage_min`` du module « scenarios »).
        Sans cela, une minute manquée (serveur occupé, redémarrage, info
        indisponible pendant une minute) faisait perdre le déclenchement
        pour toute la journée.
        """

        def run():
            from datetime import date, datetime

            from .models import Scenario as S
            from .services import get_setting, set_setting

            try:
                scenario = S.objects.get(pk=pk, enabled=True)
            except S.DoesNotExist:
                return
            trigger = (scenario.definition or {}).get("trigger", {})
            cible = heure_du_declencheur(trigger)
            if not cible:
                return

            tolerance = rattrapage_min()

            now = datetime.now()
            h, m = cible.split(":")
            retard = (now.hour * 60 + now.minute) - (int(h) * 60 + int(m))
            if retard < 0 or retard > tolerance:
                return  # pas encore l'heure, ou trop tard pour rattraper

            cle = f"declenche_{pk}"
            if get_setting(cle, module="scenarios") == str(date.today()):
                return  # déjà déclenché aujourd'hui
            set_setting(cle, str(date.today()), module="scenarios")
            origin = f"heure calculée {cible}"
            if retard:
                origin += f" (rattrapage +{retard} min)"
            run_scenario_async(scenario, origin=origin)

        return run

    def _make_changement(pk):
        """Surveille une valeur et lance le scénario quand elle change.

        La dernière valeur vue est mémorisée en base (réglage
        ``valeur_<pk>`` du module « scenarios ») : la surveillance survit
        donc à un redémarrage. Trois précautions :

        - une lecture en erreur (None) ne déclenche rien et n'écrase pas la
          référence : une API momentanément injoignable ne doit pas passer
          pour un changement ;
        - la toute première lecture mémorise sans déclencher, sinon chaque
          démarrage du serveur lancerait le scénario ;
        - si ``vers`` est renseigné, seul le passage à cette valeur déclenche.
        """

        def run():
            from .models import Scenario as S
            from .services import get_setting, set_setting

            try:
                scenario = S.objects.get(pk=pk, enabled=True)
            except S.DoesNotExist:
                return
            trigger = (scenario.definition or {}).get("trigger", {})
            valeur = valeur_du_declencheur(trigger)
            if valeur is None:
                return  # source illisible : on ne conclut rien

            # La référence est mémorisée avec la source qui l'a produite :
            # si le scénario est modifié pour surveiller autre chose, on
            # repart d'une référence neuve au lieu de comparer des valeurs
            # qui n'ont rien à voir.
            import json as _json

            source_id = trigger.get("variable") or (
                f"{trigger.get('module', '')}.{trigger.get('fonction', '')}"
            )
            cle = f"valeur_{pk}"
            precedente = None
            brut = get_setting(cle, module="scenarios")
            if brut:
                try:
                    memo = _json.loads(brut)
                    if memo.get("source") == source_id:
                        precedente = memo.get("valeur")
                except (ValueError, TypeError):
                    precedente = None

            if precedente == valeur:
                return  # rien n'a changé
            set_setting(
                cle,
                _json.dumps({"source": source_id, "valeur": valeur}),
                module="scenarios",
            )
            if precedente is None:
                return  # première lecture (ou source changée) : référence seule

            vers = str(trigger.get("vers", "") or "").strip()
            if vers and valeur != vers:
                return  # changement, mais pas vers la valeur attendue

            source = libelle_source(trigger)
            affiche = lambda v: f"« {v} »" if v else "(vide)"  # noqa: E731
            run_scenario_async(
                scenario,
                origin=f"changement {source} : {affiche(precedente)} → {affiche(valeur)}",
            )

        return run

    def _make(pk, origin, quiet=False, debut="", fin="", cible=None):
        """Fabrique le callable d'un scénario.

        ``cible`` = (heure, minute) prévue, pour les déclencheurs à heure
        fixe : si l'exécution arrive en retard (rattrapage après une veille
        ou un redémarrage), l'origine journalisée le dit.
        """

        def run():
            from .models import Scenario as S

            if debut and fin and not in_time_window(debut, fin):
                return  # hors de la fenêtre horaire du déclencheur
            try:
                scenario = S.objects.get(pk=pk, enabled=True)
            except S.DoesNotExist:
                return
            texte = origin
            if cible:
                retard = _retard_minutes(*cible)
                if retard:
                    texte = f"{origin} (rattrapage +{retard} min)"
            # En thread : une boucle longue ne bloque pas le scheduler
            run_scenario_async(scenario, origin=texte, quiet=quiet)

        return run

    count = 0
    for s in Scenario.objects.filter(enabled=True):
        t = (s.definition or {}).get("trigger", {})

        if t.get("type") == "heure":
            try:
                heure, minute = str(t.get("heure", "")).split(":")
                heure, minute = int(heure), int(minute)
            except (ValueError, AttributeError):
                continue
            # Sans « misfire_grace_time », APScheduler abandonne en silence
            # un déclenchement manqué de plus d'une seconde : une mise en
            # veille de la machine à l'heure prévue faisait perdre le
            # scénario pour la journée. On accepte le même rattrapage que
            # les déclencheurs « heure calculée ».
            tolerance = rattrapage_min()
            _scheduler.add_job(
                _make(s.pk, "horaire", cible=(heure, minute)),
                "cron",
                hour=heure,
                minute=minute,
                id=f"scenario.{s.pk}",
                max_instances=1,
                coalesce=True,
                misfire_grace_time=max(1, tolerance * 60),
            )
            count += 1

        elif t.get("type") == "heure_calculee":
            _scheduler.add_job(
                _make_heure_calculee(s.pk),
                "cron",
                minute="*",
                id=f"scenario.{s.pk}",
                max_instances=1,
                coalesce=True,
            )
            count += 1

        elif t.get("type") == "changement":
            # Surveillance par sondage : l'intervalle est réglable car une
            # info de module peut interroger un appareil ou une API.
            try:
                minutes = max(1, int(t.get("minutes", 1)))
            except (TypeError, ValueError):
                minutes = 1
            _scheduler.add_job(
                _make_changement(s.pk),
                "interval",
                minutes=minutes,
                id=f"scenario.{s.pk}",
                next_run_time=datetime.now() + timedelta(seconds=15),
                max_instances=1,
                coalesce=True,
            )
            count += 1

        elif t.get("type") == "periodique":
            try:
                minutes = int(t.get("minutes", 0))
            except (TypeError, ValueError):
                continue
            if minutes < 1:
                continue
            _scheduler.add_job(
                _make(
                    s.pk,
                    "périodique",
                    quiet=True,
                    debut=t.get("debut") or "",
                    fin=t.get("fin") or "",
                ),
                "interval",
                minutes=minutes,
                id=f"scenario.{s.pk}",
                max_instances=1,
                coalesce=True,
            )
            count += 1
    return count


def jobs():
    """Liste des jobs enregistrés (pour affichage/diagnostic)."""
    return _scheduler.get_jobs() if _scheduler else []


def _prochaine_scenario(scenario, prochaine):
    """Texte de la colonne « prochaine exécution » pour un scénario.

    Les déclencheurs « heure calculée » et « changement » n'ont pas de job à
    l'heure du déclenchement : leur job cron n'est qu'une *vérification*
    (chaque minute, ou chaque N minutes). Afficher son prochain passage
    laissait croire qu'un scénario dont l'heure cible est 13:30 allait partir
    à 13:02 — c'est le contrôle qui passait à 13:02.
    """
    from datetime import date

    from .scenarios_engine import heure_du_declencheur
    from .services import get_setting

    horodatage = prochaine.strftime("%d/%m %H:%M:%S") if prochaine else "—"
    t = (scenario.definition or {}).get("trigger", {})
    ttype = t.get("type")

    if ttype == "heure_calculee":
        if get_setting(f"declenche_{scenario.pk}", module="scenarios") == str(date.today()):
            return "déjà déclenché aujourd'hui"
        try:
            cible = heure_du_declencheur(t)
        except Exception:  # source illisible : ne doit pas casser la page
            cible = None
        if not cible:
            return "heure indisponible — vérifié chaque minute"
        return f"aujourd'hui à {cible} — vérifié chaque minute"

    if ttype == "changement":
        try:
            minutes = max(1, int(t.get("minutes", 1)))
        except (TypeError, ValueError):
            minutes = 1
        cadence = "chaque minute" if minutes == 1 else f"toutes les {minutes} min"
        quand = prochaine.strftime("%H:%M:%S") if prochaine else "—"
        return f"au prochain changement — surveillé {cadence} (contrôle {quand})"

    return horodatage


def etat():
    """État du scheduler pour l'onglet Configuration (diagnostic).

    Les identifiants techniques (« scenario.4 », « enphase.actualiser ») ne
    disent rien : on les remplace par le nom du scénario ou le libellé du
    module, en gardant l'identifiant en second plan pour le dépannage.
    """
    from .models import Module, Scenario

    scenarios = {str(s.pk): s for s in Scenario.objects.all()}
    modules = {m.name: m for m in Module.objects.all()}

    lignes = []
    for job in jobs():
        prochaine = getattr(job, "next_run_time", None)
        prochaine_txt = prochaine.strftime("%d/%m %H:%M:%S") if prochaine else "—"
        if job.id.startswith("scenario."):
            pk = job.id.split(".", 1)[1]
            scenario = scenarios.get(pk)
            if scenario:
                nom, detail = scenario.name, scenario.trigger_summary
                prochaine_txt = _prochaine_scenario(scenario, prochaine)
            else:
                nom, detail = f"scénario n°{pk}", "supprimé ou désactivé"
            genre = "scénario"
        elif job.id == "core.battement":
            nom, genre = "Battement de l'application", "socle"
            detail = "marque que l'application tourne, et repère les interruptions"
        elif job.id == "core.purge_journal":
            from .services import journal_jours_conserves

            jours = journal_jours_conserves()
            nom, genre = "Purge du journal", "socle"
            detail = (
                f"supprime chaque nuit les entrées de plus de {jours} jours"
                if jours else "désactivée : le journal grossit sans limite"
            )
        else:
            module_nom, _, tache = job.id.partition(".")
            # « solcast.previsions.2 » : une tâche à horaires variés donne
            # plusieurs jobs cron, on n'affiche que le nom de la tâche.
            tache = tache.split(".")[0]
            module = modules.get(module_nom)
            nom = f"{module.label if module else module_nom} — {tache or 'tâche'}"
            detail = "actualisation des données du module"
            genre = "tâche"

        lignes.append({
            "id": job.id,
            "nom": nom,
            "detail": detail,
            "type": genre,
            "prochaine": prochaine_txt,
            # Le tri reste celui du prochain réveil réel du job : la table se
            # lit dans l'ordre où les choses vont se passer.
            "prochaine_tri": prochaine.isoformat() if prochaine else "9",
        })

    # Par ordre de passage : c'est ce qu'on veut lire dans un diagnostic.
    lignes.sort(key=lambda x: (x["prochaine_tri"], x["nom"]))
    valeur_rattrapage = rattrapage_min()
    return {
        "actif": _scheduler is not None and getattr(_scheduler, "running", False),
        "erreur": _start_error,
        "jobs": lignes,
        "battement": dernier_battement(),
        "rattrapage": valeur_rattrapage,
        # La valeur en base peut avoir été saisie ailleurs (admin) : on
        # l'ajoute aux choix, sinon le menu afficherait autre chose.
        "rattrapage_choix": sorted({0, 5, 10, 15, 30, 60, 120, valeur_rattrapage}),
    }
