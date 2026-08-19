# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

"""Manifest du module Caméras (Arlo)."""

ONGLET = "Caméras"
ICONE = "camera-video"
DESCRIPTION = (
    "Mode de surveillance des caméras Arlo (absence, présence, veille) et "
    "instantanés. Publie l'info arlo_mode et trois actions de scénario, de "
    "quoi armer les caméras quand l'alarme Verisure passe en totale."
)

# Arlo n'impose pas de quota mensuel, et pyaarlo garde une connexion ouverte
# qui reçoit les changements en direct. La tâche sert surtout de filet : elle
# relit l'état et relance la connexion si le jeton a expiré.
TACHES = [
    {"nom": "actualiser", "fonction": "fonctions.api.tache_actualiser", "minutes": 5},
]

# Une action par mode plutôt qu'une action à paramètre : elles se branchent
# aussi bien sur un scénario que sur un bouton du tableau de bord.
SCENARIO = [
    {"nom": "mode_absence",
     "fonction": "fonctions.scenario.mode_absence",
     "description": "Caméras : passer en absence (Arm Away)"},
    {"nom": "mode_presence",
     "fonction": "fonctions.scenario.mode_presence",
     "description": "Caméras : passer en présence (Arm Home)"},
    {"nom": "mode_veille",
     "fonction": "fonctions.scenario.mode_veille",
     "description": "Caméras : mettre en veille (Stand By)"},
    {"nom": "photo",
     "fonction": "fonctions.scenario.photo",
     "description": "Caméras : prendre un instantané"},
]

INFOS = [
    {"nom": "arlo_mode",
     "fonction": "fonctions.info.arlo_mode",
     "description": "Mode des caméras en clair (En absence, En présence, En veille)"},
    {"nom": "arlo_mode_code",
     "fonction": "fonctions.info.arlo_mode_code",
     "description": "Code du mode Arlo (armAway, armHome, standby)"},
    {"nom": "arlo_batterie",
     "fonction": "fonctions.info.arlo_batterie",
     "description": "Niveau de batterie de la première caméra (%)",
     "unite": "%"},
]
