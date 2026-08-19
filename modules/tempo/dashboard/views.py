# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

"""Bloc Tempo du tableau de bord : la fonction ``bloc(request)`` est
appelée par le socle ; elle retourne le HTML du bloc (ou None)."""

from datetime import date, datetime, timedelta

from django.template.loader import render_to_string

from ..fonctions import affichage, api


def bloc(request):
    if not api.configured():
        return render_to_string("tempo/_bloc.html", {"non_configure": True})

    colors, _ts, erreur = api.get_colors_cached()
    season, _sts, _serr = api.get_season_cached()
    now = datetime.now()
    today = date.today()

    current_color = api.color_of_moment(colors, now)
    period = api.current_period(now)
    c = api.COLORS.get(current_color, api.COLORS[None])
    prix = api.get_prices().get(current_color, {}).get(period)

    demain = api.COLORS.get(colors.get(str(today + timedelta(days=1))), api.COLORS[None])

    return render_to_string(
        "tempo/_bloc.html",
        {
            "non_configure": False,
            "erreur": erreur,
            "current_name": c["name"],
            "current_chip": c["hp"],
            "period_label": "Heures creuses" if period == "HC" else "Heures pleines",
            "prix_str": f"{prix:.4f}".replace(".", ",") if prix is not None else None,
            "frise": affichage.frise(colors, today, with_marker=True),
            "demain_name": demain["name"],
            "demain_chip": demain["hp"],
            "season": season,
        },
    )
