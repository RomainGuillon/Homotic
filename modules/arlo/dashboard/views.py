"""Bloc Caméras du tableau de bord : mode courant et dernière image.

Bloc d'affichage uniquement. Les boutons de changement de mode passent par le
mécanisme de contrôles du socle : trois switchs dans un même groupe exclusif,
reliés aux actions de scénario mode_absence / mode_presence / mode_veille.
C'est la voie prévue par l'architecture, et elle donne le comportement
« un seul mode à la fois » sans code supplémentaire.
"""

from django.template.loader import render_to_string

from ..fonctions import affichage, api


def bloc(request):
    if not api.configured():
        return render_to_string("arlo/_bloc.html", {"non_configure": True})

    etat, ts, erreur = api.etat_cached()
    camera = (etat or {}).get("cameras") or []
    camera = camera[0] if camera else None

    return render_to_string(
        "arlo/_bloc.html",
        {
            "non_configure": False,
            "erreur": erreur if etat is None else "",
            "stale": bool(erreur) and etat is not None,
            "etat": etat,
            "badge": affichage.badge_classe((etat or {}).get("code")),
            "icone": affichage.icone((etat or {}).get("code")),
            "camera": camera,
            "classe_batterie": affichage.classe_batterie(camera.get("batterie")) if camera else "",
            "ts": ts,
        },
    )
