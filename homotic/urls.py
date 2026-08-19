# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

"""Routes principales du projet Homotic."""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    # Connexion / deconnexion / changement de mot de passe. Fournit les noms
    # « login » et « logout » attendus par LOGIN_URL et la barre de
    # navigation. La page de connexion est la seule accessible sans session.
    path("comptes/", include("django.contrib.auth.urls")),
    path("admin/", admin.site.urls),
    path("", include("core.urls")),
]
