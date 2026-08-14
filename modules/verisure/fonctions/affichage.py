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


def bouclier_svg(cle, libelle=None, width=170):
    """Bouclier avec cadenas fermé (armée) ou ouvert (désarmée).

    ``libelle`` est optionnel et vaut mieux vide partout où un badge dit déjà
    l'état : deux fois le même mot alourdit sans rien apprendre.
    """
    teinte = couleur(cle)
    ferme = cle != "desarmee"
    hauteur_vue = 200 if libelle else 192
    height = round(width * hauteur_vue / 180)

    # Anse du cadenas : relevée quand c'est ouvert.
    anse = ("M72 96 v-14 a18 18 0 0 1 36 0 v14"
            if ferme else
            "M72 96 v-14 a18 18 0 0 1 36 0")

    texte = (
        f'<text x="90" y="176" text-anchor="middle" fill="{_TEXTE}" font-size="17"'
        f' font-weight="700" font-family="-apple-system,Segoe UI,Roboto,sans-serif">'
        f'{libelle}</text>'
    ) if libelle else ""

    return f"""<svg viewBox="0 0 180 {hauteur_vue}" width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">
      <path d="M90 12 L156 36 v52 c0 42 -28 78 -66 96 c-38 -18 -66 -54 -66 -96 V36 Z"
            fill="{_TRACK}" stroke="{teinte}" stroke-width="4"/>
      <path d="{anse}" fill="none" stroke="{teinte}" stroke-width="9" stroke-linecap="round"/>
      <rect x="60" y="96" width="60" height="46" rx="9" fill="{teinte}"/>
      <circle cx="90" cy="115" r="6" fill="{_TRACK}"/>
      <rect x="87" y="115" width="6" height="14" rx="3" fill="{_TRACK}"/>
      {texte}
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


_MOIS = ["janv.", "févr.", "mars", "avril", "mai", "juin",
         "juil.", "août", "sept.", "oct.", "nov.", "déc."]


def date_courte(iso):
    """« 2026-08-13T22:30:31 » -> « 13 août à 22:30 ».

    Le formatage est fait ici plutôt que dans le gabarit : découper une chaîne
    ISO à coups de filtres Django donnait « 2026-08-1322:30 ».
    """
    if not iso:
        return ""
    from datetime import datetime

    try:
        quand = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return str(iso)
    return f"{quand.day} {_MOIS[quand.month - 1]} à {quand:%H:%M}"
