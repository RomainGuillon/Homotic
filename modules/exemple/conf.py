# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

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
