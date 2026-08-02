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
]


def build_info_entries():
    return [
        {**e, "fonction": f"fonctions.info.{e['nom']}"} for e in INFOS
    ]
