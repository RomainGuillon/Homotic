# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

"""Bloc « Heure de démarrage » du tableau de bord : heure calculée pour le
chauffe-eau, mode retenu et créneau solaire."""

from django.template.loader import render_to_string

from ..fonctions import calcul


def bloc(request):
    # Lecture du dernier calcul mémorisé : afficher le tableau de bord ne
    # doit pas déplacer l'heure de démarrage (voir calcul.dernier_resultat).
    return render_to_string("heure_demarrage/_bloc.html", {"r": calcul.dernier_resultat()})
