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

**La tolérance** (réglage du profil, en centimes par cycle) élargit ce
« coût égal ». Le meilleur plan est cherché comme ci-dessus, puis chaque
cycle est déplacé parmi les créneaux qui ne coûtent pas plus que le sien,
tolérance comprise : au plus tôt, ou au plus fort de la production. Attendre
trois heures pour gagner un demi-centime sur une prévision n'a pas de sens.
Elle ne change jamais la décision : un cycle moins cher de jour que de nuit
(ou reporté à demain) le reste. À 0, rien n'est déplacé.

**Le switch « Optimisé »** décide de ce qu'on fait des heures creuses :

- sur « on », le coût de chaque cycle en journée est comparé à celui du même
  cycle acheté en entier en heures creuses. Si la nuit est moins chère, le
  module le dit — c'est le cas des journées sans soleil ;
- sur « off », les cycles sont placés dans la plage de lancement, sans
  regarder les heures creuses.

**Un jour rouge, le switch ne compte pas.** Le kWh d'heures pleines y coûte
plusieurs fois celui de la nuit : chaque cycle va là où il revient le moins
cher, sur la production ou en heures creuses.

**Plus de place dans la plage, switch sur « off »** : le cycle qui reste
n'est pas abandonné. Si demain est rouge, il passe en heures creuses ce
soir. Sinon on regarde la prévision de demain — chauffe-eau de demain
retiré, il reste prioritaire — et le cycle y est reporté s'il y coûte moins
que les heures creuses de ce soir. À défaut (pas de prévision, ou pas moins
cher) : heures creuses ce soir.

Tant que la couleur de demain n'est pas publiée, on ne peut pas exclure un
jour rouge : heures creuses ce soir là aussi — sauf s'il ne reste plus aucun
jour rouge à tirer cette saison, auquel cas demain est forcément bleu ou
blanc et il est chiffré au plus cher des deux.

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

# Code du jour rouge dans l'objet « tarifs_jour » (clés « couleur » et
# « couleur_demain »). C'est la seule couleur que ce fichier connaît : les
# autres ne sont pour lui que des clés de la grille de prix.
COULEUR_ROUGE = "RED"

# Pourquoi un cycle sans place aujourd'hui n'est pas reporté à demain — en
# clair, pour l'écran et le Journal. La clé est le « motif » posé par
# ``_reporter``.
_MOTIFS = {
    "demain_rouge": "demain est rouge",
    "demain_inconnu": "couleur de demain pas encore connue",
    "demain_sans_prevision": "pas de prévision pour demain",
    "demain_plein": "pas de créneau demain non plus",
    "demain_plus_cher": "demain ne coûterait pas moins",
}

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


def _cote(cout, plafond):
    """De quel côté d'un plafond tombe un coût : -1 dessous, 0 dessus, 1 au-delà.

    Mêmes marges que les comparaisons avec les heures creuses (``_cycle``,
    ``_reporter``) : c'est ce qui garantit qu'un cycle déplacé par la
    tolérance garde le conseil qu'il avait.
    """
    if plafond is None or cout < plafond - 1e-9:
        return -1
    return 0 if cout <= plafond + 1e-9 else 1


def planifier(*, maintenant, points, profils, demandes, pointe_kw, talon_kw,
              ballon=None, plage=("08:00", "20:00"), pause_min=30, tarifs=None,
              ajustement="faible", deja=(), comparer_hc=True, lendemain=None,
              tolerance=0.0, plafonds=None):
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
      qu'après eux ;
    - ``comparer_hc`` : vrai (switch « Optimisé » sur on, ou jour rouge), un
      cycle placé de jour est conseillé en heures creuses s'il y coûte
      moins. Faux, il reste à son créneau de jour ;
    - ``lendemain`` : ce qu'on sait de demain (voir ``_lendemain``), ou
      ``None``. Fourni, un cycle qui n'a plus de place aujourd'hui est
      examiné pour demain au lieu d'aller d'office en heures creuses ;
    - ``tolerance`` : surcoût admis par cycle, en euros, pour le déplacer
      hors de son meilleur créneau (voir ``assouplir`` plus bas). Sans
      tarifs, elle ne joue pas : il n'y a pas de coût à comparer ;
    - ``plafonds`` : ``{type: €}``, coût que la tolérance ne fait pas
      franchir à un cycle qui était en dessous. Par défaut, avec
      ``comparer_hc``, le prix du cycle en heures creuses.

    Un cycle déplacé par la tolérance porte ``meilleur_debut`` (le créneau
    le moins cher, qu'il a quitté) et ``surcout`` (ce que ça coûte, en €).

    Retourne ``{"cycles": [...], "non_places": {type: n}}``. Les cycles sont
    triés par heure ; ceux qui n'ont pas trouvé de place aujourd'hui ferment
    la liste — en heures creuses (``debut`` à ``None``), puis reportés à
    demain (``conseil`` à « demain »).
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

    # --- Tolérance ---
    def assouplir(places):
        """Déplace chaque cycle parmi les créneaux qui valent le sien.

        Le plan trouvé est le moins cher au dixième de centime près. Pour
        chaque cycle, dans l'ordre, on retient parmi les départs qui ne
        coûtent pas plus que le sien + la tolérance celui que l'ajustement
        préfère : le plus tôt (« faible ») ou le plus productif (« max »).

        Ce qui ne bouge pas : un départ exclu par le chauffe-eau le reste,
        la pause entre deux cycles est tenue (le cycle suivant n'a pas
        encore bougé, on lui laisse sa place), et un cycle ne passe pas
        au-dessus de son plafond s'il était en dessous.

        Retourne des ``(départ, type, bilan, départ d'origine, bilan
        d'origine)``.
        """
        souples = []
        libre = departs[0]
        for rang, (depart, type_cycle, bilan) in enumerate(places):
            duree = profils[type_cycle]["duree_min"]
            if rang + 1 < len(places):
                limite = places[rang + 1][0] - pause_min - duree
            else:
                limite = departs[-1]
            plafond = (plafonds or {}).get(type_cycle)
            cout = bilan["cout_jour"]
            retenu, cle_retenue = (depart, bilan), None
            for d, b in zip(departs, bilans[type_cycle]):
                if b is None or not libre <= d <= limite:
                    continue
                if b["cout_jour"] > cout + tolerance + 1e-9:
                    continue
                if _cote(b["cout_jour"], plafond) > _cote(cout, plafond):
                    continue
                if ajustement == "max":
                    # À production égale, le cycle reste où il était.
                    cle = (-round(b["marge_kwh"] * 1000), d != depart, d)
                else:
                    cle = (d,)
                if cle_retenue is None or cle < cle_retenue:
                    retenu, cle_retenue = (d, b), cle
            souples.append((retenu[0], type_cycle, retenu[1], depart, bilan))
            libre = retenu[0] + duree + pause_min
        return souples

    if tolerance > 0 and prix is not None and places:
        if plafonds is None and comparer_hc:
            plafonds = {t: profils[t]["kwh"] * tarifs["hc"] for t in types}
        places = assouplir(places)
    else:
        places = [(d, t, b, d, b) for d, t, b in places]

    cycles = list(conserves)
    for depart, type_cycle, bilan, depart_origine, bilan_origine in places:
        profil = profils[type_cycle]
        debut = minuit + timedelta(minutes=depart)
        cycle = _cycle(profil, tarifs, debut, bilan, comparer_hc)
        if depart != depart_origine:
            cycle["meilleur_debut"] = minuit + timedelta(minutes=depart_origine)
            cycle["surcout"] = max(
                0.0, bilan["cout_jour"] - bilan_origine["cout_jour"]
            )
        cycles.append(cycle)
    cycles.sort(key=lambda c: c["debut"])

    non_places = {t: n for t, n in zip(types, reste) if n}
    if lendemain is not None and tarifs and non_places:
        cycles += _reporter(
            non_places, profils, tarifs, lendemain,
            pointe_kw=pointe_kw, talon_kw=talon_kw, plage=plage,
            pause_min=pause_min, ajustement=ajustement, tolerance=tolerance,
        )
    else:
        for type_cycle, nombre in non_places.items():
            for _ in range(nombre):
                cycles.append(_cycle(profils[type_cycle], tarifs, None, None))

    return {"cycles": cycles, "non_places": non_places}


def _reporter(non_places, profils, tarifs, lendemain, **reglages):
    """Cycles sans place aujourd'hui, switch « Optimisé » sur off : où vont-ils ?

    - demain est rouge : heures creuses ce soir. Elles relèvent encore de
      la couleur d'aujourd'hui, et on ne parie pas sur le soleil d'un jour
      où chaque kWh acheté coûte plusieurs fois le prix de la nuit ;
    - la couleur de demain n'est pas encore publiée et il reste des jours
      rouges à tirer : heures creuses ce soir aussi, faute de pouvoir
      exclure un jour rouge (``lendemain["tarifs"]`` est alors ``None``) ;
    - sinon les cycles sont placés sur la prévision de demain, comme ils
      l'auraient été aujourd'hui (même plage, même pause, chauffe-eau de
      demain retiré). Chacun n'y est reporté que s'il y coûte strictement
      moins que les heures creuses de ce soir.

    Chaque cycle porte un ``motif`` qui dit laquelle de ces issues l'a
    emporté — c'est ce qu'affichent l'écran et le Journal.
    """
    def ce_soir(type_cycle, motif, essai=None):
        cycle = _cycle(profils[type_cycle], tarifs, None, None)
        cycle["motif"] = motif
        if essai:
            # Ce que demain aurait donné : gardé pour l'explication.
            cycle["demain_debut"] = essai["debut"]
            cycle["cout_demain"] = essai["cout_jour"]
        return cycle

    if lendemain.get("rouge"):
        motif = "demain_rouge"
    elif not lendemain.get("tarifs"):
        motif = "demain_inconnu"
    elif not lendemain.get("points"):
        motif = "demain_sans_prevision"
    else:
        motif = ""
    if motif:
        return [ce_soir(t, motif) for t, n in non_places.items() for _ in range(n)]

    essai = planifier(
        maintenant=lendemain["minuit"], points=lendemain["points"],
        profils=profils, demandes=non_places, ballon=lendemain.get("ballon"),
        tarifs=lendemain["tarifs"], comparer_hc=False,
        # La tolérance ne doit pas faire perdre son report à un cycle : il
        # n'est reporté que s'il coûte moins que les heures creuses de ce soir.
        plafonds={t: profils[t]["kwh"] * tarifs["hc"] for t in non_places},
        **reglages,
    )
    de_nuit, reportes = [], []
    for cycle in essai["cycles"]:
        cout_hc = profils[cycle["type"]]["kwh"] * tarifs["hc"]
        if cycle["debut"] is None:
            de_nuit.append(ce_soir(cycle["type"], "demain_plein"))
        elif cycle["cout_jour"] < cout_hc - 1e-9:
            cycle.update({
                "conseil": "demain",
                "motif": "demain_moins_cher",
                "demain_debut": cycle["debut"],
                "cout_demain": cycle["cout_jour"],
                # Ni l'un ni l'autre ne parlent de demain : pas de créneau
                # de jour aujourd'hui, et les heures creuses sont celles de
                # ce soir, au tarif d'aujourd'hui.
                "cout_jour": None,
                "cout_hc": cout_hc,
                "ecart": cout_hc - cycle["cout_jour"],
            })
            reportes.append(cycle)
        else:
            de_nuit.append(ce_soir(cycle["type"], "demain_plus_cher", essai=cycle))
    return de_nuit + reportes


def _cycle(profil, tarifs, debut, bilan, comparer_hc=True):
    """Fiche d'un cycle : son créneau de jour, ses coûts, et le conseil.

    ``debut`` à ``None`` : le cycle n'a pas trouvé de place dans la plage de
    lancement. Il ne reste alors que les heures creuses — ou un autre jour.

    ``comparer_hc`` à faux : le cycle reste à son créneau de jour quoi qu'il
    coûte. Le coût en heures creuses est quand même chiffré, pour information.
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
        # Renseignés par ``_reporter`` pour un cycle sans place aujourd'hui.
        "motif": None,
        "demain_debut": None,
        "cout_demain": None,
        # Renseignés par ``planifier`` quand la tolérance a déplacé le cycle.
        "meilleur_debut": None,
        "surcout": None,
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
    if comparer_hc and cout_hc is not None and cout_hc < bilan["cout_jour"] - 1e-9:
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

    ``{"hp", "hc", "hc_debut", "hc_fin", "couleur", "libelle", "demain",
    "rouges_restants"}``. L'objet vient du besoin ``tarifs_jour`` ; un
    fournisseur qui ne donnerait pas tous les champs attendus vaut « tarifs
    inconnus », pas une erreur.

    ``demain`` est la grille du lendemain (voir ``_grille_demain``), ou
    ``None`` quand on ne peut pas exclure un jour rouge. ``rouges_restants``
    est le nombre de jours rouges encore à tirer cette saison, ``None`` si
    le fournisseur ne le dit pas.
    """
    from core.liaisons import lire_besoin

    tarifs, _err = lire_besoin(MODULE, "tarifs_jour")
    try:
        grille = _grille(tarifs, tarifs["couleur"])
    except (TypeError, KeyError, ValueError, AttributeError):
        return None
    grille["demain"], grille["rouges_restants"] = _grille_demain(tarifs)
    return grille


def _grille_demain(tarifs):
    """Grille du lendemain et jours rouges restants : ``(grille, nombre)``.

    - la couleur de demain est publiée : sa grille ;
    - elle ne l'est pas, mais il ne reste plus aucun jour rouge à tirer
      cette saison : demain ne peut pas être rouge. On ignore encore sa
      couleur, alors on le chiffre au tarif de la plus chère de celles qui
      restent possibles — si le report vaut le coup à ce prix-là, il le
      vaut à coup sûr. La grille porte ``supposee`` ;
    - sinon ``None`` : un jour rouge ne peut pas être exclu.
    """
    try:
        restants = dict(tarifs.get("jours_restants") or {})
        rouges = restants.get(COULEUR_ROUGE)
        rouges = None if rouges is None else int(rouges)
    except (TypeError, ValueError, AttributeError):
        restants, rouges = {}, None

    def reste(couleur):
        try:
            return restants.get(couleur) is None or int(restants[couleur]) > 0
        except (TypeError, ValueError):
            return True

    try:
        couleur = tarifs.get("couleur_demain")
        if couleur:
            return {**_grille(tarifs, couleur), "supposee": False}, rouges
        if rouges is not None and rouges <= 0:
            autres = [c for c in tarifs["prix"] if c != COULEUR_ROUGE]
            possibles = [c for c in autres if reste(c)] or autres
            plus_chere = max(possibles, key=lambda c: float(tarifs["prix"][c]["HP"]))
            return {**_grille(tarifs, plus_chere), "supposee": True}, rouges
    except (TypeError, KeyError, ValueError, AttributeError):
        pass
    return None, rouges


def _grille(tarifs, couleur):
    """Prix et bornes des heures creuses pour une couleur de ``tarifs_jour``."""
    prix = tarifs["prix"][couleur]
    return {
        "hp": float(prix["HP"]),
        "hc": float(prix["HC"]),
        "hc_debut": int(tarifs.get("hc_debut", 22)),
        "hc_fin": int(tarifs.get("hc_fin", 6)),
        "couleur": couleur,
        "libelle": (tarifs.get("libelles") or {}).get(couleur, couleur),
    }


def _minuit_demain(maintenant):
    """Minuit du lendemain, heure locale.

    Reconstruit depuis la date plutôt qu'en ajoutant 24 h : la nuit d'un
    changement d'heure, la journée n'en fait pas 24.
    """
    demain = maintenant.astimezone().date() + timedelta(days=1)
    return datetime(demain.year, demain.month, demain.day).astimezone()


def _ballon_demain(points):
    """Créneau que le chauffe-eau prendra sans doute demain, ou ``None``.

    Demain n'est pas encore calculé, mais le ballon reste prioritaire : on
    lui réserve le créneau que son calcul retiendrait sur la prévision de
    demain, avec les réglages du moment.

    **Ce n'est pas son créneau définitif**, seulement une estimation
    grossière pour savoir si une machine peut passer demain. Elle n'est ni
    mémorisée comme heure de chauffe ni publiée : le vrai créneau sera
    calculé demain, sur une prévision plus fraîche, et les machines seront
    replacées autour de lui à ce moment-là. S'il chauffe finalement de nuit,
    la machine aura simplement évité un créneau qui était libre.

    ``None`` : pas de surplus solaire demain, le ballon chauffera en heures
    creuses et ne prend rien à la production.
    """
    duree = api.duree_chauffe_min()
    besoin = api.conso_chauffe_eau()
    if not points or duree <= 0:
        return None
    creneau = calcul._meilleur_creneau(
        sorted(points), duree, api.conso_min_maison(), besoin, mode=api.ajustement()
    )
    if creneau is None:
        return None
    return {
        "debut": creneau["debut"],
        "fin": creneau["debut"] + timedelta(minutes=duree),
        "kw": besoin / (duree / 60.0),
        "kwh": besoin,
    }


def _lendemain(maintenant, tarifs):
    """Ce qu'on sait de demain, pour un cycle qui n'a plus de place aujourd'hui.

    ``{"minuit", "tarifs", "rouge", "rouges_restants", "points", "ballon",
    "erreur"}`` — ``tarifs`` à ``None`` si un jour rouge ne peut pas être
    exclu (couleur pas encore publiée, et il reste des jours rouges à
    tirer). La prévision n'est lue que si elle peut servir : pas un jour
    rouge, pas sans tarifs.
    """
    from core.liaisons import lire_besoin

    grille = (tarifs or {}).get("demain")
    demain = {
        "minuit": _minuit_demain(maintenant),
        "tarifs": grille,
        "rouge": bool(grille and grille["couleur"] == COULEUR_ROUGE),
        "rouges_restants": (tarifs or {}).get("rouges_restants"),
        "points": [],
        "ballon": None,
        "erreur": "",
    }
    if not grille or demain["rouge"]:
        return demain

    points, erreur = lire_besoin(MODULE, "prevision_pv_demain")
    jour = demain["minuit"].date()
    demain["points"] = [
        (t, kw) for t, kw in points or [] if t.astimezone().date() == jour
    ]
    if not demain["points"]:
        demain["erreur"] = erreur or "la courbe ne va pas jusqu'à demain"
    demain["ballon"] = _ballon_demain(demain["points"])
    return demain


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

    # Le switch « Optimisé » ne compte pas un jour rouge : la comparaison
    # avec les heures creuses est alors toujours faite. Et le lendemain
    # n'est regardé que switch sur off — sur on, un cycle sans place va en
    # heures creuses, comme tout cycle que la nuit sert mieux.
    optimise = api.machines_optimise()
    rouge = bool(tarifs and tarifs["couleur"] == COULEUR_ROUGE)
    comparer_hc = optimise or rouge
    lendemain = None if comparer_hc else _lendemain(maintenant, tarifs)

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
        comparer_hc=comparer_hc,
        lendemain=lendemain,
        tolerance=api.tolerance_machines_cts() / 100.0,
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
        "tolerance_cts": api.tolerance_machines_cts(),
        "ballon": ballon,
        "tarifs": tarifs,
        "ajustement": api.ajustement(),
        "erreur": erreur,
        "optimise": optimise,
        "rouge": rouge,
        "comparer_hc": comparer_hc,
        # Sans la courbe : elle ne sert qu'au calcul, pas à l'affichage.
        "lendemain": lendemain and {
            "tarifs": lendemain["tarifs"],
            "rouge": lendemain["rouge"],
            "rouges_restants": lendemain["rouges_restants"],
            "ballon": lendemain["ballon"],
            "erreur": lendemain["erreur"],
        },
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
        "optimise": api.machines_optimise(), "rouge": False,
        "comparer_hc": True, "lendemain": None,
    }


def dernier_resultat():
    """Dernier plan mémorisé, complété de ce qui dépend de l'heure qu'il est.

    Source unique pour le tableau de bord, l'onglet et les infos. Ajoute à
    chaque cycle ``passe`` (son heure de lancement est derrière nous) et, au
    plan, ``affichees`` (les cycles encore à lancer — les seuls qu'on
    montre), ``prochaine`` (le premier à lancer aujourd'hui, heures creuses
    de ce soir comprises), ``restantes``, ``reportees`` (ceux conseillés
    pour demain, avec ``prochaine_demain``) et les totaux. ``perime``
    signale un plan calculé un autre jour : ses heures ne veulent alors plus
    rien dire.
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

    # Un plan mémorisé avant l'arrivée du switch comparait toujours.
    resultat.setdefault("optimise", True)
    resultat.setdefault("rouge", False)
    resultat.setdefault("comparer_hc", True)
    resultat.setdefault("lendemain", None)
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
    reportees = []
    affichees = []
    total = {"kwh": 0.0, "solaire_kwh": 0.0, "import_kwh": 0.0, "cout": 0.0}
    chiffrable = True
    for numero, cycle in enumerate(resultat["cycles"], start=1):
        cycle["numero"] = numero
        conseil = cycle.get("conseil")
        # Strictement passé : un cycle prévu pour maintenant reste à lancer.
        # Seul un cycle de jour « passe » : celui de ce soir ou de demain
        # reste à lancer tant que le plan vaut.
        cycle["passe"] = bool(
            conseil == "jour" and cycle.get("debut") and cycle["debut"] < maintenant
        )

        # Coût de l'option conseillée : de jour (aujourd'hui ou demain), la
        # part solaire est gratuite ; de nuit, tout le cycle est acheté en
        # heures creuses. Un cycle coûte quelques centimes : c'est l'unité
        # lisible.
        if conseil == "jour":
            cout = cycle.get("cout_jour")
        elif conseil == "demain":
            cout = cycle.get("cout_demain")
        else:
            cout = cycle.get("cout_hc")
        cycle["cout_c"] = _centimes(cout)
        cycle["cout_jour_c"] = _centimes(cycle.get("cout_jour"))
        cycle["cout_hc_c"] = _centimes(cycle.get("cout_hc"))
        cycle["cout_demain_c"] = _centimes(cycle.get("cout_demain"))
        cycle["ecart_c"] = _centimes(cycle.get("ecart"))
        # Pourquoi ce cycle n'est pas reporté à demain (vide le plus souvent).
        cycle["raison"] = _MOTIFS.get(cycle.get("motif") or "", "")

        # Une machine dont l'heure est passée est supposée lancée : elle
        # reste dans le plan (le calcul en a besoin) mais n'est plus
        # montrée, et ne compte plus dans les totaux.
        if cycle["passe"]:
            continue
        affichees.append(cycle)
        if cycle.get("heure"):
            (reportees if conseil == "demain" else a_venir).append(cycle)

        total["kwh"] += cycle["kwh"]
        if conseil in ("jour", "demain"):
            total["solaire_kwh"] += cycle.get("solaire_kwh") or 0.0
            total["import_kwh"] += cycle.get("import_kwh") or 0.0
        else:
            total["import_kwh"] += cycle["kwh"]
        if cout is None:
            chiffrable = False
        else:
            total["cout"] += cout

    if not chiffrable or not affichees:
        total["cout"] = None
    total["cout_c"] = _centimes(total["cout"])
    resultat["total"] = total
    resultat["affichees"] = affichees
    # « À lancer aujourd'hui » : un cycle reporté à demain n'en fait pas
    # partie — son heure, lue aujourd'hui, déclencherait un rappel à tort.
    resultat["restantes"] = len(a_venir)
    resultat["reportees"] = len(reportees)
    resultat["nb_passees"] = len(resultat["cycles"]) - len(affichees)
    resultat["a_des_passees"] = resultat["nb_passees"] > 0
    # Les cycles de jour d'abord, dans l'ordre ; ceux de la nuit ensuite.
    a_venir.sort(key=lambda c: (c.get("conseil") != "jour", c.get("debut") or maintenant))
    resultat["prochaine"] = (
        a_venir[0] if a_venir and not resultat.get("perime") else None
    )
    reportees.sort(key=lambda c: c.get("debut") or maintenant)
    resultat["prochaine_demain"] = (
        reportees[0] if reportees and not resultat.get("perime") else None
    )
    return resultat


# ----------------------------------------------------------------------
# Explication lisible (Journal et onglet)
# ----------------------------------------------------------------------

def _hm(instant):
    return instant.astimezone().strftime("%H:%M")


def _detail_lendemain(lendemain):
    """Ce qui a été retenu de demain, pour un cycle sans place aujourd'hui."""
    if lendemain.get("rouge"):
        return "Demain est un jour rouge : aucun cycle n'y est reporté."
    grille = lendemain.get("tarifs")
    rouges = lendemain.get("rouges_restants")
    if not grille:
        compteur = (
            f"il reste {rouges} jour{'s' if rouges > 1 else ''} rouge"
            f"{'s' if rouges > 1 else ''} à tirer cette saison"
            if rouges else "un jour rouge ne peut pas être exclu"
        )
        return (
            f"Couleur de demain pas encore connue, et {compteur} : aucun cycle "
            f"n'y est reporté."
        )
    if grille.get("supposee"):
        entete = (
            "Demain (couleur pas encore connue, mais plus aucun jour rouge à "
            f"tirer cette saison — chiffré en {grille['libelle']}, la plus chère "
            f"des couleurs possibles : HP {grille['hp']:.4f} €/kWh)"
        )
    else:
        entete = f"Demain ({grille['libelle']}, HP {grille['hp']:.4f} €/kWh)"
    if lendemain.get("erreur"):
        return (
            f"{entete} : pas de prévision ({lendemain['erreur']}), aucun cycle "
            f"n'y est reporté."
        )
    ballon = lendemain.get("ballon")
    if ballon:
        return (
            f"{entete} : chauffe-eau estimé de {_hm(ballon['debut'])} à "
            f"{_hm(ballon['fin'])} à {ballon['kw']:.2f} kW et retiré de la "
            f"prévision — estimation grossière, son vrai créneau sera calculé "
            f"demain."
        )
    return (
        f"{entete} : pas de surplus solaire pour le chauffe-eau, rien n'est "
        f"retiré de la prévision."
    )


def _detail_tolerance(c):
    """Fin de ligne d'un cycle que la tolérance a déplacé, sinon rien."""
    meilleur = c.get("meilleur_debut")
    if not meilleur or not c.get("debut"):
        return ""
    sens = "avancé" if c["debut"] < meilleur else "décalé"
    return (
        f" Le créneau le moins cher était {_hm(meilleur)} : {sens} pour "
        f"{c.get('surcout') or 0.0:.3f} € de plus, dans la tolérance."
    )


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
    comparer = r.get("comparer_hc", True)
    if tarifs:
        lignes.append(
            f"Tarifs du jour ({tarifs['libelle']}) : HP {tarifs['hp']:.4f} €/kWh, "
            f"HC {tarifs['hc']:.4f} €/kWh à partir de {int(tarifs['hc_debut']) % 24:02d}:00."
        )
        if r.get("rouge"):
            lignes.append(
                "Jour rouge : le switch « Optimisé » n'est pas pris en compte, "
                "chaque cycle va là où il coûte le moins — sur la production ou "
                "en heures creuses."
            )
        elif comparer:
            lignes.append(
                "Switch « Optimisé » sur on : chaque créneau de jour est comparé "
                "aux heures creuses."
            )
        else:
            lignes.append(
                "Switch « Optimisé » sur off : les cycles sont placés dans la "
                "plage de lancement, sans regarder les heures creuses."
            )
    else:
        lignes.append(
            "Tarifs indisponibles : aucun coût chiffrable, les créneaux sont "
            "classés sur l'énergie achetée au réseau, sans comparaison avec "
            "les heures creuses."
        )

    if tarifs and r.get("tolerance_cts"):
        choix = (
            "le plus productif" if r.get("ajustement") == "max" else "le plus tôt"
        )
        lignes.append(
            f"Tolérance de {r['tolerance_cts']:.1f} ct par cycle : entre les "
            f"créneaux qui valent le meilleur à ce prix près, c'est {choix} "
            f"qui est retenu."
        )

    lendemain = r.get("lendemain")
    if lendemain and any(
        str(c.get("motif") or "").startswith("demain") for c in r["cycles"]
    ):
        lignes.append(_detail_lendemain(lendemain))

    for numero, c in enumerate(r["cycles"], start=1):
        nom = f"{c['libelle']} n° {numero}"
        if c.get("lancee"):
            lignes.append(
                f"{nom} : lancé à {_hm(c['debut'])} d'après le plan précédent, conservé."
            )
            continue
        if c.get("conseil") == "demain":
            ligne = (
                f"{nom} : plus de créneau libre aujourd'hui → demain "
                f"{_hm(c['debut'])}–{_hm(c['fin'])}, "
                f"{c['solaire_kwh']:.2f} kWh couverts par le solaire, "
                f"{c['import_kwh']:.2f} kWh achetés au réseau"
            )
            if c.get("avec_ballon"):
                ligne += (
                    " (en même temps que le chauffe-eau estimé, le solaire "
                    "couvre les deux)"
                )
            lignes.append(
                ligne
                + f" → {c['cout_demain']:.3f} € contre {c['cout_hc']:.3f} € en heures "
                f"creuses ce soir. DÉCISION : lancer demain à {c['heure']} "
                f"(écart {c['ecart']:.3f} €)." + _detail_tolerance(c)
            )
            continue
        if not c.get("debut"):
            pourquoi = ""
            if c.get("motif") == "demain_plus_cher":
                pourquoi = (
                    f" ; demain à {_hm(c['demain_debut'])} coûterait "
                    f"{c['cout_demain']:.3f} €, pas moins que les heures creuses"
                )
            elif c.get("motif") in _MOTIFS:
                pourquoi = " ; " + _MOTIFS[c["motif"]]
            suite = (
                f"heures creuses à partir de {c['heure']} ({c['cout_hc']:.3f} €)"
                if c.get("conseil") == "hc" else "à reporter"
            )
            lignes.append(
                f"{nom} : plus de créneau libre dans la plage de lancement"
                f"{pourquoi} → {suite}."
            )
            continue

        ligne = (
            f"{nom} : {_hm(c['debut'])}–{_hm(c['fin'])}, "
            f"{c['solaire_kwh']:.2f} kWh couverts par le solaire, "
            f"{c['import_kwh']:.2f} kWh achetés au réseau"
        )
        if c.get("avec_ballon"):
            ligne += " (en même temps que le chauffe-eau, le solaire couvre les deux)"
        if c.get("cout_hc") is not None and comparer:
            ligne += (
                f" → {c['cout_jour']:.3f} € de jour contre {c['cout_hc']:.3f} € "
                f"en heures creuses"
            )
        elif c.get("cout_jour") is not None:
            ligne += f" → {c['cout_jour']:.3f} €"
        if c.get("conseil") == "hc":
            ligne += (
                f". DÉCISION : heures creuses, à partir de {c['heure']} "
                f"(écart {c['ecart']:.3f} €)."
            )
        else:
            ligne += f". DÉCISION : lancer à {c['heure']}." + _detail_tolerance(c)
        lignes.append(ligne)

    if r.get("erreur"):
        lignes.append(f"Remarque : {r['erreur']}.")
    return lignes
