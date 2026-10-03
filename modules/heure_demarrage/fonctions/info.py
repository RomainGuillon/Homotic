# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

"""Fonctions d'INFO du module Heure de démarrage (contrat INFOS).

Ces fonctions ne calculent RIEN : elles lisent le dernier calcul mémorisé
(``calcul.dernier_resultat()``). C'est volontaire — une info qui recalculait
à chaque lecture donnait une heure différente à chaque minute, puisque le
calcul ne retient que les créneaux à venir. Un déclencheur « heure calculée »
courait alors après une heure qui reculait devant lui.

Pour rafraîchir ces valeurs : action de scénario « recalculer » (ou le bouton
Recalculer de l'onglet).
"""

from . import api, calcul, machines


def heure_demarrage():
    """Heure conseillée pour démarrer le chauffe-eau (HH:MM)."""
    return calcul.dernier_resultat().get("heure")


def mode_retenu():
    """« solaire » (créneau du jour) ou « nuit » (heures creuses)."""
    return calcul.dernier_resultat().get("mode")


def duree_chauffe_min():
    """Durée de chauffe retenue selon la saison (minutes)."""
    return api.duree_chauffe_min()


def saison():
    """« ete » ou « hiver » (switchs exclusifs Été/Hiver, sinon date)."""
    return api.saison()


def gain_estime_eur():
    """Écart de coût entre chauffe de jour et chauffe de nuit (€)."""
    g = calcul.dernier_resultat().get("gain")
    return round(g, 3) if g is not None else None


def surplus_creneau_kwh():
    """Surplus solaire prévu sur le meilleur créneau (kWh)."""
    c = calcul.dernier_resultat().get("creneau")
    return round(c["surplus_kwh"], 2) if c else None


def calcul_du_jour():
    """« oui » si le dernier calcul date d'aujourd'hui, sinon « non ».

    Utile en condition : ne pas démarrer la chauffe sur une heure calculée
    la veille.
    """
    r = calcul.dernier_resultat()
    return "non" if (r.get("jamais_calcule") or r.get("perime")) else "oui"


def heure_calcul():
    """Heure du dernier calcul (HH:MM), ou None si jamais calculé."""
    quand = calcul.dernier_resultat().get("quand")
    return quand.strftime("%H:%M") if quand else None


def creneau_retenu():
    """Créneau de chauffe retenu (liaison entre modules, type « objet »).

    ``{"heure": "13:30", "duree_min": 60, "mode": "solaire",
       "forcee": False, "perime": False}`` — ou ``None`` si aucune heure
    n'est retenue. ``forcee`` signale une heure saisie à la main, ``perime``
    un calcul qui date d'un autre jour : au consommateur de décider ce qu'il
    en fait.
    """
    r = calcul.dernier_resultat()
    heure = str(r.get("heure") or "").strip()
    if not heure:
        return None
    try:
        duree = int(r.get("duree_min") or 60)
    except (TypeError, ValueError):
        duree = 60
    return {
        "heure": heure,
        "duree_min": duree,
        "mode": r.get("mode") or "",
        "forcee": bool(r.get("heure_forcee")),
        "perime": bool(r.get("perime")),
    }


def _plan_machines():
    """Plan des machines du jour, ou ``None`` s'il n'y en a pas d'utilisable.

    Un plan calculé la veille ne dit rien d'aujourd'hui : mieux vaut une
    info vide qu'une heure qui déclencherait un scénario à tort.
    """
    plan = machines.dernier_resultat()
    if plan["jamais_calcule"] or plan["perime"]:
        return None
    return plan


def prochaine_machine():
    """Heure de lancement de la prochaine machine (HH:MM), sinon None."""
    plan = _plan_machines()
    return plan["prochaine"]["heure"] if plan and plan["prochaine"] else None


def machines_restantes():
    """Nombre de machines du plan du jour qui restent à lancer."""
    plan = _plan_machines()
    return plan["restantes"] if plan else 0


def plan_machines():
    """Machines restant à lancer : « 11:00 normal · 15:30 normal · 22:00 court (HC) ».

    Comme à l'écran, une machine dont l'heure est passée n'y figure plus.
    """
    plan = _plan_machines()
    if not plan:
        return None
    return " · ".join(
        f"{c['heure']} {c['type']}" + (" (HC)" if c.get("conseil") == "hc" else "")
        for c in plan["affichees"] if c.get("heure")
    ) or None


INFOS = [
    {"nom": "creneau_retenu", "type": "objet",
     "description": "Créneau de chauffe retenu (heure, durée, mode)"},
    {"nom": "heure_demarrage", "description": "Heure conseillée de démarrage (HH:MM)"},
    {"nom": "mode_retenu", "description": "Mode retenu (solaire/nuit)"},
    {"nom": "duree_chauffe_min", "description": "Durée de chauffe retenue (min)"},
    {"nom": "saison", "description": "Saison active (ete/hiver)"},
    {"nom": "gain_estime_eur", "description": "Écart de coût jour/nuit (€)"},
    {"nom": "surplus_creneau_kwh", "description": "Surplus solaire sur le créneau (kWh)"},
    {"nom": "calcul_du_jour",
     "description": "Le dernier calcul date-t-il d'aujourd'hui ? (oui/non)"},
    {"nom": "heure_calcul", "description": "Heure du dernier calcul (HH:MM)"},
    {"nom": "prochaine_machine",
     "description": "Heure de lancement de la prochaine machine (HH:MM)"},
    {"nom": "machines_restantes", "description": "Machines restant à lancer aujourd'hui"},
    {"nom": "plan_machines", "description": "Machines restant à lancer (texte)"},
]


def build_info_entries():
    return [
        {"type": "valeur", **e, "fonction": f"fonctions.info.{e['nom']}"} for e in INFOS
    ]
