"""Manifest du module Heure de démarrage (chauffe-eau).

Module de calcul : un onglet de configuration, un bloc sur le tableau de bord
(heure calculée) et des fonctions utilisables dans les scénarios.
"""

ONGLET = "Heure démarrage"
ICONE = "clock-history"
DESCRIPTION = (
    "Calcule la meilleure heure de démarrage du chauffe-eau (créneau le plus "
    "productif, ou arbitrage coût jour/nuit si « optimiser » est coché)."
)

# Pas de recalcul automatique : c'est un choix. Le calcul dépend des
# prévisions solaires, qui ne sont rafraîchies qu'à heures fixes (quota
# Solcast) ; un recalcul toutes les 30 min ne faisait donc que rejouer les
# mêmes données, tout en déplaçant l'heure de démarrage sous les pieds des
# scénarios qui la lisent.
#
# Le recalcul est piloté par scénario, via l'action « recalculer » du module
# (voir fonctions/scenario.py) : à l'heure qu'on veut, après un
# rafraîchissement Solcast, sur changement de la couleur Tempo...
TACHES = []

# Ce module ne calcule rien tout seul : il lui faut une prévision de
# production et une tarification. Il ne nomme aucun fournisseur — le
# branchement se fait dans Configuration → Liaisons
# (voir docs/09-liaisons-entre-modules.md).
BESOINS = [
    {
        "nom": "prevision_pv",
        "libelle": "Prévision de production solaire",
        "type": "serie",
        "unite": "kW",
        "obligatoire": True,
        "sans": "aucun créneau solaire : repli systématique sur les heures creuses",
    },
    {
        "nom": "tarifs_jour",
        "libelle": "Tarification électrique du jour",
        "type": "objet",
        "obligatoire": False,
        "sans": "pas d'arbitrage coût jour/nuit, le créneau solaire est retenu tel quel",
    },
]

# Actions exposées aux scénarios : relancer le calcul à la demande.
try:
    from modules.heure_demarrage.fonctions.scenario import build_scenario_entries

    SCENARIO = build_scenario_entries()
except Exception:
    SCENARIO = []

# Fonctions d'info (lecture) utilisables en condition / Info → variable.
try:
    from modules.heure_demarrage.fonctions.info import build_info_entries

    INFOS = build_info_entries()
except Exception:
    INFOS = []
