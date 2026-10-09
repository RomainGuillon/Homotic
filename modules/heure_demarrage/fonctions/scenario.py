# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

"""Fonctions d'ACTION du module Heure de démarrage (contrat SCENARIO).

Permettent de relancer le calcul depuis un scénario, par exemple juste
avant de tester l'heure ou en début de journée — pour le chauffe-eau comme
pour les machines.
"""

from . import api, calcul, machines


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


def recalculer_machines(normales="", courtes="", vaisselles=""):
    """Refait le plan des machines et retourne l'heure de la prochaine.

    ``normales`` / ``courtes`` : nombre de cycles voulus, placés à partir de
    maintenant — comme le bouton « Calculer ». Laissés vides tous les deux,
    l'action replace seulement les cycles encore à lancer du plan en place
    (par exemple après une mise à jour des prévisions), sans en ajouter.
    Le créneau du chauffe-eau n'est jamais modifié par cette action.
    """
    if any(str(v or "").strip() for v in (normales, courtes, vaisselles)):
        api.set_machines_demandees(normal=normales, court=courtes, vaisselle=vaisselles)
        plan = machines.calculer(tracer=True)
    else:
        plan = machines.replanifier_restantes() or machines.dernier_resultat()
    return plan["prochaine"]["heure"] if plan["prochaine"] else None


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
    {"nom": "recalculer_machines", "fonction": "fonctions.scenario.recalculer_machines",
     "description": "Recalcule les heures de lancement des machines, sans "
                    "toucher au créneau du chauffe-eau (détail dans le Journal)",
     "params": [
         {"nom": "normales", "label": "Cycles normaux", "type": "nombre",
          "placeholder": "inchangé", "largeur": 110},
         {"nom": "courtes", "label": "Cycles courts", "type": "nombre",
          "placeholder": "inchangé", "largeur": 110},
         {"nom": "vaisselles", "label": "Lave-vaisselle", "type": "nombre",
          "placeholder": "inchangé", "largeur": 110},
     ]},
]


def build_scenario_entries():
    return list(SCENARIO)
