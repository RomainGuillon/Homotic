# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

"""Fonctions d'INFO du module Caméras.

Comme partout ailleurs, ces fonctions lisent le cache et n'appellent jamais
Arlo : un déclencheur « au changement » les interroge chaque minute.
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


def arlo_mode():
    """« En absence », « En présence », « En veille »."""
    return _etat().get("libelle")


def arlo_mode_code():
    """armAway, armHome ou standby."""
    return _etat().get("code")


def arlo_batterie():
    cameras = _etat().get("cameras") or []
    return cameras[0].get("batterie") if cameras else None
