# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

"""Blocs du tableau de bord : l'heure calculée pour le chauffe-eau (mode
retenu, créneau solaire) et les heures de lancement des machines."""

from django.template.loader import render_to_string

from ..fonctions import api, calcul, machines


def blocs(request):
    # Lecture des derniers calculs mémorisés : afficher le tableau de bord
    # ne doit déplacer ni l'heure du ballon ni celles des machines (voir
    # calcul.dernier_resultat).
    return [
        {
            # Sans titre ni icône : le bloc garde ceux du module, comme
            # avant l'arrivée du second bloc.
            "html": render_to_string(
                "heure_demarrage/_bloc.html", {"r": calcul.dernier_resultat()}
            ),
        },
        {
            "titre": "Machines",
            "icone": "basket",
            # « request » : le bloc contient un formulaire, il lui faut le
            # jeton CSRF.
            "html": render_to_string(
                "heure_demarrage/_bloc_machines.html",
                {
                    "p": machines.dernier_resultat(),
                    "demandes": api.machines_demandees(),
                    "max_machines": api.MAX_MACHINES,
                    "optimise": api.machines_optimise(),
                },
                request=request,
            ),
        },
    ]
