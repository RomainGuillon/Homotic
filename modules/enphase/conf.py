"""Manifest du module Énergie (passerelle Enphase Envoy locale)."""

ONGLET = "Énergie"
ICONE = "lightning-charge"
DESCRIPTION = (
    "Production solaire, consommation et flux réseau (Enphase Envoy). "
    "Publie les variables enphase_production_w, enphase_conso_w, "
    "enphase_import_w, enphase_export_w pour les scénarios."
)

TACHES = [
    {"nom": "actualiser", "fonction": "fonctions.api.tache_actualiser", "minutes": 5},
]

SCENARIO = []  # pas d'action directe : ce module fournit des mesures

# Ce module a besoin d'une tarification pour chiffrer la journée mesurée.
# Il ne nomme aucun fournisseur : le branchement se fait dans
# Configuration → Liaisons (voir docs/09-liaisons-entre-modules.md).
BESOINS = [
    {
        "nom": "tarifs_jour",
        "libelle": "Tarification électrique du jour",
        "type": "objet",
        "obligatoire": False,
        "sans": "le coût de la journée n'est pas affiché, les mesures restent complètes",
    },
]

# Fonctions d'info (lecture) : une fonction par mesure disponible.
try:
    from modules.enphase.fonctions.info import build_info_entries

    INFOS = build_info_entries()
except Exception:
    INFOS = []
