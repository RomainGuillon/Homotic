"""Fonctions d'INFO du module Alarme.

Ces fonctions sont appelées **très souvent** — chaque minute pour un
déclencheur, à chaque affichage d'une page pour un bloc. Elles lisent donc le
cache et n'appellent jamais l'API : c'est la tâche périodique qui rafraîchit.
Sans ça, un déclencheur « au changement » ferait une requête Verisure par
minute et finirait bloqué par le pare-feu.
"""

import json

from core.services import get_setting

from . import api


def _etat():
    brut = get_setting("cache_etat", module=api.MODULE, default="")
    if not brut:
        return {}
    try:
        return json.loads(brut).get("data") or {}
    except (ValueError, TypeError):
        return {}


def alarme_etat():
    """« Désarmée », « Partielle », « Totale »… ou None si jamais lu."""
    return _etat().get("libelle")


def alarme_armee():
    """« on » / « off » — la forme la plus commode pour une condition."""
    etat = _etat()
    if not etat:
        return None
    return "on" if etat.get("armee") else "off"


def alarme_code():
    """Code protocole brut : D, P, Q, T… Utile pour distinguer deux modes
    que le libellé regroupe."""
    return _etat().get("code")
