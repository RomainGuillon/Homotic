"""Bloc Climatisations du tableau de bord (présentation v1 : liste des
unités avec mode/consigne, température pièce et pastille Marche/Arrêt)."""

from django.template.loader import render_to_string

from ..fonctions import api


def bloc(request):
    if not api.configured():
        return render_to_string("clim/_bloc.html", {"non_configure": True})

    units, _ts, erreur = api.get_units_cached()
    units = [
        {**u, "mode_fr": api.MODES_FR.get(u.get("mode"), u.get("mode"))}
        for u in (units or [])
    ]
    return render_to_string(
        "clim/_bloc.html",
        {
            "non_configure": False,
            "erreur": erreur if not units else "",
            "stale": bool(erreur) and bool(units),
            "units": units,
        },
    )
