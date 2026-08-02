"""Manifest du module — le contrat minimal.

ONGLET est obligatoire, le reste est optionnel.
"""

ONGLET = "Exemple"
ICONE = "stars"  # nom Bootstrap Icons (https://icons.getbootstrap.com)
DESCRIPTION = "Module d'exemple : à copier/coller pour créer un nouveau module."

# Tâches périodiques (optionnel) — exécutées par le scheduler du socle.
# La périodicité par défaut est surchargeable en base via le réglage
# « tache_<nom>_minutes » du module (0 = désactivée).
# TACHES = [
#     {"nom": "actualiser", "fonction": "fonctions.api.tache_actualiser", "minutes": 30},
# ]
