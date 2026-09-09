# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

"""Fonctions d'INFO du chauffe-eau — une fonction par mesure (contrat INFOS).

Lecture depuis le cache du statut (rafraîchi par la tâche périodique).
"""

from . import api


def _status():
    data, _ts, _err = api.get_status_cached()
    return data or {}


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def temperature():
    """Température du ballon, milieu de cuve (°C)."""
    return _num(_status().get("temperature"))


def consigne():
    """Température de consigne (°C)."""
    return _num(_status().get("target_temperature"))


def bas_de_cuve():
    """Température en bas de cuve (°C)."""
    return _num(_status().get("bottom_temperature"))


def douches_restantes():
    """Nombre de douches restantes."""
    return _num(_status().get("showers_remaining"))


def douches_souhaitees():
    """Nombre de douches souhaité (consigne)."""
    return _num(_status().get("showers_expected"))


def eau_chaude_pct():
    """Niveau d'eau chaude (%)."""
    v = _status().get("hot_water_pct")
    return round(v, 1) if v is not None else None


def eau_chaude_litres():
    """Eau chaude disponible (litres à ~40°C)."""
    return _num(_status().get("hot_water_liters"))


def en_chauffe():
    """« on » si le ballon chauffe, sinon « off »."""
    data = _status()
    if not data:
        return None
    return "on" if api.is_heating(data.get("heating")) else "off"


def boost():
    """État du boost : on / off / prog."""
    return _status().get("boost")


def absence():
    """Mode absence déclaré par le ballon : off / prog / on."""
    return _status().get("absence")


def absence_active():
    """« on » si l'absence est en cours *maintenant*, sinon « off ».

    À préférer à « absence » en condition : les dates restent inscrites
    dans la passerelle après le retour, et une absence programmée pour la
    semaine prochaine n'est pas une absence en cours.
    """
    data = _status()
    if not data:
        return None
    return "on" if api.is_absence(data) else "off"


def absence_debut():
    """Date de départ de l'absence (JJ/MM/AAAA HH:MM), vide si aucune."""
    moment = api.parse_iso(_status().get("absence_debut"))
    return moment.strftime("%d/%m/%Y %H:%M") if moment else ""


def absence_fin():
    """Date de retour de l'absence (JJ/MM/AAAA HH:MM), vide si aucune."""
    moment = api.parse_iso(_status().get("absence_fin"))
    return moment.strftime("%d/%m/%Y %H:%M") if moment else ""


def absence_jours_restants():
    """Jours avant le retour (décimal), ou None hors absence.

    Utile en condition numérique : « relancer une chauffe quand il reste
    moins de 0,5 jour avant le retour ».
    """
    from datetime import datetime

    data = _status()
    if not data or not api.is_absence(data):
        return None
    fin = api.parse_iso(data.get("absence_fin"))
    if not fin:
        return None
    return round((fin - datetime.now()).total_seconds() / 86400, 2)


INFOS = [
    {"nom": "temperature", "description": "Température du ballon (°C)"},
    {"nom": "consigne", "description": "Consigne (°C)"},
    {"nom": "bas_de_cuve", "description": "Température bas de cuve (°C)"},
    {"nom": "douches_restantes", "description": "Douches restantes"},
    {"nom": "douches_souhaitees", "description": "Douches souhaitées"},
    {"nom": "eau_chaude_pct", "description": "Niveau d'eau chaude (%)"},
    {"nom": "eau_chaude_litres", "description": "Eau chaude disponible (L)"},
    {"nom": "en_chauffe", "description": "En chauffe (on/off)"},
    {"nom": "boost", "description": "Boost (on/off/prog)"},
    {"nom": "absence", "description": "Mode absence (off/prog/on)"},
    {"nom": "absence_active", "description": "Absence en cours (on/off)"},
    {"nom": "absence_debut", "description": "Absence — départ (JJ/MM/AAAA HH:MM)"},
    {"nom": "absence_fin", "description": "Absence — retour (JJ/MM/AAAA HH:MM)"},
    {"nom": "absence_jours_restants", "description": "Absence — jours avant le retour"},
]


def build_info_entries():
    return [
        {**e, "fonction": f"fonctions.info.{e['nom']}"} for e in INFOS
    ]
