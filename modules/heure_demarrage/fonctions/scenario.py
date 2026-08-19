# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

"""Fonctions d'ACTION du module Heure de démarrage (contrat SCENARIO).

Permettent de relancer le calcul depuis un scénario, par exemple juste
avant de tester l'heure ou en début de journée.
"""

from . import api, calcul


def recalculer(arbitrage="nuit"):
    """Recalcule l'heure de démarrage, publie les variables et journalise
    le détail du calcul. Retourne l'heure retenue (HH:MM).

    ``arbitrage`` :

    - « nuit » : compare le coût de la chauffe solaire à celui des heures
      creuses, et peut décider de reporter à la nuit ;
    - « jour » : les heures creuses sont passées, on ne compare pas. Seul
      compte le meilleur créneau solaire restant, et faute de surplus, le
      moins coûteux de la journée.
    """
    resultat = api.tache_actualiser(arbitrage=arbitrage)
    return resultat.get("heure")


def publier_variables():
    """Republie les réglages du module en variables globales (sans recalcul)."""
    api.publier_variables()
    return "ok"


SCENARIO = [
    {"nom": "recalculer", "fonction": "fonctions.scenario.recalculer",
     "description": "Recalcule l'heure de démarrage et met à jour "
                    "heure_demarrage_chauffe_eau (détail dans le Journal)",
     "params": [
         {"nom": "arbitrage", "label": "Arbitrage", "options": [
             ["nuit", "Nuit — comparer avec les heures creuses"],
             ["jour", "Jour — solaire seul, heures creuses passées"],
         ]},
     ]},
    {"nom": "publier_variables", "fonction": "fonctions.scenario.publier_variables",
     "description": "Republie les réglages du module en variables globales"},
]


def build_scenario_entries():
    return list(SCENARIO)
