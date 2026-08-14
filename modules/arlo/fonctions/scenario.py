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
