# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

"""Heures de lancement des machines (lave-linge lancé à la main).

On indique combien de cycles on veut faire dans la journée — normaux ou
courts — et le module propose une heure de lancement pour chacun, là où le
solaire en couvre le plus.

**Le chauffe-eau est prioritaire.** Son créneau n'est jamais recalculé ici :
on lit le dernier calcul mémorisé (``calcul.dernier_resultat``) et on retire
sa consommation de la production prévue. Les machines se placent sur ce qui
reste. Une machine ne tourne en même temps que le ballon que si le solaire
couvre les deux ; sinon elle passe avant ou après.

Le surplus à chaque instant :

    surplus = production prévue − talon de la maison − chauffe-eau

**Un cycle a deux phases**, qui ne pèsent pas pareil face au solaire :

- la *chauffe* de l'eau, en début de cycle : la résistance tire sa pleine
  puissance par salves. Une moyenne sur 30 minutes la ferait passer pour
  couverte par un surplus qui ne l'est pas : pendant une salve, le solaire
  ne fournit que ``surplus / pointe`` de ce qui est tiré, le reste vient du
  réseau. C'est cette fraction qui est comptée ;
- le *reste* du cycle (brassage, rinçage, essorage) : faible et continu,
  couvert dès que le surplus dépasse sa puissance.

Pour chaque départ possible (toutes les 10 minutes dans la plage de
lancement), on chiffre ce qu'il faudrait acheter au réseau. On retient les
départs qui coûtent le moins, sans chevauchement et avec une pause entre
deux cycles — il n'y a qu'une machine. À coût égal, le réglage
« ajustement » du module départage, comme pour le ballon : au plus tôt
(« faible ») ou au plus fort de la production (« max »).

**Heures creuses** : le coût de chaque cycle en journée est comparé à celui
du même cycle acheté en entier en heures creuses. Si la nuit est moins
chère, le module le dit — c'est le cas des journées sans soleil, et presque
toujours des jours rouges.

Comme pour le ballon, **rien n'est recalculé à l'affichage** : le plan est
mémorisé (réglage ``machines_dernier_calcul``) et ne change que sur demande.
Un nouveau calcul dans la journée garde les cycles dont l'heure est passée
— ils sont supposés lancés — et ne replace que ceux qui restent à faire.
"""

import json
import math
from datetime import datetime, timedelta

from core.services import get_setting, journal, set_setting

from . import api, calcul

MODULE = api.MODULE
CLE_DERNIER = "machines_dernier_calcul"

# Un départ possible toutes les 10 minutes : assez fin pour caler un cycle
# juste après le ballon, assez gros pour donner des heures qu'on retient.
PAS_DEPART_MIN = 10

# Pas des prévisions de production (les points sont datés au milieu du pas).
PAS_PREVISION_MIN = 30

_JOUR_MIN = 24 * 60
# Le plan est tenu à la minute, sur deux jours : un cycle lancé tard dans la
# soirée déborde sur le lendemain, où plus aucune production n'est prévue.
_HORIZON_MIN = 2 * _JOUR_MIN


# ----------------------------------------------------------------------
# Outils de temps
# ----------------------------------------------------------------------

def _maintenant():
    """L'instant présent, avec le fuseau local. Point d'entrée unique de
    l'horloge dans ce fichier : c'est lui que les tests figent."""
    return datetime.now().astimezone()


def _minuit(maintenant):
    return maintenant.replace(hour=0, minute=0, second=0, microsecond=0)


def _minute(instant, minuit):
    """Minute écoulée depuis ``minuit`` (peut être négative ou > 1440)."""
    return int(math.floor((instant - minuit).total_seconds() / 60))


def _minutes_hhmm(texte):
    heures, minutes = str(texte).split(":")
    return int(heures) * 60 + int(minutes)


def _en_heures_creuses(minute, hc_debut, hc_fin):
    """Vrai si la minute tombe en heures creuses (plage à cheval sur minuit gérée)."""
    heure = (minute % _JOUR_MIN) / 60.0
    if hc_debut > hc_fin:  # ex : 22 h -> 6 h
        return heure >= hc_debut or heure < hc_fin
    return hc_debut <= heure < hc_fin


# ----------------------------------------------------------------------
# Planification — fonction pure, sans base ni réseau
# ----------------------------------------------------------------------

def _courbes(maintenant, points, talon_kw, ballon):
    """Surplus disponible et puissance du ballon, minute par minute.

    Retourne ``(surplus, ballon_kw)``, deux listes indexées par la minute
    écoulée depuis minuit.
    """
    minuit = _minuit(maintenant)
    production = [0.0] * _HORIZON_MIN
    for instant, kw in points or []:
        debut = _minute(instant, minuit) - PAS_PREVISION_MIN // 2
        for m in range(max(0, debut), min(_HORIZON_MIN, debut + PAS_PREVISION_MIN)):
            production[m] = max(0.0, float(kw or 0.0))

    ballon_kw = [0.0] * _HORIZON_MIN
    if ballon:
        debut = _minute(ballon["debut"], minuit)
        fin = _minute(ballon["fin"], minuit)
        for m in range(max(0, debut), min(_HORIZON_MIN, fin)):
            ballon_kw[m] = ballon["kw"]

    surplus = [
        max(0.0, production[m] - talon_kw - ballon_kw[m]) for m in range(_HORIZON_MIN)
    ]
    return surplus, ballon_kw


def _evaluer(depart, profil, pointe_kw, surplus, ballon_kw, prix):
    """Bilan d'un cycle lancé à la minute ``depart``, ou ``None`` s'il est exclu.

    Exclu = il tournerait en même temps que le chauffe-eau sans que le
    solaire couvre les deux. Le ballon est prioritaire : on ne lui ajoute
    pas une machine que le réseau devrait alimenter.
    """
    duree = profil["duree_min"]
    chauffe_min = profil["chauffe_min"]
    reste_min = duree - chauffe_min

    # Puissances moyennes de chaque phase. La chauffe est tirée par salves à
    # la puissance de la résistance, jamais moins que sa propre moyenne.
    p_chauffe = profil["chauffe_kwh"] / (chauffe_min / 60.0) if chauffe_min else 0.0
    pointe = max(pointe_kw, p_chauffe)
    p_reste = (
        (profil["kwh"] - profil["chauffe_kwh"]) / (reste_min / 60.0) if reste_min else 0.0
    )

    achat = cout = marge = 0.0
    avec_ballon = False
    for j in range(duree):
        m = depart + j
        dispo = surplus[m] if m < _HORIZON_MIN else 0.0
        if j < chauffe_min:
            seuil = pointe
            achat_min = (p_chauffe / 60.0) * (1.0 - min(1.0, dispo / pointe))
        else:
            seuil = p_reste
            achat_min = max(0.0, p_reste - dispo) / 60.0

        if m < _HORIZON_MIN and ballon_kw[m] > 0:
            avec_ballon = True
            if dispo + 1e-9 < seuil:
                return None

        achat += achat_min
        marge += dispo / 60.0
        if prix is not None:
            cout += achat_min * prix[m % _JOUR_MIN]

    return {
        "import_kwh": achat,
        "cout_jour": cout if prix is not None else None,
        "marge_kwh": marge,
        "avec_ballon": avec_ballon,
    }


def planifier(*, maintenant, points, profils, demandes, pointe_kw, talon_kw,
              ballon=None, plage=("08:00", "20:00"), pause_min=30, tarifs=None,
              ajustement="faible", deja=()):
    """Place les cycles demandés sur la journée. Fonction pure.

    - ``maintenant`` : instant du calcul (datetime avec fuseau) ; aucun
      départ n'est proposé dans le passé ;
    - ``points`` : prévision de production ``[(datetime, kW)]`` au pas de
      30 minutes ;
    - ``profils`` : ``{"normal": {...}, "court": {...}}``, voir
      ``api.profil_machine`` ;
    - ``demandes`` : nombre de cycles voulus **dans la journée**, par type ;
    - ``ballon`` : ``{"debut", "fin", "kw"}`` — le créneau du chauffe-eau à
      respecter, ou ``None`` ;
    - ``plage`` : heures entre lesquelles un lancement est possible ;
    - ``tarifs`` : ``{"hp", "hc", "hc_debut", "hc_fin"}`` (prix en €/kWh,
      bornes en heures), ou ``None`` — on classe alors sur l'énergie achetée ;
    - ``deja`` : cycles d'un plan précédent déjà lancés. Ils sont conservés
      tels quels, comptent dans la demande, et la machine n'est libre
      qu'après eux.

    Retourne ``{"cycles": [...], "non_places": {type: n}}``. Les cycles sont
    triés par heure ; ceux qui n'ont pas trouvé de place ferment la liste,
    avec ``debut`` à ``None``.
    """
    minuit = _minuit(maintenant)
    surplus, ballon_kw = _courbes(maintenant, points, talon_kw, ballon)

    prix = None
    if tarifs:
        prix = [
            tarifs["hc"] if _en_heures_creuses(m, tarifs["hc_debut"], tarifs["hc_fin"])
            else tarifs["hp"]
            for m in range(_JOUR_MIN)
        ]

    # --- Ce qui est déjà lancé, et ce qui reste à placer ---
    conserves = []
    restant = {t: max(0, int(n)) for t, n in demandes.items()}
    for cycle in sorted(deja, key=lambda c: c["debut"]):
        if restant.get(cycle["type"], 0) > 0:
            restant[cycle["type"]] -= 1
            conserves.append({**cycle, "lancee": True})

    # --- Départs possibles ---
    premier = max(_minutes_hhmm(plage[0]), _minute(maintenant, minuit))
    for cycle in conserves:
        premier = max(premier, _minute(cycle["fin"], minuit) + pause_min)
    premier = -(-premier // PAS_DEPART_MIN) * PAS_DEPART_MIN  # arrondi au pas supérieur
    departs = list(range(premier, _minutes_hhmm(plage[1]) + 1, PAS_DEPART_MIN))

    types = [t for t in profils if restant.get(t, 0) > 0]
    bilans = {
        t: [_evaluer(d, profils[t], pointe_kw, surplus, ballon_kw, prix) for d in departs]
        for t in types
    }

    def note(bilan, depart):
        """(critère principal, départage), en entiers.

        Le critère principal est arrondi au dixième de centime (ou au
        centième de kWh sans tarifs) : en dessous, l'écart entre deux
        créneaux est plus petit que l'erreur de la prévision, et c'est au
        départage de choisir — sinon « au plus tôt » ne jouerait jamais.
        """
        if prix is not None:
            principal = round(bilan["cout_jour"] * 1000)
        else:
            principal = round(bilan["import_kwh"] * 100)
        if ajustement == "max":
            departage = -round(bilan["marge_kwh"] * 1000)
        else:
            departage = depart
        return principal, departage

    def suivant(i, type_cycle):
        """Indice du premier départ possible après un cycle lancé à departs[i]."""
        libre = departs[i] + profils[type_cycle]["duree_min"] + pause_min
        j = i + 1
        while j < len(departs) and departs[j] < libre:
            j += 1
        return j

    # --- Recherche du meilleur plan ---
    # État : (indice du départ examiné, cycles restant à placer par type).
    # Valeur : (cycles non placés, coût, départage), à minimiser dans cet
    # ordre — placer tous les cycles passe avant tout.
    memo = {}

    def resoudre(i, reste):
        if not any(reste):
            return (0, 0, 0), None
        if i >= len(departs):
            return (sum(reste), 0, 0), None
        cle = (i, reste)
        if cle in memo:
            return memo[cle]

        meilleur = (resoudre(i + 1, reste)[0], None)  # ne rien lancer ici
        for k, type_cycle in enumerate(types):
            bilan = bilans[type_cycle][i]
            if reste[k] == 0 or bilan is None:
                continue
            apres = reste[:k] + (reste[k] - 1,) + reste[k + 1:]
            suite, _ = resoudre(suivant(i, type_cycle), apres)
            principal, departage = note(bilan, departs[i])
            total = (suite[0], suite[1] + principal, suite[2] + departage)
            if total < meilleur[0]:
                meilleur = (total, k)
        memo[cle] = meilleur
        return meilleur

    # --- Lecture du plan ---
    places = []
    i, reste = 0, tuple(restant[t] for t in types)
    while any(reste) and i < len(departs):
        _valeur, choix = resoudre(i, reste)
        if choix is None:
            i += 1
            continue
        type_cycle = types[choix]
        places.append((departs[i], type_cycle, bilans[type_cycle][i]))
        reste = reste[:choix] + (reste[choix] - 1,) + reste[choix + 1:]
        i = suivant(i, type_cycle)

    cycles = list(conserves)
    for depart, type_cycle, bilan in places:
        profil = profils[type_cycle]
        debut = minuit + timedelta(minutes=depart)
        cycles.append(_cycle(profil, tarifs, debut, bilan))
    cycles.sort(key=lambda c: c["debut"])

    non_places = dict(zip(types, reste))
    for type_cycle, nombre in non_places.items():
        for _ in range(nombre):
            cycles.append(_cycle(profils[type_cycle], tarifs, None, None))

    return {"cycles": cycles, "non_places": {t: n for t, n in non_places.items() if n}}


def _cycle(profil, tarifs, debut, bilan):
    """Fiche d'un cycle : son créneau de jour, ses coûts, et le conseil.

    ``debut`` à ``None`` : le cycle n'a pas trouvé de place dans la plage de
    lancement. Il ne reste alors que les heures creuses — ou un autre jour.
    """
    kwh = profil["kwh"]
    cout_hc = kwh * tarifs["hc"] if tarifs else None
    heure_hc = f"{int(tarifs['hc_debut']) % 24:02d}:00" if tarifs else None

    cycle = {
        "type": profil["type"],
        "libelle": profil["libelle"],
        "duree_min": profil["duree_min"],
        "kwh": kwh,
        "debut": debut,
        "fin": debut + timedelta(minutes=profil["duree_min"]) if debut else None,
        "import_kwh": None,
        "solaire_kwh": None,
        "cout_jour": None,
        "cout_hc": cout_hc,
        "ecart": None,
        "avec_ballon": False,
        "lancee": False,
    }

    if bilan is None:
        cycle["conseil"] = "hc" if tarifs else "aucun"
        cycle["heure"] = heure_hc
        return cycle

    cycle.update({
        "import_kwh": bilan["import_kwh"],
        "solaire_kwh": max(0.0, kwh - bilan["import_kwh"]),
        "cout_jour": bilan["cout_jour"],
        "avec_ballon": bilan["avec_ballon"],
    })
    # La nuit ne l'emporte que si elle est strictement moins chère : à coût
    # égal, autant lancer de jour, quand on est là pour étendre le linge.
    if cout_hc is not None and cout_hc < bilan["cout_jour"] - 1e-9:
        cycle["conseil"] = "hc"
        cycle["heure"] = heure_hc
    else:
        cycle["conseil"] = "jour"
        cycle["heure"] = debut.astimezone().strftime("%H:%M")
    if cout_hc is not None:
        cycle["ecart"] = abs(bilan["cout_jour"] - cout_hc)
    return cycle


# ----------------------------------------------------------------------
# Données d'entrée
# ----------------------------------------------------------------------

def _prevision(maintenant):
    """Prévision de production du jour : ``(points, erreur)``.

    Même besoin que pour le ballon (``prevision_pv``) : ce module ne sait
    pas qui fournit la courbe.
    """
    from core.liaisons import lire_besoin

    points, err = lire_besoin(MODULE, "prevision_pv")
    if err:
        return [], err
    if not points:
        return [], "prévisions indisponibles"
    jour = maintenant.date()
    return [(t, kw) for t, kw in points if t.astimezone().date() == jour], ""


def _tarifs():
    """Tarifs du jour utiles au calcul, ou ``None`` s'ils sont inconnus.

    ``{"hp", "hc", "hc_debut", "hc_fin", "couleur", "libelle"}``. L'objet
    vient du besoin ``tarifs_jour`` ; un fournisseur qui ne donnerait pas
    tous les champs attendus vaut « tarifs inconnus », pas une erreur.
    """
    from core.liaisons import lire_besoin

    tarifs, _err = lire_besoin(MODULE, "tarifs_jour")
    try:
        couleur = tarifs["couleur"]
        prix = tarifs["prix"][couleur]
        return {
            "hp": float(prix["HP"]),
            "hc": float(prix["HC"]),
            "hc_debut": int(tarifs.get("hc_debut", 22)),
            "hc_fin": int(tarifs.get("hc_fin", 6)),
            "couleur": couleur,
            "libelle": (tarifs.get("libelles") or {}).get(couleur, couleur),
        }
    except (TypeError, KeyError, ValueError, AttributeError):
        return None


def _creneau_ballon(maintenant):
    """Créneau du chauffe-eau à respecter aujourd'hui, ou ``None``.

    Lu dans le dernier calcul mémorisé — jamais recalculé ici, sinon
    demander une machine déplacerait l'heure du ballon. L'heure retenue est
    celle que lisent les scénarios : une heure forcée à la main prime donc
    sur le calcul.
    """
    r = calcul.dernier_resultat()
    heure = str(r.get("heure") or "").strip()
    if not heure:
        return None
    try:
        debut = _minuit(maintenant) + timedelta(minutes=_minutes_hhmm(heure))
        duree = int(r.get("duree_min") or api.duree_chauffe_min())
        besoin = float(r.get("besoin_kwh") or api.conso_chauffe_eau())
    except (TypeError, ValueError):
        return None
    if duree <= 0:
        return None
    return {
        "debut": debut,
        "fin": debut + timedelta(minutes=duree),
        "kw": besoin / (duree / 60.0),
        "kwh": besoin,
        "mode": r.get("mode") or "",
        "perime": bool(r.get("perime")),
        "forcee": bool(r.get("heure_forcee")),
        # Chauffe finie (ou faite la nuit dernière) : elle ne prend plus rien
        # à la production qui reste, mais on le dit dans le détail.
        "termine": debut + timedelta(minutes=duree) <= maintenant,
    }


def _cycles_lances(maintenant):
    """Cycles du plan du jour dont l'heure de lancement est passée.

    On suppose que le plan a été suivi : c'est ce qui permet de recalculer
    en cours de journée sans reproposer une machine déjà faite. Un cycle
    conseillé en heures creuses n'est pas « lancé » tant que la nuit n'est
    pas là. Le bouton « Tout replanifier » ignore cette mémoire.
    """
    precedent = dernier_resultat()
    if precedent["jamais_calcule"] or precedent["perime"]:
        return []
    return [
        c for c in precedent["cycles"]
        if c.get("debut") and c.get("conseil") == "jour" and c["debut"] < maintenant
    ]


# ----------------------------------------------------------------------
# Calcul, mémoire et lecture
# ----------------------------------------------------------------------

def calculer(tracer=False, tout_replanifier=False):
    """Calcule le plan des machines, le mémorise et le retourne.

    ``tracer`` : écrit le détail dans le Journal. ``tout_replanifier`` :
    repart de zéro, sans supposer lancés les cycles dont l'heure est passée.
    """
    maintenant = _maintenant()
    demandes = api.machines_demandees()
    profils = {t: api.profil_machine(t) for t, _libelle in api.TYPES_MACHINE}
    plage = api.plage_machines()
    ballon = _creneau_ballon(maintenant)
    tarifs = _tarifs()
    points, erreur = _prevision(maintenant)

    plan = planifier(
        maintenant=maintenant,
        points=points,
        profils=profils,
        demandes=demandes,
        pointe_kw=api.pointe_machine_kw(),
        talon_kw=api.conso_min_maison(),
        ballon=ballon,
        plage=plage,
        pause_min=api.pause_machines_min(),
        tarifs=tarifs,
        ajustement=api.ajustement(),
        deja=[] if tout_replanifier else _cycles_lances(maintenant),
    )

    resultat = {
        "cycles": plan["cycles"],
        "non_places": plan["non_places"],
        "demandes": demandes,
        "profils": profils,
        "pointe_kw": api.pointe_machine_kw(),
        "talon_kwh_h": api.conso_min_maison(),
        "plage": list(plage),
        "pause_min": api.pause_machines_min(),
        "ballon": ballon,
        "tarifs": tarifs,
        "ajustement": api.ajustement(),
        "erreur": erreur,
    }
    resultat["detail"] = detail_texte(resultat)
    if tracer:
        journal(
            "Calcul des machines — " + " ".join(resultat["detail"]), module=MODULE
        )

    charge = calcul._serialiser(resultat)
    charge["quand"] = maintenant.isoformat()
    set_setting(CLE_DERNIER, json.dumps(charge), module=MODULE)
    return dernier_resultat()


def recalculer_si_demande():
    """Refait le plan si des cycles sont demandés, sinon ne touche à rien.

    Appelé après chaque recalcul du chauffe-eau : son créneau vient
    peut-être de bouger, et les machines se placent autour de lui.
    """
    if sum(api.machines_demandees().values()) == 0:
        return None
    return calculer(tracer=True)


def _resultat_vide():
    """Plan neutre : toutes les clés lues par les gabarits sont présentes."""
    return {
        "cycles": [], "non_places": {}, "demandes": api.machines_demandees(),
        "ballon": None, "tarifs": None, "erreur": "", "detail": [],
        "quand": None, "perime": False, "jamais_calcule": True,
    }


def dernier_resultat():
    """Dernier plan mémorisé, complété de ce qui dépend de l'heure qu'il est.

    Source unique pour le tableau de bord, l'onglet et les infos. Ajoute à
    chaque cycle ``passe`` (son heure de lancement est derrière nous) et, au
    plan, ``prochaine`` (le prochain cycle à lancer), ``restantes`` et les
    totaux. ``perime`` signale un plan calculé un autre jour : ses heures ne
    veulent alors plus rien dire.
    """
    raw = get_setting(CLE_DERNIER, module=MODULE)
    if not raw:
        return _completer(_resultat_vide())
    try:
        resultat = calcul._deserialiser(json.loads(raw))
        quand = resultat.get("quand")
        resultat["quand"] = datetime.fromisoformat(quand) if quand else None
        resultat["cycles"]  # un plan sans cycles n'en est pas un
    except (ValueError, TypeError, AttributeError, KeyError):
        return _completer(_resultat_vide())

    resultat["jamais_calcule"] = False
    resultat["perime"] = bool(
        resultat["quand"]
        and resultat["quand"].astimezone().date() != _maintenant().date()
    )
    return _completer(resultat)


def _centimes(euros):
    return None if euros is None else euros * 100.0


def _completer(resultat):
    maintenant = _maintenant()
    tarifs = resultat.get("tarifs")
    resultat["heure_hc"] = (
        f"{int(tarifs['hc_debut']) % 24:02d}:00" if tarifs else None
    )

    a_venir = []
    total = {"kwh": 0.0, "solaire_kwh": 0.0, "import_kwh": 0.0, "cout": 0.0}
    chiffrable = bool(resultat["cycles"])
    for numero, cycle in enumerate(resultat["cycles"], start=1):
        cycle["numero"] = numero
        de_nuit = cycle.get("conseil") != "jour"
        # Strictement passé : un cycle prévu pour maintenant reste à lancer.
        cycle["passe"] = bool(
            not de_nuit and cycle.get("debut") and cycle["debut"] < maintenant
        )
        if not cycle["passe"] and cycle.get("heure"):
            a_venir.append(cycle)

        # Totaux sur l'option conseillée : de jour, la part solaire est
        # gratuite ; de nuit, tout le cycle est acheté en heures creuses.
        total["kwh"] += cycle["kwh"]
        if de_nuit:
            total["import_kwh"] += cycle["kwh"]
            cout = cycle.get("cout_hc")
        else:
            total["solaire_kwh"] += cycle.get("solaire_kwh") or 0.0
            total["import_kwh"] += cycle.get("import_kwh") or 0.0
            cout = cycle.get("cout_jour")
        if cout is None:
            chiffrable = False
        else:
            total["cout"] += cout

        # Un cycle coûte quelques centimes : c'est l'unité lisible.
        cycle["cout_c"] = _centimes(cout)
        cycle["cout_jour_c"] = _centimes(cycle.get("cout_jour"))
        cycle["cout_hc_c"] = _centimes(cycle.get("cout_hc"))
        cycle["ecart_c"] = _centimes(cycle.get("ecart"))

    if not chiffrable:
        total["cout"] = None
    total["cout_c"] = _centimes(total["cout"])
    resultat["total"] = total
    resultat["restantes"] = len(a_venir)
    resultat["a_des_passees"] = any(c["passe"] for c in resultat["cycles"])
    # Les cycles de jour d'abord, dans l'ordre ; ceux de la nuit ensuite.
    a_venir.sort(key=lambda c: (c.get("conseil") != "jour", c.get("debut") or maintenant))
    resultat["prochaine"] = (
        a_venir[0] if a_venir and not resultat.get("perime") else None
    )
    return resultat


# ----------------------------------------------------------------------
# Explication lisible (Journal et onglet)
# ----------------------------------------------------------------------

def _hm(instant):
    return instant.astimezone().strftime("%H:%M")


def detail_texte(r):
    """Explication du plan, ligne par ligne : les données, puis chaque cycle."""
    lignes = []

    demandes = [
        f"{n} × {p['libelle'].lower()} ({p['duree_min']} min, {p['kwh']:.2f} kWh "
        f"dont {p['chauffe_kwh']:.2f} kWh de chauffe)"
        for t, p in r["profils"].items()
        if (n := r["demandes"].get(t, 0))
    ]
    if not demandes:
        return ["Aucune machine demandée aujourd'hui."]
    lignes.append(
        "Données : " + ", ".join(demandes)
        + f" ; résistance {r['pointe_kw']:.2f} kW ; talon maison "
        f"{r['talon_kwh_h']:.2f} kWh/h ; lancement possible de {r['plage'][0]} à "
        f"{r['plage'][1]}, {r['pause_min']} min entre deux cycles."
    )

    ballon = r.get("ballon")
    if ballon and ballon.get("termine"):
        # En mode « nuit », l'heure retenue désigne la chauffe de la nuit
        # qui vient : la dire « passée » serait faux.
        quand = (
            f"prévue en heures creuses ({_hm(ballon['debut'])})"
            if ballon.get("mode") == "nuit"
            else f"de {_hm(ballon['debut'])}–{_hm(ballon['fin'])} déjà passée"
        )
        lignes.append(
            f"Chauffe-eau : chauffe {quand}, toute la production restante est "
            f"disponible."
        )
    elif ballon:
        origine = (
            "heure forcée à la main" if ballon["forcee"]
            else "calcul de la veille" if ballon["perime"]
            else "dernier calcul"
        )
        lignes.append(
            f"Chauffe-eau prioritaire ({origine}) : {_hm(ballon['debut'])}–"
            f"{_hm(ballon['fin'])} à {ballon['kw']:.2f} kW, retirés de la "
            f"production prévue."
        )
    else:
        lignes.append(
            "Chauffe-eau : aucune heure de démarrage connue, rien n'est retiré "
            "de la production prévue."
        )

    tarifs = r.get("tarifs")
    if tarifs:
        lignes.append(
            f"Tarifs du jour ({tarifs['libelle']}) : HP {tarifs['hp']:.4f} €/kWh, "
            f"HC {tarifs['hc']:.4f} €/kWh à partir de {int(tarifs['hc_debut']) % 24:02d}:00."
        )
    else:
        lignes.append(
            "Tarifs indisponibles : aucun coût chiffrable, les créneaux sont "
            "classés sur l'énergie achetée au réseau, sans comparaison avec "
            "les heures creuses."
        )

    for numero, c in enumerate(r["cycles"], start=1):
        nom = f"{c['libelle']} n° {numero}"
        if c.get("lancee"):
            lignes.append(
                f"{nom} : lancé à {_hm(c['debut'])} d'après le plan précédent, conservé."
            )
            continue
        if not c.get("debut"):
            suite = (
                f"heures creuses à partir de {c['heure']} ({c['cout_hc']:.3f} €)"
                if c.get("conseil") == "hc" else "à reporter"
            )
            lignes.append(
                f"{nom} : plus de créneau libre dans la plage de lancement → {suite}."
            )
            continue

        ligne = (
            f"{nom} : {_hm(c['debut'])}–{_hm(c['fin'])}, "
            f"{c['solaire_kwh']:.2f} kWh couverts par le solaire, "
            f"{c['import_kwh']:.2f} kWh achetés au réseau"
        )
        if c.get("avec_ballon"):
            ligne += " (en même temps que le chauffe-eau, le solaire couvre les deux)"
        if c.get("cout_hc") is not None:
            ligne += (
                f" → {c['cout_jour']:.3f} € de jour contre {c['cout_hc']:.3f} € "
                f"en heures creuses"
            )
        if c.get("conseil") == "hc":
            ligne += (
                f". DÉCISION : heures creuses, à partir de {c['heure']} "
                f"(écart {c['ecart']:.3f} €)."
            )
        else:
            ligne += f". DÉCISION : lancer à {c['heure']}."
        lignes.append(ligne)

    if r.get("erreur"):
        lignes.append(f"Remarque : {r['erreur']}.")
    return lignes
