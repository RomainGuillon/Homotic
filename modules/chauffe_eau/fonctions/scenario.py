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


def _jour(choix, date_precise):
    """Traduit le choix de jour en quelque chose que le module comprend.

    « Aujourd'hui » vaut une chaîne vide (le jour d'exécution), « Dans 3
    jours » vaut « +3j », et « Date précise… » renvoie au calendrier du
    champ voisin. Une date reçue directement dans le choix est acceptée
    telle quelle : les scénarios enregistrés avant la liste déroulante
    continuent de fonctionner.
    """
    choix = str(choix or "").strip()
    if choix == "date":
        return str(date_precise or "").strip()
    return choix


def absence(depart_jour="", depart_date="", depart_heure="",
            retour_jour="", retour_date="", retour_heure="",
            depart="", retour=""):
    """Programme une absence entre deux dates.

    Chaque moment se saisit en deux temps : un jour, choisi **relativement
    au jour d'exécution** (« Aujourd'hui », « Demain », « Dans 5
    jours »…), et une heure. C'est ce qui permet d'écrire « départ
    aujourd'hui à 18 h » dans un scénario qui rejouera la semaine
    prochaine à l'identique — une date figée au calendrier ne vaudrait que
    la première fois. « Date précise… » ouvre le calendrier pour les
    absences ponctuelles.

    Deux commodités :

    - départ « Aujourd'hui » sans heure = au moment de l'exécution ;
    - retour « Aujourd'hui » dont l'heure tombe avant le départ = le
      **prochain** passage à cette heure, donc le lendemain. « Je pars ce
      soir 22 h, retour 7 h » veut dire 7 h demain, pas 7 h ce matin.

    Un retour sans heure ni jour reste une erreur : une absence sans fin
    laisserait le ballon froid indéfiniment.

    ``depart`` et ``retour`` acceptent encore un texte complet
    (« 20/09/2026 18:00 », « maintenant », « +7j 18:00 ») : les scénarios
    écrits avant ces champs continuent de fonctionner.
    """
    jour_depart = _jour(depart_jour, depart_date)
    jour_retour = _jour(retour_jour, retour_date)

    debut = api.parse_moment(depart) if depart else api.moment_jour_heure(
        jour_depart, depart_heure
    )
    fin = api.parse_moment(retour) if retour else api.moment_jour_heure(
        jour_retour, retour_heure
    )
    if fin and debut and fin <= debut and not retour and not jour_retour:
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
