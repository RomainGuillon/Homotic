# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

"""Manifest du module Tuya (capteurs température/humidité + prises)."""

ONGLET = "Capteurs"
ICONE = "thermometer-half"
DESCRIPTION = ("Capteurs température/humidité et prises connectées Tuya, "
               "lus sur le réseau local ou via l'API Cloud.")

# Tâches périodiques (scheduler du socle) — surchargeable via
# « tache_actualiser_minutes » (0 = désactivée).
TACHES = [
    {"nom": "actualiser", "fonction": "fonctions.api.tache_actualiser", "minutes": 10},
]

# Fonctions exposées aux scénarios : générées dynamiquement, une paire
# allumer_x / eteindre_x par prise détectée (voir fonctions/scenario.py).
try:
    from modules.tuya.fonctions.scenario import build_scenario_entries

    SCENARIO = build_scenario_entries()
except Exception:  # base pas prête ou module pas encore activé
    SCENARIO = []

# Fonctions d'info (lecture) : température/humidité par capteur, état par prise.
try:
    from modules.tuya.fonctions.info import build_info_entries

    INFOS = build_info_entries()
except Exception:
    INFOS = []
