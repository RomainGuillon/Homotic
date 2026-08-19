# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

"""Fonctions d'INFO du module Tempo (contrat INFOS)."""

from datetime import date, datetime, timedelta

from . import api

_FR = {"BLUE": "Bleu", "WHITE": "Blanc", "RED": "Rouge"}


def _colors():
    colors, _ts, _err = api.get_colors_cached()
    return colors or {}


def couleur_du_moment():
    """Couleur Tempo en ce moment (jour 6h→6h) : Bleu / Blanc / Rouge."""
    code = api.color_of_moment(_colors(), datetime.now())
    return _FR.get(code)


def couleur_aujourdhui():
    """Couleur du jour calendaire : Bleu / Blanc / Rouge."""
    return _FR.get(_colors().get(str(date.today())))


def couleur_demain():
    """Couleur de demain (publiée vers 11h) : Bleu / Blanc / Rouge."""
    return _FR.get(_colors().get(str(date.today() + timedelta(days=1))))


def periode():
    """Période tarifaire courante : HP ou HC."""
    return api.current_period(datetime.now())


def prix_courant():
    """Prix du kWh en ce moment (€/kWh)."""
    code = api.color_of_moment(_colors(), datetime.now())
    p = api.get_prices().get(code, {}).get(api.current_period(datetime.now()))
    return round(p, 4) if p is not None else None


def tarifs_jour():
    """Tarification complète du jour (liaison entre modules, type « objet »).

    Tout ce qu'il faut pour valoriser une consommation de la journée sans
    rien savoir de Tempo : les prix de **toutes** les couleurs (un pas de
    15 min avant 6 h relève de la couleur de la veille), les bornes des
    heures creuses, et les libellés/couleurs d'affichage — sans quoi le
    module consommateur devrait redéclarer sa propre table « BLUE = Bleu ».

    Retourne ``None`` si la couleur du jour est inconnue : c'est un cas
    normal (API injoignable, module non configuré), pas une erreur.
    """
    colors = _colors()
    today = date.today()
    couleur = colors.get(str(today))
    if couleur is None:
        return None

    hc_debut, hc_fin = api.hc_bounds()
    return {
        "fournisseur": "Tempo",
        "couleur": couleur,
        "couleur_veille": colors.get(str(today - timedelta(days=1))),
        "prix": api.get_prices(),          # {"BLUE": {"HP": .., "HC": ..}, ...}
        "libelles": dict(_FR),
        "couleurs_hex": {"BLUE": "#2563eb", "WHITE": "#cbd5e1", "RED": "#dc2626"},
        "hc_debut": hc_debut,
        "hc_fin": hc_fin,
        "periode_courante": api.current_period(datetime.now()),
        "abonnement_mensuel": api.abonnement_mensuel(),
        "prix_revente": api.prix_revente(),
    }


INFOS = [
    {"nom": "tarifs_jour", "type": "objet",
     "description": "Tarification du jour (couleurs, prix HP/HC, abonnement)"},
    {"nom": "couleur_du_moment", "description": "Couleur Tempo en ce moment (Bleu/Blanc/Rouge)"},
    {"nom": "couleur_aujourdhui", "description": "Couleur du jour (Bleu/Blanc/Rouge)"},
    {"nom": "couleur_demain", "description": "Couleur de demain (Bleu/Blanc/Rouge)"},
    {"nom": "periode", "description": "Période tarifaire (HP/HC)"},
    {"nom": "prix_courant", "description": "Prix du kWh en ce moment (€)"},
]


def build_info_entries():
    return [
        {"type": "valeur", **e, "fonction": f"fonctions.info.{e['nom']}"} for e in INFOS
    ]
