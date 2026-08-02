"""Manifest du module Tempo."""

ONGLET = "Tempo"
ICONE = "calendar3"
DESCRIPTION = "Couleurs des jours EDF Tempo (API RTE) : jour, lendemain, compteurs de saison et tarifs."

# Tâches périodiques (exécutées par le scheduler du socle).
# Périodicité surchargeable dans le paramétrage de l'onglet (0 = désactivée).
TACHES = [
    {"nom": "actualiser", "fonction": "fonctions.api.tache_actualiser", "minutes": 30},
]

# Fonctions d'info (lecture) : couleurs, période, prix courant.
try:
    from modules.tempo.fonctions.info import build_info_entries

    INFOS = build_info_entries()
except Exception:
    INFOS = []
