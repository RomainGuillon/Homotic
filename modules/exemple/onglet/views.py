"""Onglet du module : la fonction ``onglet(request)`` est appelée par le
socle quand l'utilisateur clique sur l'onglet du module."""

from django.shortcuts import render

from core.services import get_setting, journal


def onglet(request):
    journal("Onglet exemple consulté", module="exemple")
    return render(
        request,
        "exemple/onglet.html",
        {
            "active_tab": "module:exemple",
            "exemple_reglage": get_setting("exemple_cle", module="exemple", default="(non défini)"),
        },
    )
