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
