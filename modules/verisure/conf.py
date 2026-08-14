"""Manifest du module Alarme (Verisure France — lecture seule)."""

ONGLET = "Alarme"
ICONE = "shield-lock"
DESCRIPTION = (
    "État de l'alarme Verisure (lecture seule). Publie les infos alarme_etat, "
    "alarme_armee et alarme_code pour les scénarios — de quoi basculer le mode "
    "des caméras Arlo selon que la maison est armée ou non."
)

# Pas de quota mensuel côté Verisure, mais un pare-feu applicatif qui répond
# 403 quand les requêtes s'enchaînent. Le projet de référence poste toutes les
# 120 s et recommande 180 à 300 s en cas de blocage : on part directement à
# 180 s, soit 480 lectures par jour. Réglable via `tache_actualiser_minutes`
# (0 = désactivée), ou par la période ci-dessous.
TACHES = [
    {"nom": "actualiser", "fonction": "fonctions.api.tache_actualiser", "minutes": 3},
]

# Module de mesure : il lit, il ne commande rien. Aucune fonction d'armement
# n'existe dans le module — voir l'en-tête de fonctions/api.py.
SCENARIO = []

# Infos exposées à l'éditeur de scénarios. Statiques : il n'y a qu'une
# installation, pas besoin de les construire dynamiquement.
INFOS = [
    {
        "nom": "alarme_etat",
        "fonction": "fonctions.info.alarme_etat",
        "description": "État de l'alarme en clair (Désarmée, Partielle, Totale)",
    },
    {
        "nom": "alarme_armee",
        "fonction": "fonctions.info.alarme_armee",
        "description": "« on » si l'alarme est armée, « off » sinon",
    },
    {
        "nom": "alarme_code",
        "fonction": "fonctions.info.alarme_code",
        "description": "Code protocole brut (D désarmée, P/Q partielle, T totale)",
    },
]
