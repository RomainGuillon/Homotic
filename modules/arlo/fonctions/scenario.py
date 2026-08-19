# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

"""Actions de scénario du module Caméras.

Chaque fonction renvoie une chaîne courte : le socle la journalise, ce qui
donne une trace lisible de ce qu'un scénario a réellement fait.
"""

from . import api


def mode_absence():
    return api.changer_mode("armAway")


def mode_presence():
    return api.changer_mode("armHome")


def mode_veille():
    return api.changer_mode("standby")


def photo():
    url = api.photo()
    return "Instantané pris" if url else "Instantané sans image"
