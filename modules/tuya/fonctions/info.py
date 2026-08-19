# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

"""Fonctions d'INFO du module Tuya (contrat INFOS de l'éditeur).

Générées par appareil : ``temperature_<capteur>``, ``humidite_<capteur>``
pour chaque capteur, ``etat_<prise>`` (« on »/« off ») pour chaque prise.
Lecture depuis les caches (rafraîchis par la tâche périodique).
"""

import json

from . import api


def _cached(key):
    from core.services import get_setting

    raw = get_setting(key, module=api.MODULE)
    if not raw:
        return []
    try:
        return json.loads(raw).get("data") or []
    except (ValueError, TypeError):
        return []


def build_info_entries():
    entries = []
    for s in _cached("cache_sensors"):
        slug = api.slug(s["name"])
        entries.append({
            "nom": f"temperature_{slug}",
            "fonction": f"fonctions.info.temperature_{slug}",
            "description": f"Température « {s['name']} » (°C)",
        })
        entries.append({
            "nom": f"humidite_{slug}",
            "fonction": f"fonctions.info.humidite_{slug}",
            "description": f"Humidité « {s['name']} » (%)",
        })
    for p in _cached("cache_plugs"):
        slug = api.slug(p["name"])
        entries.append({
            "nom": f"etat_{slug}",
            "fonction": f"fonctions.info.etat_{slug}",
            "description": f"État de la prise « {p['name']} » (on/off)",
        })
    return entries


def _sensor_value(slug, key):
    data, _ts, _err = api.get_sensors_cached()
    for s in data or []:
        if api.slug(s["name"]) == slug:
            return s.get(key)
    return None


def _plug_state(slug):
    data, _ts, _err = api.get_plugs_cached()
    for p in data or []:
        if api.slug(p["name"]) == slug:
            return "on" if p.get("state") else "off"
    return None


def __getattr__(name):
    if name.startswith("temperature_"):
        s = name[len("temperature_"):]
        return lambda: _sensor_value(s, "temperature")
    if name.startswith("humidite_"):
        s = name[len("humidite_"):]
        return lambda: _sensor_value(s, "humidity")
    if name.startswith("etat_"):
        s = name[len("etat_"):]
        return lambda: _plug_state(s)
    raise AttributeError(name)
