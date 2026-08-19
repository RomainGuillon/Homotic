# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

"""Manifest du module Climatisation (Hitachi Hi-Kumo via Overkiz)."""

ONGLET = "Climatisation"
ICONE = "wind"
DESCRIPTION = "Climatisations Hitachi Hi-Kumo : état, consigne, mode, ventilation, balayage."

# Tâches périodiques (scheduler du socle).
TACHES = [
    {"nom": "actualiser", "fonction": "fonctions.api.tache_actualiser", "minutes": 15},
]

# Fonctions scénario : générées dynamiquement, une paire allumer_x /
# eteindre_x par unité détectée (voir fonctions/scenario.py).
try:
    from modules.clim.fonctions.scenario import build_scenario_entries

    SCENARIO = build_scenario_entries()
except Exception:
    SCENARIO = []

# Fonctions d'info (lecture) par unité : état, température pièce, consigne, mode.
try:
    from modules.clim.fonctions.info import build_info_entries

    INFOS = build_info_entries()
except Exception:
    INFOS = []
