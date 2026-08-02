"""Fonctions scénario du module Climatisation — générées par unité.

Chaque clim détectée expose ``allumer_<slug>`` et ``eteindre_<slug>``
(ex : ``allumer_salon``). Résolution dynamique (PEP 562), catalogue
reconstruit depuis le cache des unités.
"""

import json

from . import api


def _cached_units():
    from core.services import get_setting

    raw = get_setting("cache_units", module=api.MODULE)
    if not raw:
        return []
    try:
        return json.loads(raw).get("data") or []
    except (ValueError, TypeError):
        return []


# Paramètres proposés dans l'éditeur ("" = ne pas changer)
PARAMS_CLIM = [
    {"nom": "mode", "label": "Mode", "options": [
        ["", "(inchangé)"], ["auto", "Auto"], ["cooling", "Froid"],
        ["heating", "Chauffage"], ["dehumidify", "Déshumidification"], ["fan", "Ventilation"]]},
    {"nom": "consigne", "label": "Consigne", "options": [
        ["", "(inchangée)"]] + [[str(t), f"{t} °C"] for t in range(api.TEMP_MIN, api.TEMP_MAX + 1)]},
    {"nom": "ventilation", "label": "Ventilation", "options": [
        ["", "(inchangée)"]] + [[f, f] for f in api.FAN_OPTIONS]},
    {"nom": "balayage", "label": "Balayage", "options": [
        ["", "(inchangé)"]] + [[s, s] for s in api.SWING_OPTIONS]},
]


def build_scenario_entries():
    entries = []
    for u in _cached_units():
        s = u.get("room") or api.slug(u.get("label", ""))
        label = u.get("label")
        entries += [
            {"nom": f"allumer_{s}", "fonction": f"fonctions.scenario.allumer_{s}",
             "description": f"Allumer la clim « {label} » (réglages au choix)",
             "params": PARAMS_CLIM},
            {"nom": f"eteindre_{s}", "fonction": f"fonctions.scenario.eteindre_{s}",
             "description": f"Éteindre la clim « {label} »"},
            {"nom": f"regler_{s}", "fonction": f"fonctions.scenario.regler_{s}",
             "description": f"Régler « {label} » sans changer marche/arrêt",
             "params": PARAMS_CLIM},
        ]
    return entries


def _kwargs(mode="", consigne="", ventilation="", balayage=""):
    """Traduit les paramètres de l'éditeur en arguments de set_unit."""
    kw = {}
    if mode:
        kw["mode"] = mode
    if consigne:
        kw["temperature"] = int(consigne)
    if ventilation:
        kw["fan_mode"] = ventilation
    if balayage:
        kw["swing"] = balayage
    return kw


def __getattr__(name):
    if name.startswith("allumer_"):
        room = name[len("allumer_"):]
        return lambda **p: api.set_unit(room, power="on", **_kwargs(**p))
    if name.startswith("eteindre_"):
        room = name[len("eteindre_"):]
        return lambda **p: api.set_unit(room, power="off")
    if name.startswith("regler_"):
        room = name[len("regler_"):]
        return lambda **p: api.set_unit(room, **_kwargs(**p)) if _kwargs(**p) else None
    # Compatibilité avec d'anciens scénarios
    if name.startswith("mode_chauffage_"):
        room = name[len("mode_chauffage_"):]
        return lambda: api.set_unit(room, mode="heating")
    if name.startswith("mode_froid_"):
        room = name[len("mode_froid_"):]
        return lambda: api.set_unit(room, mode="cooling")
    if name.startswith("mode_auto_"):
        room = name[len("mode_auto_"):]
        return lambda: api.set_unit(room, mode="auto")
    raise AttributeError(name)
