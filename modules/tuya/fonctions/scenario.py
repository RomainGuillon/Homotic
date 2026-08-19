# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

"""Fonctions scénario du module Tuya — générées par prise.

Chaque prise détectée expose une paire de fonctions SIMPLES :
``allumer_<slug>`` et ``eteindre_<slug>`` (ex : ``allumer_machine_a_laver``).
Elles sont résolues dynamiquement (PEP 562) : pas besoin de modifier ce
fichier quand une prise apparaît — le catalogue de l'éditeur est reconstruit
à partir du cache des prises.
"""

import json

from . import api


def _cached_plugs():
    """Prises du cache (sans appel API : appelé à l'import du conf.py)."""
    from core.services import get_setting

    raw = get_setting("cache_plugs", module=api.MODULE)
    if not raw:
        return []
    try:
        return json.loads(raw).get("data") or []
    except (ValueError, TypeError):
        return []


def build_scenario_entries():
    """Entrées SCENARIO du conf.py : une paire allumer/eteindre par prise."""
    entries = []
    for p in _cached_plugs():
        s = api.slug(p["name"])
        entries.append({
            "nom": f"allumer_{s}",
            "fonction": f"fonctions.scenario.allumer_{s}",
            "description": f"Allumer la prise « {p['name']} »",
        })
        entries.append({
            "nom": f"eteindre_{s}",
            "fonction": f"fonctions.scenario.eteindre_{s}",
            "description": f"Éteindre la prise « {p['name']} »",
        })
    return entries


def __getattr__(name):
    """Résout allumer_<slug> / eteindre_<slug> en fonctions réelles."""
    if name.startswith("allumer_"):
        plug_slug = name[len("allumer_"):]
        return lambda: api.set_plug_by_slug(plug_slug, True)
    if name.startswith("eteindre_"):
        plug_slug = name[len("eteindre_"):]
        return lambda: api.set_plug_by_slug(plug_slug, False)
    raise AttributeError(name)
