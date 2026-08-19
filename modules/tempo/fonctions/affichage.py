# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

"""Fonctions d'affichage du module Tempo : construction de la frise
horaire 0h→24h (repris de la v1 tempo/dashboard.py).

Sur une journée calendaire, 00h-06h porte la couleur de la veille
(jour Tempo 6h→6h). Les segments HC/HP suivent la plage heures creuses
paramétrée dans l'onglet.
"""

from datetime import datetime, timedelta

from . import api


def fmt_price(p, decimals=3):
    """0.1609 -> « 0,161 EUR » (format v1)."""
    return (f"{p:.{decimals}f}".replace(".", ",") + " EUR") if p is not None else "?"


def frise(colors, day, with_marker=False):
    """Frise d'une journée calendaire : segments + axe + curseur.

    Retourne {"segments": [...], "axis": [...], "marker": "56.25"|None,
    "name": "Bleu", "chip": "#2563eb", "known": bool}.
    """
    prices = api.get_prices()
    hc_debut, hc_fin = api.hc_bounds()
    veille = colors.get(str(day - timedelta(days=1)))
    jour = colors.get(str(day))

    def in_hc(h):
        if hc_debut > hc_fin:  # à cheval sur minuit (ex : 22h -> 6h)
            return h >= hc_debut or h < hc_fin
        return hc_debut <= h < hc_fin

    points = sorted({0, 6, 24} | ({hc_debut, hc_fin} - {24}))
    segments = []
    for a, b in zip(points, points[1:]):
        mid = (a + b) / 2
        color = veille if mid < 6 else jour
        period = "HC" if in_hc(mid) else "HP"
        c = api.COLORS.get(color, api.COLORS[None])
        segments.append(
            {
                "width": f"{(b - a) / 24 * 100:.3f}",
                "bg": c["hp"] if period == "HP" else c["hc"],
                "text": c["thp"] if period == "HP" else c["thc"],
                "price_str": fmt_price(prices.get(color, {}).get(period)),
                "period": period,
            }
        )

    axis = []
    for h in sorted(set(points) | {12, 18}):
        pos = h / 24 * 100
        transform = "translateX(-50%)"
        if pos == 0:
            transform = "translateX(0)"
        elif pos == 100:
            transform = "translateX(-100%)"
        axis.append({"pos": f"{pos:.3f}", "label": f"{h}h", "transform": transform})

    marker = None
    if with_marker:
        now = datetime.now()
        marker = f"{(now.hour * 60 + now.minute) / 1440 * 100:.2f}"

    c = api.COLORS.get(jour, api.COLORS[None])
    return {
        "segments": segments,
        "axis": axis,
        "marker": marker,
        "name": c["name"],
        "chip": c["hp"],
        "known": jour is not None,
    }
