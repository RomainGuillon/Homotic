"""Bloc « Production solaire prévue » du tableau de bord (présentation v1)."""

from django.template.loader import render_to_string

from ..fonctions import api
from ..onglet.views import build_solar_context


def bloc(request):
    if not api.configured():
        return render_to_string("solcast/_bloc.html", {"non_configure": True})

    context = {"non_configure": False}
    context.update(build_solar_context())
    return render_to_string("solcast/_bloc.html", context)
