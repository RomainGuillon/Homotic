"""Manifest du module Chauffe-eau (Atlantic Cozytouch via Overkiz)."""

ONGLET = "Chauffe-eau"
ICONE = "droplet-half"
DESCRIPTION = "Ballon Atlantic Cozytouch : niveau d'eau chaude, douches, boost."

# Tâches périodiques (scheduler du socle) — surchargeable via
# « tache_actualiser_minutes » (0 = désactivée).
TACHES = [
    {"nom": "actualiser", "fonction": "fonctions.api.tache_actualiser", "minutes": 15},
    # Suivi des chauffes : relève à la minute PENDANT la chauffe seulement,
    # veille espacée le reste du temps (voir fonctions/suivi.py). Mettre
    # « suivi_actif = non » dans les réglages du module pour l'arrêter.
    {"nom": "suivi", "fonction": "fonctions.suivi.tache_suivi", "minutes": 1},
]

# Le suivi des chauffes doit savoir quand une chauffe est attendue, pour
# passer à la relève à la minute. Il ne calcule pas cette heure et ne sait
# pas qui la calcule : branchement dans Configuration → Liaisons
# (voir docs/09-liaisons-entre-modules.md).
BESOINS = [
    {
        "nom": "heure_chauffe_prevue",
        "libelle": "Heure de chauffe prévue (HH:MM)",
        "type": "valeur",
        "obligatoire": False,
        "sans": "pas de fenêtre de surveillance : le suivi reste en veille espacée",
    },
]

# Fonctions SIMPLES exposées aux scénarios : une fonction par action possible.
SCENARIO = [
    {"nom": "chauffer", "fonction": "fonctions.scenario.chauffer",
     "description": "Chauffe max : passe le nombre de douches souhaité à 5"},
    {"nom": "eteindre", "fonction": "fonctions.scenario.eteindre",
     "description": "Chauffe mini : passe le nombre de douches souhaité à 1"},
    {"nom": "boost_on", "fonction": "fonctions.scenario.boost_on",
     "description": "Active le mode boost"},
    {"nom": "boost_off", "fonction": "fonctions.scenario.boost_off",
     "description": "Arrête le mode boost"},
    {"nom": "boost_prog", "fonction": "fonctions.scenario.boost_prog",
     "description": "Boost en mode programme"},
] + [
    {"nom": f"douches_{n}", "fonction": f"fonctions.scenario.douches_{n}",
     "description": f"Nombre de douches souhaité : {n}"}
    for n in range(1, 6)
]

# Fonctions d'info (lecture) : une fonction par mesure du ballon.
try:
    from modules.chauffe_eau.fonctions.info import build_info_entries

    INFOS = build_info_entries()
except Exception:
    INFOS = []
