# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

"""Fonctions d'INFO du module Solaire (contrat INFOS)."""

from datetime import date, timedelta

from . import api


def _summary():
    try:
        return api.get_summary()
    except Exception:
        return {}


def prevu_aujourdhui_kwh():
    """Production prévue aujourd'hui (kWh)."""
    v = _summary().get("today_kwh")
    return round(v, 1) if v is not None else None


def prevu_demain_kwh():
    """Production prévue demain (kWh)."""
    v = _summary().get("tomorrow_kwh")
    return round(v, 1) if v is not None else None


def creneau_debut():
    """Début du meilleur créneau chauffe-eau (HH:MM), sinon None."""
    w = _summary().get("window")
    return w["start"].astimezone().strftime("%H:%M") if w else None


def creneau_fin():
    """Fin du meilleur créneau chauffe-eau (HH:MM), sinon None."""
    w = _summary().get("window")
    return w["end"].astimezone().strftime("%H:%M") if w else None


def creneau_kwh():
    """Énergie prévue sur le meilleur créneau (kWh)."""
    w = _summary().get("window")
    return round(w["kwh"], 1) if w else None


def prevision_pv():
    """Courbe de production prévue aujourd'hui : ``[(datetime, kW)]``.

    Liaison entre modules (type « serie », unité kW). La journée entière est
    renvoyée, passé compris : c'est au consommateur de décider s'il ne
    regarde que ce qui reste à venir. ``None`` si les prévisions sont
    indisponibles (module non configuré, API injoignable et aucun cache).
    """
    return _prevision_du(date.today())


def prevision_pv_demain():
    """Courbe de production prévue demain : ``[(datetime, kW)]``.

    Même forme que ``prevision_pv``, pour la journée suivante. Elle sert à
    qui doit décider aujourd'hui d'un report au lendemain. La liste est vide
    si la prévision en cache ne va pas jusque-là, ``None`` si les prévisions
    sont indisponibles. Une journée couverte en partie seulement (horizon de
    48 h, cache ancien) est renvoyée telle quelle : les heures qui manquent
    sont des heures sans production connue, au consommateur d'en tenir compte.
    """
    return _prevision_du(date.today() + timedelta(days=1))


def _prevision_du(jour):
    try:
        forecast = api.get_forecast()
    except Exception:
        return None
    return [
        (p["time"], p["pv_kw"])
        for p in (forecast or {}).get("periods", [])
        if p["time"].date() == jour
    ]


INFOS = [
    {"nom": "prevision_pv", "type": "serie", "unite": "kW",
     "description": "Prévision de production du jour (pas 30 min)"},
    {"nom": "prevision_pv_demain", "type": "serie", "unite": "kW",
     "description": "Prévision de production de demain (pas 30 min)"},
    {"nom": "prevu_aujourdhui_kwh", "description": "Prévu aujourd'hui (kWh)"},
    {"nom": "prevu_demain_kwh", "description": "Prévu demain (kWh)"},
    {"nom": "creneau_debut", "description": "Début du meilleur créneau chauffe-eau (HH:MM)"},
    {"nom": "creneau_fin", "description": "Fin du meilleur créneau (HH:MM)"},
    {"nom": "creneau_kwh", "description": "kWh prévus sur le créneau"},
]


def build_info_entries():
    return [
        {"type": "valeur", **e, "fonction": f"fonctions.info.{e['nom']}"} for e in INFOS
    ]
