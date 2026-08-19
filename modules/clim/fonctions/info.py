# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

"""Fonctions d'INFO du module Climatisation — par unité (contrat INFOS).

``etat_<piece>`` (on/off), ``temperature_piece_<piece>`` (°C),
``consigne_<piece>`` (°C), ``mode_<piece>`` (auto/cooling/heating/...).
Lecture depuis le cache des unités.
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


def build_info_entries():
    entries = []
    for u in _cached_units():
        room = u.get("room") or api.slug(u.get("label", ""))
        label = u.get("label", room)
        entries += [
            {"nom": f"etat_{room}", "fonction": f"fonctions.info.etat_{room}",
             "description": f"État de « {label} » (on/off)"},
            {"nom": f"temperature_piece_{room}", "fonction": f"fonctions.info.temperature_piece_{room}",
             "description": f"Température de la pièce « {label} » (°C)"},
            {"nom": f"consigne_{room}", "fonction": f"fonctions.info.consigne_{room}",
             "description": f"Consigne de « {label} » (°C)"},
            {"nom": f"mode_{room}", "fonction": f"fonctions.info.mode_{room}",
             "description": f"Mode de « {label} » (auto/cooling/heating/dehumidify/fan)"},
        ]
    return entries


def _unit(room):
    data, _ts, _err = api.get_units_cached()
    for u in data or []:
        if u.get("room") == room:
            return u
    return {}


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def __getattr__(name):
    if name.startswith("etat_"):
        room = name[len("etat_"):]
        return lambda: ("on" if _unit(room).get("on") else "off") if _unit(room) else None
    if name.startswith("temperature_piece_"):
        room = name[len("temperature_piece_"):]
        return lambda: _num(_unit(room).get("room_temperature"))
    if name.startswith("consigne_"):
        room = name[len("consigne_"):]
        return lambda: _num(_unit(room).get("temperature"))
    if name.startswith("mode_"):
        room = name[len("mode_"):]
        return lambda: _unit(room).get("mode")
    raise AttributeError(name)
