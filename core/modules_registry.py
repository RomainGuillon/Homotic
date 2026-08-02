"""Détection des modules présents dans le répertoire ``modules/``.

Contrat minimal d'un module (étape 3) : un sous-répertoire de ``modules/``
contenant un fichier ``conf.py`` qui déclare :

    ONGLET = "Nom de l'onglet"          # obligatoire
    ICONE = "calendar3"                  # optionnel (Bootstrap Icons)
    DESCRIPTION = "À quoi sert ce module"  # optionnel

Le contrat s'enrichira aux étapes suivantes (périodicités, champs de
configuration, fonctions exposées aux scénarios...).
"""

import importlib.util
import os
from pathlib import Path

from django.conf import settings


def scan_modules():
    """Retourne la liste des modules détectés sur le disque.

    Chaque élément : {"name", "onglet", "icone", "description", "erreur"}.
    Un répertoire sans ``conf.py`` est ignoré.
    """
    result = []
    base = Path(settings.MODULES_DIR)
    if not base.exists():
        return result

    for d in sorted(base.iterdir()):
        if not d.is_dir() or d.name.startswith(("_", ".")):
            continue
        conf_file = d / "conf.py"
        if not conf_file.exists():
            continue

        info = {
            "name": d.name,
            "onglet": d.name,
            "icone": "puzzle",
            "description": "",
            "erreur": "",
        }
        try:
            spec = importlib.util.spec_from_file_location(
                f"modules.{d.name}.conf", conf_file
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            info["onglet"] = str(getattr(mod, "ONGLET", d.name))
            info["icone"] = str(getattr(mod, "ICONE", "puzzle"))
            info["description"] = str(getattr(mod, "DESCRIPTION", ""))
        except Exception as exc:  # conf.py invalide : on l'affiche, sans planter
            info["erreur"] = f"conf.py invalide : {exc}"
        result.append(info)

    return result


def trigger_restart():
    """Déclenche le redémarrage du serveur de développement.

    On touche ``homotic/settings.py`` : l'autoreloader de ``runserver``
    détecte le changement et relance le process, qui relit alors la liste
    des modules actifs en base (voir settings.INSTALLED_APPS).
    """
    settings_file = Path(settings.BASE_DIR) / "homotic" / "settings.py"
    os.utime(settings_file, None)
