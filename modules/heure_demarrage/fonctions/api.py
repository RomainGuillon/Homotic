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

DEFAUTS = {k: d for k, d, _t in REGLAGES}

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
    return resultat
