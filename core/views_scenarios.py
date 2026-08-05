"""Vues de l'éditeur de scénarios (onglet Configuration)."""

import json

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from . import scheduler
from .models import Control, Scenario, Variable
from .scenarios_engine import available_functions, available_infos, run_scenario_async
from .services import journal

TRIGGER_TYPES = (
    "heure", "heure_calculee", "changement", "periodique", "bouton", "switch", "manuel",
)
CONDITION_TYPES = ("switch", "plage", "variable", "info")
ACTION_TYPES = ("fonction", "switch", "scenario", "journal", "variable", "info_var")


MAX_BLOC_DEPTH = 3  # niveaux d'imbrication Si/Boucle autorisés


def _validate_actions(actions, depth=0):
    """Valide une liste d'actions (blocs Si/Boucle imbricables sur 3 niveaux)."""
    if not isinstance(actions, list):
        return "Actions illisibles."
    for a in actions:
        atype = a.get("type")
        if atype == "si":
            if depth >= MAX_BLOC_DEPTH:
                return f"Imbrication trop profonde (max {MAX_BLOC_DEPTH} niveaux de blocs)."
            if _validate_conditions(a.get("conditions", [])):
                return "Condition invalide dans un bloc Si."
            if not a.get("conditions"):
                return "Un bloc Si doit avoir au moins une condition."
            if not a.get("alors") and not a.get("sinon"):
                return "Un bloc Si doit avoir au moins une action dans Alors ou Sinon."
            for branch in ("alors", "sinon"):
                err = _validate_actions(a.get(branch, []), depth=depth + 1)
                if err:
                    return err
        elif atype == "boucle":
            if depth >= MAX_BLOC_DEPTH:
                return f"Imbrication trop profonde (max {MAX_BLOC_DEPTH} niveaux de blocs)."
            if a.get("mode") not in ("tantque", "jusqua"):
                return "Boucle : mode invalide."
            if _validate_conditions(a.get("conditions", [])) or _validate_conditions(
                a.get("sortie", [])
            ):
                return "Condition invalide dans une boucle."
            if not a.get("conditions"):
                return "Une boucle doit avoir au moins une condition (sinon elle ne s'arrête jamais)."
            # Une boucle sans action répétée est légitime : c'est une attente
            # (« chauffer, puis attendre que le ballon soit plein »). La
            # condition d'arrêt et la durée max garantissent la terminaison.
            try:
                if float(str(a.get("minutes", 0)).replace(",", ".")) <= 0:
                    return "Boucle : intervalle en minutes invalide."
                if int(a.get("duree_max", 0)) < 1:
                    return "Boucle : durée max invalide (≥ 1 min)."
            except (TypeError, ValueError):
                return "Boucle : intervalle ou durée max invalide."
            err = _validate_actions(a.get("actions", []), depth=depth + 1)
            if err:
                return err
        elif atype not in ACTION_TYPES:
            return "Action invalide."
    return None


def _validate_conditions(conditions, dans_groupe=False):
    """Vérifie les types et normalise le lien logique (et / ou).

    ``dans_groupe`` interdit le groupe dans un groupe : un seul niveau de
    parenthèses, comme le propose l'éditeur. Au-delà, l'expression devient
    illisible pour un gain rare.
    """
    if not isinstance(conditions, list):
        return "Conditions illisibles."
    for i, cond in enumerate(conditions):
        ctype = cond.get("type")
        if ctype == "groupe":
            if dans_groupe:
                return "Un groupe ne peut pas en contenir un autre."
            sous = cond.get("conditions") or []
            if not sous:
                # Le moteur traite un groupe vide comme neutre, mais
                # l'enregistrer serait presque sûrement un oubli.
                return "Un groupe doit contenir au moins une condition."
            err = _validate_conditions(sous, dans_groupe=True)
            if err:
                return err
        elif ctype not in CONDITION_TYPES:
            return "Condition invalide."
        lien = str(cond.get("lien", "et")).lower()
        if i == 0:
            cond.pop("lien", None)  # rien avant la première condition
        else:
            cond["lien"] = "ou" if lien == "ou" else "et"
    return None


def _validate_definition(raw):
    """Valide le JSON de l'éditeur. Retourne (definition, erreur)."""
    try:
        defn = json.loads(raw)
    except (TypeError, ValueError):
        return None, "Définition illisible."

    trigger = defn.get("trigger", {})
    if trigger.get("type") not in TRIGGER_TYPES:
        return None, "Déclencheur invalide."
    if trigger.get("type") == "heure":
        heure = str(trigger.get("heure", ""))
        parts = heure.split(":")
        if len(parts) != 2 or not all(p.isdigit() for p in parts) \
                or not (0 <= int(parts[0]) <= 23 and 0 <= int(parts[1]) <= 59):
            return None, "Heure du déclencheur invalide (format HH:MM)."
    if trigger.get("type") in ("bouton", "switch") and not trigger.get("controle"):
        return None, "Déclencheur : choisir un bouton ou un switch."
    if trigger.get("type") == "heure_calculee":
        if trigger.get("source") == "info":
            if not (trigger.get("module") and trigger.get("fonction")):
                return None, "Déclencheur : choisir l'info qui donne l'heure."
        elif not trigger.get("variable"):
            return None, "Déclencheur : choisir la variable qui donne l'heure."
    if trigger.get("type") == "changement":
        if trigger.get("source") == "info":
            if not (trigger.get("module") and trigger.get("fonction")):
                return None, "Déclencheur : choisir l'info à surveiller."
        elif not trigger.get("variable"):
            return None, "Déclencheur : choisir la variable à surveiller."
        try:
            if int(trigger.get("minutes", 1)) < 1:
                return None, "Déclencheur au changement : vérification ≥ 1 min."
        except (TypeError, ValueError):
            return None, "Déclencheur au changement : intervalle invalide."
    if trigger.get("type") == "periodique":
        try:
            if int(trigger.get("minutes", 0)) < 1:
                return None, "Déclencheur périodique : minutes ≥ 1."
        except (TypeError, ValueError):
            return None, "Déclencheur périodique : minutes invalides."
        if bool(trigger.get("debut")) != bool(trigger.get("fin")):
            return None, "Déclencheur périodique : renseigner début ET fin (ou aucun)."

    erreur_conditions = _validate_conditions(defn.get("conditions", []))
    if erreur_conditions:
        return None, erreur_conditions

    actions = defn.get("actions", [])
    if not actions:
        return None, "Ajouter au moins une action."
    erreur_actions = _validate_actions(actions)
    if erreur_actions:
        return None, erreur_actions

    return {
        "trigger": trigger,
        "conditions": defn.get("conditions", []),
        "actions": actions,
    }, None


def _editor_context(scenario=None):
    return {
        "active_tab": "configuration",
        "scenario": scenario,
        "editor_data": {
            "fonctions": available_functions(),
            "infos": available_infos(),
            "boutons": [
                {"name": c.name, "label": c.label}
                for c in Control.objects.filter(type=Control.BUTTON)
            ],
            "switchs": [
                {"name": c.name, "label": c.label}
                for c in Control.objects.filter(type=Control.SWITCH)
            ],
            "scenarios": [
                s.name for s in Scenario.objects.all()
                if scenario is None or s.pk != scenario.pk
            ],
            "variables": [
                {"name": v.name, "value": v.value} for v in Variable.objects.all()
            ],
            "initial": (scenario.definition if scenario else None)
            or {"trigger": {"type": "manuel"}, "conditions": [], "actions": []},
        },
    }


def scenario_edit(request, pk=None):
    """Création (pk=None) ou modification d'un scénario."""
    scenario = get_object_or_404(Scenario, pk=pk) if pk else None

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        description = request.POST.get("description", "").strip()
        defn, erreur = _validate_definition(request.POST.get("definition", ""))

        if not name:
            erreur = "Le nom est obligatoire."
        elif Scenario.objects.exclude(pk=pk).filter(name__iexact=name).exists():
            erreur = f"Un scénario nommé « {name} » existe déjà."

        if erreur:
            messages.error(request, erreur)
            context = _editor_context(scenario)
            context["form_name"] = name
            context["form_description"] = description
            return render(request, "core/scenario_editeur.html", context)

        if scenario is None:
            scenario = Scenario.objects.create(
                name=name, description=description, definition=defn
            )
            journal(f"Scénario créé : « {name} »", module="scenarios")
            messages.success(request, f"Scénario « {name} » créé.")
        else:
            scenario.name = name
            scenario.description = description
            scenario.definition = defn
            scenario.save()
            journal(f"Scénario modifié : « {name} »", module="scenarios")
            messages.success(request, f"Scénario « {name} » enregistré.")

        scheduler.refresh_scenarios()
        return redirect("core:configuration")

    context = _editor_context(scenario)
    context["form_name"] = scenario.name if scenario else ""
    context["form_description"] = scenario.description if scenario else ""
    return render(request, "core/scenario_editeur.html", context)


@require_POST
def scenario_delete(request, pk):
    scenario = get_object_or_404(Scenario, pk=pk)
    journal(f"Scénario supprimé : « {scenario.name} »", module="scenarios")
    messages.success(request, f"Scénario « {scenario.name} » supprimé.")
    scenario.delete()
    scheduler.refresh_scenarios()
    return redirect("core:configuration")


@require_POST
def scenario_toggle(request, pk):
    scenario = get_object_or_404(Scenario, pk=pk)
    scenario.enabled = not scenario.enabled
    scenario.save(update_fields=["enabled"])
    etat = "activé" if scenario.enabled else "désactivé"
    journal(f"Scénario {etat} : « {scenario.name} »", module="scenarios")
    scheduler.refresh_scenarios()
    return redirect("core:configuration")


@require_POST
def scenario_run(request, pk):
    scenario = get_object_or_404(Scenario, pk=pk)
    run_scenario_async(scenario, origin="test manuel")
    messages.success(
        request,
        f"Scénario « {scenario.name} » lancé — voir le Journal pour le résultat.",
    )
    return redirect("core:configuration")
