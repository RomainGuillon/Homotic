# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

"""Fonctions SIMPLES du chauffe-eau pour les scénarios.

Déclarées dans conf.py (SCENARIO) : l'éditeur de scénarios les proposera
comme actions.
"""

from . import api


def chauffer():
    """Chauffe max : nombre de douches souhaité à la valeur réglée
    (5 par défaut, réglable dans le paramétrage du module)."""
    return api.set_showers(api.douches_chauffe())


def eteindre():
    """Chauffe mini : nombre de douches souhaité à la valeur réglée
    (1 par défaut, réglable dans le paramétrage du module)."""
    return api.set_showers(api.douches_veille())


def boost_on():
    """Active le mode boost."""
    return api.set_boost_mode("on")


def boost_off():
    """Arrête le mode boost."""
    return api.set_boost_mode("off")


def boost_prog():
    """Boost en mode programme."""
    return api.set_boost_mode("prog")


def absence(depart="maintenant", retour=""):
    """Programme une absence entre deux dates.

    Les deux champs se saisissent dans l'action du scénario. Trois
    écritures acceptées (voir ``api.parse_moment``) :

    - « 20/09/2026 18:00 » : date fixe, pour une absence ponctuelle ;
    - « maintenant » : au moment où le scénario s'exécute ;
    - « +7j 18:00 » : dans 7 jours à 18 h — la seule forme qui garde un
      sens dans un scénario récurrent ou déclenché par un bouton.

    Départ vide = maintenant. Retour vide = erreur : une absence sans
    date de retour laisserait le ballon froid indéfiniment.
    """
    return api.set_absence(depart, retour)


def absence_off():
    """Annule l'absence en cours ou programmée."""
    return api.arreter_absence()


def __getattr__(name):
    """douches_1 .. douches_5 : règle le nombre de douches souhaité."""
    if name.startswith("douches_"):
        try:
            n = int(name[len("douches_"):])
        except ValueError:
            raise AttributeError(name)
        if 1 <= n <= 5:
            return lambda: api.set_showers(n)
    raise AttributeError(name)
