"""Bloc Chauffe-eau du tableau de bord (présentation v1 : mini ballon +
douches + température + pastilles chauffe/boost)."""

from django.template.loader import render_to_string

from ..fonctions import affichage, api


def bloc(request):
    if not api.configured():
        return render_to_string("chauffe_eau/_bloc.html", {"non_configure": True})

    data, _ts, erreur = api.get_status_cached()
    if data is None:
        return render_to_string(
            "chauffe_eau/_bloc.html", {"non_configure": False, "erreur": erreur}
        )

    return render_to_string(
        "chauffe_eau/_bloc.html",
        {
            "non_configure": False,
            "erreur": "",
            "stale": bool(erreur),
            "h": data,
            "tank": affichage.tank_svg(data.get("hot_water_pct"), width=110),
            "heating_on": api.is_heating(data.get("heating")),
            "boost_on": str(data.get("boost", "")).lower() == "on",
        },
    )
