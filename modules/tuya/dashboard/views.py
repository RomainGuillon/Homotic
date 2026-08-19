# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

"""Bloc Capteurs du tableau de bord (présentation v1 : liste nom +
température + humidité)."""

from django.template.loader import render_to_string

from ..fonctions import api


def bloc(request):
    if not api.configured():
        return render_to_string("tuya/_bloc.html", {"non_configure": True})

    sensors, _ts, erreur = api.get_sensors_cached()
    return render_to_string(
        "tuya/_bloc.html",
        {
            "non_configure": False,
            "erreur": erreur if sensors is None else "",
            "stale": bool(erreur) and sensors is not None,
            "sensors": sensors or [],
        },
    )
