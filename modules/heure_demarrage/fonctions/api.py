# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

"""Réglages du module Heure de démarrage + publication des variables.

Les réglages saisis dans l'onglet sont stockés dans la configuration du
module ET publiés comme variables globales, pour être utilisables dans les
scénarios (conditions et actions).
"""

from core.services import get_setting, get_variable, journal, set_setting, set_variable

MODULE = "heure_demarrage"

# (clé, défaut, type) — type : "float", "int", "bool", "heure", "choix"
REGLAGES = [
    ("temp_chauffe_ete", "60", "int"),
    ("temp_chauffe_hiver", "90", "int"),
    ("optimiser", "non", "bool"),
    ("ajustement", "faible", "choix"),
    ("conso_min_maison", "0.30", "float"),
    ("conso_chauffe_eau", "2.50", "float"),
    ("heure_nuit", "04:30", "heure"),
]

# Réglages des machines (lave-linge lancé à la main, voir machines.py).
# Tenus à part de REGLAGES : ce sont les paramètres d'un appareil, pas des
# valeurs à tester dans un scénario — ils ne sont donc pas publiés comme
# variables globales. « int0 » = entier qui peut valoir zéro.
REGLAGES_MACHINES = [
    ("machine_normal_duree", "70", "int"),
    ("machine_normal_kwh", "0.30", "float"),
    ("machine_normal_chauffe_min", "25", "int0"),
    ("machine_normal_chauffe_kwh", "0.22", "float"),
    ("machine_court_duree", "20", "int"),
    ("machine_court_kwh", "0.05", "float"),
    ("machine_court_chauffe_min", "5", "int0"),
    ("machine_court_chauffe_kwh", "0.03", "float"),
    ("machine_pointe_kw", "1.90", "float"),
    ("machine_plage_debut", "08:00", "heure"),
    ("machine_plage_fin", "20:00", "heure"),
    ("machine_pause_min", "30", "int0"),
]

DEFAUTS = {k: d for k, d, _t in REGLAGES + REGLAGES_MACHINES}

# Les deux cycles connus de la machine, dans l'ordre d'affichage.
TYPES_MACHINE = [("normal", "Cycle normal"), ("court", "Cycle court")]

# Garde-fou de saisie : au-delà, ce n'est plus une journée de lessive mais
# une faute de frappe, et le calcul explore toutes les combinaisons.
MAX_MACHINES = 6

# Valeurs autorisées des réglages de type « choix »
CHOIX = {
    "ajustement": [
        ("faible", "Faible — au plus tôt dès que le solaire couvre le besoin"),
        ("max", "Max — au pic de production solaire"),
    ],
}


def get_reglage(key):
    return get_setting(key, module=MODULE, default=DEFAUTS.get(key, ""))


def _float(key):
    try:
        return float(str(get_reglage(key)).replace(",", "."))
    except (TypeError, ValueError):
        return float(str(DEFAUTS[key]).replace(",", "."))


def _int(key):
    try:
        return int(float(str(get_reglage(key)).replace(",", ".")))
    except (TypeError, ValueError):
        return int(DEFAUTS[key])


def temp_chauffe_ete():
    """Durée de chauffe en été (minutes)."""
    return _int("temp_chauffe_ete")


def temp_chauffe_hiver():
    """Durée de chauffe en hiver (minutes)."""
    return _int("temp_chauffe_hiver")


def optimiser():
    """True si l'arbitrage coût jour/nuit est activé."""
    return str(get_reglage("optimiser")).lower() in ("oui", "true", "1", "on")


def ajustement():
    """Comment choisir le créneau de chauffe : « faible » ou « max ».

    - « faible » : le créneau dont l'import réseau est le plus faible, et
      le plus tôt à import égal. Dès que le solaire couvre le besoin,
      plusieurs créneaux sont à zéro : c'est donc le premier qui gagne.
    - « max » : le créneau où la production solaire est la plus forte. La
      chauffe se cale sur le pic, ce qui maximise la part autoconsommée
      même quand plusieurs créneaux suffiraient.
    """
    valeur = str(get_reglage("ajustement")).strip().lower()
    return "max" if valeur == "max" else "faible"


def conso_min_maison():
    """Talon de consommation de la maison, « sans rien faire » (kWh/h ≈ kW)."""
    return _float("conso_min_maison")


def conso_chauffe_eau():
    """Énergie consommée par un cycle de chauffe (kWh)."""
    return _float("conso_chauffe_eau")


def heure_nuit():
    """Heure de repli en heures creuses (par défaut 04:30)."""
    valeur = str(get_reglage("heure_nuit"))
    parts = valeur.split(":")
    if len(parts) == 2 and all(p.isdigit() for p in parts):
        return f"{int(parts[0]):02d}:{int(parts[1]):02d}"
    return "04:30"


def saison():
    """« hiver » ou « ete », d'après les switchs exclusifs du tableau de bord.

    Switch « hiver » ON -> hiver ; switch « ete » ON -> été ; **aucun des
    deux -> été**. Le repli est explicite plutôt que déduit de la date : la
    saison pilote la durée de chauffe, et une bascule automatique le 15
    octobre allongerait la chauffe sans que personne l'ait demandé. Été est
    le repli le plus prudent (chauffe la plus courte).
    """
    from core.models import Control

    try:
        if Control.objects.filter(name="hiver", type=Control.SWITCH, is_on=True).exists():
            return "hiver"
    except Exception:
        pass
    return "ete"


def duree_chauffe_min():
    """Durée de chauffe retenue selon la saison (minutes)."""
    return temp_chauffe_hiver() if saison() == "hiver" else temp_chauffe_ete()


def publier_variables():
    """Publie les réglages en variables globales (utilisables en scénario)."""
    set_variable("temp_chauffe_ete", str(temp_chauffe_ete()))
    set_variable("temp_chauffe_hiver", str(temp_chauffe_hiver()))
    set_variable("optimiser", "oui" if optimiser() else "non")
    set_variable("ajustement", ajustement())
    set_variable("conso_min_maison", f"{conso_min_maison():.2f}")
    set_variable("conso_chauffe_eau", f"{conso_chauffe_eau():.2f}")
    set_variable("duree_chauffe_min", str(duree_chauffe_min()))


# ----------------------------------------------------------------------
# Machines (lave-linge)
# ----------------------------------------------------------------------

def _heure(key):
    """Réglage « HH:MM » normalisé ; la valeur par défaut s'il est illisible."""
    for valeur in (str(get_reglage(key)), DEFAUTS[key]):
        parts = valeur.split(":")
        if len(parts) == 2 and all(p.isdigit() for p in parts):
            return f"{int(parts[0]) % 24:02d}:{int(parts[1]) % 60:02d}"
    return "00:00"


def profil_machine(type_cycle):
    """Profil de consommation d'un cycle : « normal » ou « court ».

    Un cycle se décrit en deux phases, parce qu'elles ne pèsent pas pareil
    face au solaire : la **chauffe** de l'eau, en début de cycle, tire la
    résistance à pleine puissance par salves ; le **reste** (brassage,
    rinçage, essorage) consomme peu et en continu.

    Retourne ``{type, libelle, duree_min, kwh, chauffe_min, chauffe_kwh}``.
    Les valeurs incohérentes sont ramenées dans leurs bornes plutôt que
    refusées : une chauffe ne dure pas plus que le cycle et ne consomme pas
    plus que lui.
    """
    if type_cycle not in dict(TYPES_MACHINE):
        raise ValueError(f"type de cycle inconnu « {type_cycle} »")
    prefixe = f"machine_{type_cycle}_"
    duree = max(1, _int(prefixe + "duree"))
    kwh = max(0.0, _float(prefixe + "kwh"))
    return {
        "type": type_cycle,
        "libelle": dict(TYPES_MACHINE)[type_cycle],
        "duree_min": duree,
        "kwh": kwh,
        "chauffe_min": min(duree, max(0, _int(prefixe + "chauffe_min"))),
        "chauffe_kwh": min(kwh, max(0.0, _float(prefixe + "chauffe_kwh"))),
    }


def pointe_machine_kw():
    """Puissance de la résistance de la machine pendant la chauffe (kW)."""
    return max(0.1, _float("machine_pointe_kw"))


def plage_machines():
    """Plage où un lancement à la main est possible : (« HH:MM », « HH:MM »)."""
    return _heure("machine_plage_debut"), _heure("machine_plage_fin")


def pause_machines_min():
    """Délai entre la fin d'un cycle et le lancement du suivant (minutes)."""
    return max(0, _int("machine_pause_min"))


def machines_demandees():
    """Nombre de cycles voulus dans la journée : ``{"normal": n, "court": n}``."""
    demandes = {}
    for type_cycle, _libelle in TYPES_MACHINE:
        try:
            n = int(float(get_setting(f"machines_{type_cycle}", module=MODULE, default="0")))
        except (TypeError, ValueError):
            n = 0
        demandes[type_cycle] = max(0, min(MAX_MACHINES, n))
    return demandes


def set_machines_demandees(normal=None, court=None):
    """Enregistre le nombre de cycles voulus ; ``None`` laisse la valeur en place.

    Lève ``ValueError`` si une valeur n'est pas un entier lisible : appelée
    depuis un scénario, une saisie fantaisiste doit arrêter l'action avec un
    message clair plutôt que de passer pour un zéro.
    """
    for type_cycle, valeur in (("normal", normal), ("court", court)):
        if valeur is None or str(valeur).strip() == "":
            continue
        try:
            n = int(float(str(valeur).replace(",", ".")))
        except (TypeError, ValueError):
            raise ValueError(
                f"nombre de cycles illisible « {valeur} » ({type_cycle})"
            ) from None
        set_setting(
            f"machines_{type_cycle}", str(max(0, min(MAX_MACHINES, n))), module=MODULE
        )
    return machines_demandees()


def tache_actualiser(arbitrage="nuit"):
    """Recalcule et publie l'heure de démarrage.

    ``arbitrage`` : « nuit » compare avec les heures creuses, « jour »
    cherche uniquement le meilleur créneau solaire restant.
    """
    from . import calcul

    publier_variables()
    # détail du calcul dans le Journal
    resultat = calcul.calculer(tracer=True, arbitrage=arbitrage)
    # Une heure vide (mode « jour » sans créneau restant) n'écrase pas la
    # valeur en place : mieux vaut garder la dernière heure connue que
    # laisser la variable vide, que le déclencheur ne saurait pas lire.
    if resultat.get("heure"):
        set_variable("heure_demarrage_chauffe_eau", resultat["heure"])
    set_variable("heure_demarrage_mode", resultat.get("mode") or "")

    # Les machines se placent autour du créneau du ballon, qui vient
    # peut-être de bouger : leur plan est refait dans la foulée. Ce qui
    # précède est déjà enregistré — une panne ici ne doit jamais coûter
    # l'heure du chauffe-eau, d'où le filet.
    try:
        from . import machines

        machines.recalculer_si_demande()
    except Exception as exc:
        journal(f"Machines : replanification impossible — {exc}",
                module=MODULE, level="ERROR")
    return resultat
