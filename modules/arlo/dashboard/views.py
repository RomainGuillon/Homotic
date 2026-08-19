# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

"""Bloc Caméras du tableau de bord : le mode de surveillance, d'un coup d'œil.

Pas d'image ici, volontairement : la vignette Arlo est lourde, elle pousse les
autres blocs vers le bas et elle n'apporte rien à la question qu'on se pose en
regardant un tableau de bord — « est-ce que c'est armé ? ». L'image reste dans
l'onglet Caméras, avec le bouton d'instantané.

Les trois modes sont affichés côte à côte plutôt qu'un seul badge : voir les
options grisées autour de celle qui est active se lit plus vite qu'un libellé
seul, qu'il faut relire pour savoir s'il est bon.
"""

from django.template.loader import render_to_string

from ..fonctions import affichage, api


def bloc(request):
    if not api.configured():
        return render_to_string("arlo/_bloc.html", {"non_configure": True})

    etat, ts, erreur = api.etat_cached()
    courant = (etat or {}).get("code")

    modes = [
        {
            "code": code,
            "libelle": api.MODES[code],
            "icone": affichage.icone(code),
            "actif": code == courant,
            "couleur": affichage.couleur_texte(code),
        }
        for code in api.ORDRE_MODES
    ]

    cameras = (etat or {}).get("cameras") or []
    camera = cameras[0] if cameras else None

    return render_to_string(
        "arlo/_bloc.html",
        {
            "non_configure": False,
            "erreur": erreur if etat is None else "",
            "stale": bool(erreur) and etat is not None,
            "etat": etat,
            "modes": modes,
            "camera": camera,
            "classe_batterie": affichage.classe_batterie(camera.get("batterie")) if camera else "",
            "ts": ts,
        },
    )
