# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

import os
import sys

from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"
    verbose_name = "Homotic - Socle"

    def ready(self):
        # Le scheduler ne démarre qu'avec le serveur (pas pour migrate,
        # shell...) et une seule fois (pas dans le process de surveillance
        # de l'autoreload).
        if "runserver" not in sys.argv:
            return
        if os.environ.get("RUN_MAIN") != "true" and "--noreload" not in sys.argv:
            return
        try:
            from . import scheduler

            scheduler.start()
        except Exception as exc:  # base pas migrée, etc. : on ne bloque pas le serveur
            print(f"[scheduler] démarrage impossible : {exc}")
            # Sans scheduler, plus aucun scénario horaire ni tâche périodique ne
            # tourne : l'erreur doit être visible dans l'onglet Journal.
            try:
                from .models import LogEntry
                from .services import journal

                journal(
                    f"Scheduler NON démarré ({type(exc).__name__}) : {exc} — "
                    f"aucune tâche périodique ni scénario horaire ne tournera. "
                    f"Vérifier les dépendances (pip install -r requirements.txt).",
                    level=LogEntry.ERROR,
                )
            except Exception:
                pass
