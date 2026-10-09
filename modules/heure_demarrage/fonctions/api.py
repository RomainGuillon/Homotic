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

from core.services import get_setting, get_variable, set_setting, set_variable

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
    ("ecart_nuit_cts", "10.00", "float"),
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
    ("machine_tolerance_cts", "1.00", "float"),
    # Lave-vaisselle : un seul type de lavage, décrit phase par phase.
    # Relevé sur la prise « Raspberry » le 07/10/2026 (102 min, 1,71 kWh) :
    # quatre chauffes à ~2,1 kW, dont le rinçage final une heure après le
    # départ. Format : « minutes:kWh[:chauffe] » séparés par des « ; ».
    ("machine_vaisselle_phases",
     "14:0.48:chauffe; 12:0.02; 17:0.45:chauffe; 22:0.09; 8:0.21:chauffe; "
     "4:0.02; 11:0.40:chauffe; 14:0.04", "phases"),
    ("machine_vaisselle_pointe_kw", "2.10", "float"),
]

DEFAUTS = {k: d for k, d, _t in REGLAGES + REGLAGES_MACHINES}

# Switch « Optimisé » du bloc Machines. Tenu hors de REGLAGES_MACHINES : il
# ne se règle pas dans le formulaire du profil, qui l'éteindrait à chaque
# enregistrement (une case absente d'un formulaire vaut « décochée »). Et
# sans rapport avec le réglage « optimiser » ci-dessus, qui ne concerne que
# le chauffe-eau.
CLE_MACHINES_OPTIMISE = "machines_optimise"

# Les deux cycles connus de la machine, dans l'ordre d'affichage.
TYPES_MACHINE = [
    ("normal", "Cycle normal"), ("court", "Cycle court"), ("vaisselle", "Lave-vaisselle"),
]

# L'appareil de chaque type de cycle : deux cycles d'un même appareil se
# suivent, deux appareils différents peuvent tourner ensemble.
APPAREILS = {"normal": "Lave-linge", "court": "Lave-linge", "vaisselle": "Lave-vaisselle"}

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


def ecart_nuit_cts():
    """Écart de coût à partir duquel les heures creuses l'emportent (centimes).

    Ne joue que si « optimiser » est coché. Tant que la chauffe de nuit ne
    fait pas gagner au moins cette somme, la chauffe reste sur le créneau
    solaire : quelques centimes sont en dessous de l'erreur d'une prévision,
    et une éclaircie de plus que prévu suffit à les effacer — alors qu'une
    chauffe partie la nuit ne profite plus de rien. À 0, le moins cher des
    deux gagne toujours, comme avant.
    """
    return max(0.0, _float("ecart_nuit_cts"))


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
    set_variable("ecart_nuit_cts", f"{ecart_nuit_cts():.2f}")
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


def lire_phases(texte):
    """Phases saisies « minutes:kWh[:chauffe] ; … » -> liste de dicts.

    Lève ``ValueError`` sur une saisie illisible : mieux vaut garder
    l'ancien profil que calculer sur un profil faux.
    """
    phases = []
    for morceau in str(texte or "").replace("\n", ";").split(";"):
        morceau = morceau.strip()
        if not morceau:
            continue
        champs = [c.strip() for c in morceau.split(":")]
        if len(champs) not in (2, 3):
            raise ValueError(f"phase illisible « {morceau} »")
        try:
            duree = int(float(champs[0].replace(",", ".")))
            kwh = float(champs[1].replace(",", "."))
        except ValueError:
            raise ValueError(f"phase illisible « {morceau} »") from None
        chauffe = len(champs) == 3
        if chauffe and champs[2].lower() not in ("c", "chauffe"):
            raise ValueError(f"phase illisible « {morceau} » (3e champ : chauffe)")
        if duree <= 0 or kwh < 0:
            raise ValueError(f"phase illisible « {morceau} »")
        phases.append({"duree_min": duree, "kwh": kwh, "chauffe": chauffe})
    if not phases:
        raise ValueError("aucune phase")
    if sum(p["duree_min"] for p in phases) > 600:
        raise ValueError("cycle de plus de 10 heures")
    return phases


def ecrire_phases(phases):
    """Inverse de ``lire_phases`` : le texte affiché dans l'onglet."""
    return "; ".join(
        f"{p['duree_min']}:{p['kwh']:.2f}" + (":chauffe" if p["chauffe"] else "")
        for p in phases
    )


def _profil_vaisselle():
    """Profil du lave-vaisselle, d'après ses phases."""
    try:
        phases = lire_phases(get_reglage("machine_vaisselle_phases"))
    except ValueError:
        phases = lire_phases(DEFAUTS["machine_vaisselle_phases"])
    chauffes = [p for p in phases if p["chauffe"]]
    return {
        "type": "vaisselle",
        "libelle": dict(TYPES_MACHINE)["vaisselle"],
        "appareil": APPAREILS["vaisselle"],
        "duree_min": sum(p["duree_min"] for p in phases),
        "kwh": sum(p["kwh"] for p in phases),
        "chauffe_min": sum(p["duree_min"] for p in chauffes),
        "chauffe_kwh": sum(p["kwh"] for p in chauffes),
        "phases": phases,
        "pointe_kw": max(0.1, _float("machine_vaisselle_pointe_kw")),
    }


def profil_machine(type_cycle):
    """Profil de consommation d'un cycle : « normal », « court » ou « vaisselle ».

    Le lave-vaisselle est décrit phase par phase (voir ``_profil_vaisselle``) ;
    les cycles du lave-linge par les deux phases ci-dessous.

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
    if type_cycle == "vaisselle":
        return _profil_vaisselle()
    prefixe = f"machine_{type_cycle}_"
    duree = max(1, _int(prefixe + "duree"))
    kwh = max(0.0, _float(prefixe + "kwh"))
    return {
        "type": type_cycle,
        "libelle": dict(TYPES_MACHINE)[type_cycle],
        "appareil": APPAREILS[type_cycle],
        "duree_min": duree,
        "kwh": kwh,
        "chauffe_min": min(duree, max(0, _int(prefixe + "chauffe_min"))),
        "chauffe_kwh": min(kwh, max(0.0, _float(prefixe + "chauffe_kwh"))),
    }


def pointe_machine_kw():
    """Puissance de la résistance du lave-linge pendant la chauffe (kW)."""
    return max(0.1, _float("machine_pointe_kw"))


def plage_machines():
    """Plage où un lancement à la main est possible : (« HH:MM », « HH:MM »)."""
    return _heure("machine_plage_debut"), _heure("machine_plage_fin")


def pause_machines_min():
    """Délai entre la fin d'un cycle et le lancement du suivant (minutes)."""
    return max(0, _int("machine_pause_min"))


def tolerance_machines_cts():
    """Surcoût admis par cycle pour ne pas attendre le meilleur créneau (centimes).

    Le planificateur classe les créneaux au dixième de centime — plus fin
    que l'erreur d'une prévision solaire. Avec une tolérance, deux créneaux
    qui se valent à ce prix près sont tenus pour équivalents, et c'est le
    réglage « ajustement » qui choisit entre eux. À 0, le créneau le moins
    cher gagne toujours, comme avant.
    """
    return max(0.0, _float("machine_tolerance_cts"))


def machines_demandees():
    """Nombre de cycles voulus dans la journée : ``{"normal": n, "court": n, "vaisselle": n}``."""
    demandes = {}
    for type_cycle, _libelle in TYPES_MACHINE:
        try:
            n = int(float(get_setting(f"machines_{type_cycle}", module=MODULE, default="0")))
        except (TypeError, ValueError):
            n = 0
        demandes[type_cycle] = max(0, min(MAX_MACHINES, n))
    return demandes


def set_machines_demandees(normal=None, court=None, vaisselle=None):
    """Enregistre le nombre de cycles voulus ; ``None`` laisse la valeur en place.

    Lève ``ValueError`` si une valeur n'est pas un entier lisible : appelée
    depuis un scénario, une saisie fantaisiste doit arrêter l'action avec un
    message clair plutôt que de passer pour un zéro.
    """
    for type_cycle, valeur in (("normal", normal), ("court", court), ("vaisselle", vaisselle)):
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


def machines_optimise():
    """État du switch « Optimisé » des machines. « on » par défaut.

    - « on » : chaque créneau de jour est comparé aux heures creuses, et la
      machine est conseillée la nuit si elle y coûte moins ;
    - « off » : les machines sont placées dans la plage de lancement, sans
      regarder les heures creuses.

    Un jour rouge, le calcul ne tient pas compte de ce switch (voir
    ``machines.calculer``) : l'état enregistré, lui, ne change pas.
    """
    valeur = str(get_setting(CLE_MACHINES_OPTIMISE, module=MODULE, default="oui"))
    return valeur.strip().lower() not in ("non", "false", "0", "off")


def set_machines_optimise(actif):
    """Enregistre l'état du switch « Optimisé » et retourne l'état lu."""
    set_setting(CLE_MACHINES_OPTIMISE, "oui" if actif else "non", module=MODULE)
    return machines_optimise()


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
    # Le plan des machines n'est pas refait ici : il ne change que quand
    # on le demande (bouton « Calculer »), jamais tout seul.
    return resultat
