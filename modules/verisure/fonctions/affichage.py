"""Fonctions d'affichage du module Alarme : bouclier coloré selon l'état.

Convention de couleur : vert = la maison est protégée, ambre = protection
partielle, gris = aucune protection, rouge = état non compris. Le gris pour
« désarmée » plutôt que le rouge est délibéré — désarmer est une situation
normale, pas une anomalie ; le rouge reste disponible pour ce qui cloche.
"""

_COULEURS = {
    "desarmee": "#5a6473",
    "partielle": "#f59e0b",
    "partielle_peri": "#f59e0b",
    "partielle_annexe": "#f59e0b",
    "totale": "#4ade80",
    "totale_peri": "#4ade80",
    "totale_annexe": "#4ade80",
    "peripherique": "#4ade80",
    "annexe": "#4ade80",
}
_INCONNU = "#ef4444"

_TRACK = "#243044"
_TEXTE = "#e5e7eb"


def couleur(cle):
    return _COULEURS.get(cle, _INCONNU)


def bouclier_svg(cle, libelle, width=170):
    """Bouclier avec cadenas fermé (armée) ou ouvert (désarmée)."""
    teinte = couleur(cle)
    ferme = cle != "desarmee"
    height = round(width * 200 / 180)

    # Anse du cadenas : relevée quand c'est ouvert.
    anse = ("M72 96 v-14 a18 18 0 0 1 36 0 v14"
            if ferme else
            "M72 96 v-14 a18 18 0 0 1 36 0")

    return f"""<svg viewBox="0 0 180 200" width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">
      <path d="M90 12 L156 36 v52 c0 42 -28 78 -66 96 c-38 -18 -66 -54 -66 -96 V36 Z"
            fill="{_TRACK}" stroke="{teinte}" stroke-width="4"/>
      <path d="{anse}" fill="none" stroke="{teinte}" stroke-width="9" stroke-linecap="round"/>
      <rect x="60" y="96" width="60" height="46" rx="9" fill="{teinte}"/>
      <circle cx="90" cy="115" r="6" fill="{_TRACK}"/>
      <rect x="87" y="115" width="6" height="14" rx="3" fill="{_TRACK}"/>
      <text x="90" y="176" text-anchor="middle" fill="{_TEXTE}" font-size="17" font-weight="700"
            font-family="-apple-system,Segoe UI,Roboto,sans-serif">{libelle}</text>
    </svg>"""


def badge_classe(cle):
    """Classe Bootstrap du badge d'état, pour l'onglet et le bloc."""
    if cle == "desarmee":
        return "text-bg-secondary"
    if cle in ("partielle", "partielle_peri", "partielle_annexe"):
        return "text-bg-warning"
    if cle in _COULEURS:
        return "text-bg-success"
    return "text-bg-danger"
