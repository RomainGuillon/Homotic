#!/usr/bin/env python
"""Utilitaire en ligne de commande Django du projet Homotic.

Lancement du serveur de developpement (depuis le repertoire v2/) :
    python manage.py runserver 0.0.0.0:8100
"""

import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "homotic.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Django est introuvable. Verifier que l'environnement virtuel est "
            "active et que les dependances sont installees."
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
