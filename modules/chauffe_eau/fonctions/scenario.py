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

from datetime import timedelta

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


def absence(depart_jour="", depart_heure="", retour_jour="", retour_heure="",
            depart="", retour=""):
    """Programme une absence entre deux dates.

    Chaque moment se saisit en deux champs : un jour et une heure. Le
    **jour laissé vide vaut le jour où le scénario s'exécute** — c'est ce
    qui permet d'écrire « départ aujourd'hui à 18 h » dans un scénario qui
    rejouera demain à l'identique. Renseigner le jour fixe une date
    précise.

    Deux commodités :

    - départ entièrement vide = au moment de l'exécution ;
    - retour sans jour dont l'heure tombe avant le départ = le **prochain**
      passage à cette heure, donc le lendemain. « Je pars ce soir 22 h,
      retour 7 h » veut dire 7 h demain, pas 7 h ce matin.

    Un retour vide reste une erreur : une absence sans fin laisserait le
    ballon froid indéfiniment.

    ``depart`` et ``retour`` acceptent encore un texte complet
    (« 20/09/2026 18:00 », « maintenant », « +7j 18:00 ») : les scénarios
    écrits avant les deux champs continuent de fonctionner.
    """
    debut = api.parse_moment(depart) if depart else api.moment_jour_heure(
        depart_jour, depart_heure
    )
    fin = api.parse_moment(retour) if retour else api.moment_jour_heure(
        retour_jour, retour_heure
    )
    if fin and debut and fin <= debut and not retour and not str(retour_jour or "").strip():
        fin += timedelta(days=1)
    return api.set_absence(debut or "maintenant", fin)


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
