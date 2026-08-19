# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

"""Manifest du module Solaire (prévisions Solcast)."""

ONGLET = "Solaire"
ICONE = "sun"
DESCRIPTION = (
    "Prévisions de production solaire Solcast : courbes, cumuls jour/demain, "
    "meilleur créneau chauffe-eau. Publie les variables "
    "solcast_prevu_aujourdhui_kwh et solcast_prevu_demain_kwh."
)

# Quota gratuit Solcast : 10 appels/jour pour le compte, et **un appel par
# site** à chaque requête. Avec 2 sites, un rafraîchissement coûte 2 appels,
# donc 5 rafraîchissements par jour au maximum.
#
# D'où des heures fixes plutôt qu'un intervalle : le coût de la journée est
# connu d'avance (5 × 2 = 10 appels) et ne dépend pas du nombre de fois où
# les pages sont affichées. 3h alimente le scénario de calcul de l'heure de
# démarrage du chauffe-eau, les suivantes suivent l'évolution de la journée.
# Modifiable sans toucher au code via le réglage `tache_previsions_heures`
# du module (vide = tâche désactivée).
TACHES = [
    {
        "nom": "previsions",
        "fonction": "fonctions.api.tache_actualiser",
        # Horaires par défaut, modifiables dans l'onglet Solaire (ajout /
        # retrait ligne par ligne). Les minutes sont libres : « 07:30 ».
        "heures": ["03:00", "07:00", "11:00", "15:00", "17:00"],
    },
]

SCENARIO = []  # module de mesures : fournit des infos, pas d'actions

# Deux besoins d'affichage : comparer le prévu au réalisé, et situer la
# chauffe sur la courbe. Aucun n'est vital — sans eux, l'onglet montre les
# prévisions seules. Branchement : Configuration → Liaisons.
BESOINS = [
    {
        "nom": "production_reelle",
        "libelle": "Production solaire mesurée",
        "type": "serie",
        "unite": "kW",
        "obligatoire": False,
        "sans": "la courbe « réel » est remplacée par l'estimation Solcast",
    },
    {
        "nom": "creneau_chauffe",
        "libelle": "Créneau de chauffe du chauffe-eau",
        "type": "objet",
        "obligatoire": False,
        "sans": (
            "aucune zone de chauffe surlignée sur les courbes ; le "
            "« meilleur créneau » (infos creneau_debut/fin/kwh) utilise une "
            "durée de chauffe approximative (60 ou 90 min selon la date) au "
            "lieu de la durée réellement retenue"
        ),
    },
]

# Fonctions d'info (lecture) : prévisions et meilleur créneau.
try:
    from modules.solcast.fonctions.info import build_info_entries

    INFOS = build_info_entries()
except Exception:
    INFOS = []
