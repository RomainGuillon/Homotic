# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

"""Context processors : variables disponibles dans tous les templates."""

from pathlib import Path

from django.conf import settings


def version_theme(request):
    """Empreinte de la feuille de style, pour casser le cache du navigateur.

    Ajoutée en paramètre à l'URL de ``theme.css`` : à chaque modification du
    fichier, l'URL change et le navigateur recharge la feuille. Sans cela une
    version en cache continue d'être servie, et les nouveaux styles semblent
    ne pas exister alors que le HTML, lui, est à jour.
    """
    fichier = Path(settings.BASE_DIR) / "core" / "static" / "core" / "theme.css"
    try:
        return {"version_theme": int(fichier.stat().st_mtime)}
    except OSError:
        return {"version_theme": 0}


def identite(request):
    """Nom et version de l'application, pour l'en-tête et le « À propos »."""
    return {
        "app_nom": getattr(settings, "APP_NOM", "Homotic"),
        "app_version": getattr(settings, "APP_VERSION", "v2"),
    }


def nav_modules(request):
    """Liste des modules actifs, pour afficher leurs onglets dans la nav."""
    try:
        from .models import Module

        return {"nav_modules": list(Module.objects.filter(enabled=True))}
    except Exception:  # base pas encore migrée
        return {"nav_modules": []}
