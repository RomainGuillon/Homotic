"""Fonctions d'affichage du module Caméras."""

# Vert quand les caméras surveillent vraiment, ambre en présence (surveillance
# partielle), gris en veille : même grammaire de couleurs que le module Alarme,
# pour qu'un coup d'œil au tableau de bord se lise sans réfléchir.
_BADGES = {
    "armAway": "text-bg-success",
    "armHome": "text-bg-warning",
    "standby": "text-bg-secondary",
}

_ICONES = {
    "armAway": "shield-check",
    "armHome": "house-check",
    "standby": "pause-circle",
}


def badge_classe(code):
    return _BADGES.get(code, "text-bg-danger")


def icone(code):
    return _ICONES.get(code, "question-circle")


def classe_batterie(niveau):
    try:
        niveau = int(niveau)
    except (TypeError, ValueError):
        return "text-secondary"
    if niveau <= 15:
        return "text-danger"
    if niveau <= 35:
        return "text-warning"
    return "text-success"
