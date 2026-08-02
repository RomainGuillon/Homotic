"""Moteur d'exécution des scénarios.

Un scénario = déclencheur → conditions (toutes doivent être vraies) →
actions (exécutées dans l'ordre, arrêt à la première erreur).

Déclencheurs gérés :
- « heure »  : job cron enregistré dans le scheduler (voir core/scheduler.py)
- « bouton » : appui sur un bouton poussoir du tableau de bord
- « switch » : bascule d'un switch du tableau de bord (état visé)
- « manuel » : bouton Tester, ou action « lancer un scénario » d'un autre scénario

Note : une action « régler un switch » ne redéclenche PAS les scénarios de ce
switch (pour éviter les boucles involontaires) — utiliser l'action « lancer un
scénario » pour chaîner explicitement.
"""

import importlib
import threading
import time as time_mod
from datetime import datetime

from django.utils import timezone

from .models import Control, LogEntry, Scenario, Variable
from .services import journal, set_control_state, set_variable

MODULE = "scenarios"
MAX_DEPTH = 3  # profondeur maxi de chaînage scénario -> scénario


# ----------------------------------------------------------------------
# Catalogue des fonctions exposées par les modules (contrat SCENARIO)
# ----------------------------------------------------------------------

def _catalogue(attr):
    """Entrées déclarées dans le conf.py des modules actifs (SCENARIO, INFOS)."""
    from .models import Module

    result = []
    for m in Module.objects.filter(enabled=True):
        try:
            conf = importlib.import_module(f"modules.{m.name}.conf")
            importlib.reload(conf)  # les listes dynamiques (prises...) évoluent
        except Exception:
            continue
        for f in getattr(conf, attr, []):
            result.append(
                {
                    "module": m.name,
                    "module_label": m.label,
                    "nom": f.get("nom", "?"),
                    "fonction": f.get("fonction", ""),
                    "description": f.get("description", ""),
                    "params": f.get("params", []),
                    # Type de donnée d'une info : « valeur » par défaut, donc
                    # les modules écrits avant les liaisons restent valides.
                    "type": f.get("type", "valeur"),
                    "unite": f.get("unite", ""),
                }
            )
    return result


def available_functions():
    """Fonctions d'ACTION des modules actifs (contrat SCENARIO)."""
    return _catalogue("SCENARIO")


def available_infos(types=("valeur",)):
    """Fonctions d'INFO (lecture) des modules actifs (contrat INFOS).

    Par défaut, seules les infos de type « valeur » : une condition
    « courbe > 5 » n'aurait aucun sens dans l'éditeur de scénarios. Les
    types structurés (serie, table, objet) n'existent que pour les liaisons
    entre modules — passer ``types=None`` pour tout obtenir.
    """
    infos = _catalogue("INFOS")
    if types is None:
        return infos
    return [i for i in infos if i.get("type", "valeur") in types]


def _call_module_function(module, dotted):
    mod_path, func_name = dotted.rsplit(".", 1)
    func = getattr(importlib.import_module(f"modules.{module}.{mod_path}"), func_name)
    return func()


def _compare(actuelle, op, attendue):
    """Compare deux valeurs (numérique si possible). Retourne (ok, erreur)."""
    try:
        a = float(str(actuelle).replace(",", "."))
        b = float(str(attendue).replace(",", "."))
    except (TypeError, ValueError):
        a, b = str(actuelle), str(attendue)
        if op in ("<", ">", "<=", ">="):
            return None, f"comparaison {op} impossible (non numérique)"

    ok = {
        "=": a == b,
        "!=": a != b,
        "<": a < b,
        ">": a > b,
        "<=": a <= b,
        ">=": a >= b,
    }.get(op)
    if ok is None:
        return None, f"opérateur inconnu « {op} »"
    return ok, ""


# ----------------------------------------------------------------------
# Conditions
# ----------------------------------------------------------------------

def valeur_du_declencheur(trigger):
    """Valeur courante lue par un déclencheur, en texte, ou None si illisible.

    La source est soit une info de module, soit une variable globale. Elle
    est relue à chaque vérification. On distingue « illisible » (None) de
    « vide » (chaîne vide) : une lecture en erreur ne doit pas passer pour un
    changement de valeur.
    """
    if trigger.get("source") == "info":
        try:
            valeur = _call_module_function(
                trigger.get("module", ""), trigger.get("fonction", "")
            )
        except Exception:
            return None
    else:
        try:
            valeur = Variable.objects.get(name=trigger.get("variable", "")).value
        except Variable.DoesNotExist:
            return None
    return "" if valeur is None else str(valeur).strip()


def libelle_source(trigger):
    """Nom lisible de la source d'un déclencheur (pour le journal)."""
    if trigger.get("source") == "info":
        return f"info « {trigger.get('nom') or trigger.get('fonction', '?')} »"
    return f"variable « {trigger.get('variable', '?')} »"


def heure_du_declencheur(trigger):
    """Heure « HH:MM » d'un déclencheur « heure calculée », ou None.

    La valeur vient d'une variable globale ou d'une info de module ; elle est
    relue à chaque vérification, donc l'heure peut changer dans la journée.
    """
    texte = valeur_du_declencheur(trigger)
    if texte is None:
        return None
    parts = texte.split(":")
    if len(parts) == 2 and all(p.isdigit() for p in parts):
        h, m = int(parts[0]), int(parts[1])
        if 0 <= h <= 23 and 0 <= m <= 59:
            return f"{h:02d}:{m:02d}"
    return None


def in_time_window(debut, fin):
    """Vrai si l'heure courante est dans [debut, fin) (minuit géré)."""
    now = datetime.now().strftime("%H:%M")
    if debut > fin:  # plage à cheval sur minuit
        return now >= debut or now < fin
    return debut <= now < fin


def _check_condition(cond):
    """Retourne (ok, explication si non remplie)."""
    ctype = cond.get("type")

    if ctype == "switch":
        name = cond.get("controle", "")
        try:
            control = Control.objects.get(name=name, type=Control.SWITCH)
        except Control.DoesNotExist:
            return False, f"switch « {name} » introuvable"
        attendu = cond.get("etat") == "on"
        if control.is_on != attendu:
            return False, f"switch « {name} » n'est pas {'ON' if attendu else 'OFF'}"
        return True, ""

    if ctype == "variable":
        name = cond.get("variable", "")
        try:
            actuelle = Variable.objects.get(name=name).value
        except Variable.DoesNotExist:
            return False, f"variable « {name} » introuvable"
        op = cond.get("operateur", "=")
        attendue = str(cond.get("valeur", ""))
        ok, err = _compare(actuelle, op, attendue)
        if ok is None:
            return False, f"variable « {name} » : {err}"
        if not ok:
            return False, f"variable « {name} » = « {actuelle} » (attendu {op} « {attendue} »)"
        return True, ""

    if ctype == "info":
        nom = cond.get("nom", "?")
        try:
            actuelle = _call_module_function(cond.get("module", ""), cond.get("fonction", ""))
        except Exception as exc:
            return False, f"info « {nom} » : erreur ({exc})"
        if actuelle is None:
            return False, f"info « {nom} » indisponible"
        op = cond.get("operateur", "=")
        attendue = str(cond.get("valeur", ""))
        ok, err = _compare(actuelle, op, attendue)
        if ok is None:
            return False, f"info « {nom} » : {err}"
        if not ok:
            return False, f"info « {nom} » = « {actuelle} » (attendu {op} « {attendue} »)"
        return True, ""

    if ctype == "plage":
        ok = in_time_window(cond.get("debut", "00:00"), cond.get("fin", "23:59"))
        if cond.get("mode") == "hors":
            ok = not ok
            if not ok:
                return False, f"dans la plage {cond.get('debut')}-{cond.get('fin')} (exclue)"
        elif not ok:
            return False, f"hors plage {cond.get('debut')}-{cond.get('fin')}"
        return True, ""

    return False, f"condition inconnue « {ctype} »"


def check_conditions(conditions):
    """Évalue une liste de conditions reliées par ET / OU.

    Chaque condition à partir de la deuxième porte un lien (``lien`` :
    « et » par défaut, ou « ou »). Le ET est prioritaire sur le OU, comme en
    algèbre booléenne : « A et B ou C et D » se lit « (A et B) ou (C et D) ».
    On découpe donc la liste en groupes à chaque « ou » ; un groupe est vrai
    si toutes ses conditions le sont, et l'ensemble est vrai si au moins un
    groupe l'est.

    Retourne (ok, explication du refus).
    """
    if not conditions:
        return True, ""

    groupes = [[]]
    for i, cond in enumerate(conditions):
        if i and str(cond.get("lien", "et")).lower() == "ou":
            groupes.append([])
        groupes[-1].append(cond)

    raisons = []
    for groupe in groupes:
        echec = ""
        for cond in groupe:
            ok, why = _check_condition(cond)
            if not ok:
                echec = why
                break
        if not echec:
            return True, ""  # ce groupe suffit
        raisons.append(echec)

    if len(raisons) == 1:
        return False, raisons[0]
    return False, "aucune branche remplie — " + " ; ".join(
        f"branche {i + 1} : {r}" for i, r in enumerate(raisons)
    )


# ----------------------------------------------------------------------
# Actions
# ----------------------------------------------------------------------

def _run_action(action, depth):
    atype = action.get("type")

    if atype == "fonction":
        module = action.get("module", "")
        dotted = action.get("fonction", "")
        mod_path, func_name = dotted.rsplit(".", 1)
        func = getattr(importlib.import_module(f"modules.{module}.{mod_path}"), func_name)
        # Paramètres optionnels déclarés par le module ("" = inchangé)
        params = {k: v for k, v in (action.get("params") or {}).items() if v not in ("", None)}
        func(**params) if params else func()
        detail = f"fonction {module}.{action.get('nom', func_name)}"
        if params:
            detail += " (" + ", ".join(f"{k}={v}" for k, v in params.items()) + ")"
        return detail

    if atype == "switch":
        name = action.get("controle", "")
        control = Control.objects.get(name=name, type=Control.SWITCH)
        # set_control_state applique le groupe exclusif (ex : Été/Hiver)
        eteints = set_control_state(control, action.get("etat") == "on")
        detail = f"switch « {name} » -> {'ON' if control.is_on else 'OFF'}"
        if eteints:
            detail += " (exclusif : " + ", ".join(c.label for c in eteints) + " -> OFF)"
        return detail

    if atype == "scenario":
        name = action.get("nom", "")
        target = Scenario.objects.get(name=name)
        run_scenario(target, origin="chaînage", depth=depth + 1)
        return f"scénario « {name} » lancé"

    if atype == "variable":
        name = action.get("variable", "")
        valeur = str(action.get("valeur", ""))
        set_variable(name, valeur)
        return f"variable « {name} » = « {valeur} »"

    if atype == "journal":
        journal(action.get("message", ""), module=MODULE)
        return "message journalisé"

    if atype == "si":
        ok, why = check_conditions(action.get("conditions", []))
        branch = action.get("alors", []) if ok else action.get("sinon", [])
        etat = "vrai" if ok else f"faux ({why})"
        if not branch:
            return f"si {etat} → rien à faire"
        details = [_run_action(a, depth) for a in branch]
        return f"si {etat} → " + " ; ".join(details)

    if atype == "info_var":
        value = _call_module_function(action.get("module", ""), action.get("fonction", ""))
        variable = action.get("variable", "")
        set_variable(variable, "" if value is None else str(value))
        return f"info {action.get('nom', '?')} → variable « {variable} » = « {value} »"

    if atype == "boucle":
        mode = action.get("mode", "tantque")  # tantque | jusqua
        try:
            interval_s = max(10, int(float(str(action.get("minutes", 5)).replace(",", ".")) * 60))
        except (TypeError, ValueError):
            interval_s = 300
        try:
            deadline = time_mod.time() + max(1, int(action.get("duree_max", 60))) * 60
        except (TypeError, ValueError):
            deadline = time_mod.time() + 3600

        sortie = action.get("sortie", [])
        iterations = 0
        stop = "condition"
        while True:
            ok, _why = check_conditions(action.get("conditions", []))
            keep = ok if mode == "tantque" else not ok
            if not keep:
                break
            for a in action.get("actions", []):
                _run_action(a, depth)
            iterations += 1
            # Sortie anticipée : vérifiée après chaque itération
            if sortie:
                ok_s, _ = check_conditions(sortie)
                if ok_s:
                    stop = "sortie anticipée"
                    break
            if time_mod.time() + interval_s > deadline:
                stop = "durée max atteinte"
                break
            time_mod.sleep(interval_s)
        libelle = "tant que" if mode == "tantque" else "jusqu'à ce que"
        # Sans action répétée, la boucle est une attente : parler
        # d'« itérations » n'aurait aucun sens dans le journal.
        unite = "itération" if action.get("actions") else "vérification"
        return f"boucle {libelle} terminée ({iterations} {unite}(s), arrêt : {stop})"

    raise ValueError(f"action inconnue « {atype} »")


# ----------------------------------------------------------------------
# Exécution
# ----------------------------------------------------------------------

def run_scenario(scenario, origin="manuel", depth=0, quiet=False):
    """Exécute un scénario (conditions puis actions). Retourne True si OK.

    ``quiet`` : ne journalise pas un échec de conditions (utilisé par les
    déclencheurs périodiques pour ne pas inonder le Journal).
    """
    if depth > MAX_DEPTH:
        journal(
            f"Scénario « {scenario.name} » : chaînage trop profond, abandon",
            module=MODULE,
            level=LogEntry.ERROR,
        )
        return False

    defn = scenario.definition or {}
    ok, why = check_conditions(defn.get("conditions", []))
    if not ok:
        scenario.last_run = timezone.now()
        scenario.last_status = f"non exécuté : {why}"
        scenario.save(update_fields=["last_run", "last_status"])
        if not quiet:
            journal(f"Scénario « {scenario.name} » non exécuté : {why}", module=MODULE)
        return False

    for i, action in enumerate(defn.get("actions", []), start=1):
        try:
            detail = _run_action(action, depth)
            journal(f"Scénario « {scenario.name} » ({origin}) : {detail}", module=MODULE)
        except Exception as exc:
            scenario.last_run = timezone.now()
            scenario.last_status = f"erreur action {i} : {exc}"
            scenario.save(update_fields=["last_run", "last_status"])
            journal(
                f"Scénario « {scenario.name} » : erreur action {i} : {exc}",
                module=MODULE,
                level=LogEntry.ERROR,
            )
            return False

    scenario.last_run = timezone.now()
    scenario.last_status = f"OK ({origin})"
    scenario.save(update_fields=["last_run", "last_status"])
    return True


def run_scenario_async(scenario, origin="manuel", quiet=False):
    """Exécution en arrière-plan (les actions et les boucles peuvent durer)."""
    threading.Thread(
        target=run_scenario,
        args=(scenario,),
        kwargs={"origin": origin, "quiet": quiet},
        daemon=True,
    ).start()


def on_control_event(control):
    """Appelé quand un contrôle du tableau de bord est actionné.

    Bouton : lance les scénarios déclenchés par ce bouton.
    Switch : lance les scénarios déclenchés par ce switch dans son nouvel état.
    Retourne le nombre de scénarios lancés.
    """
    launched = 0
    for s in Scenario.objects.filter(enabled=True):
        t = (s.definition or {}).get("trigger", {})
        if control.type == Control.BUTTON:
            match = t.get("type") == "bouton" and t.get("controle") == control.name
            origin = f"bouton « {control.label} »"
        else:
            etat = "on" if control.is_on else "off"
            match = (
                t.get("type") == "switch"
                and t.get("controle") == control.name
                and t.get("etat") == etat
            )
            origin = f"switch « {control.label} » {etat}"
        if match:
            run_scenario_async(s, origin=origin)
            launched += 1
    return launched
