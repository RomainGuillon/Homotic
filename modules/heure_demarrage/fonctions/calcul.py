# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

"""Calcul de la meilleure heure de démarrage du chauffe-eau.

Durée de chauffe : temp_chauffe_ete (1 h par défaut) ou temp_chauffe_hiver
(1 h 30) selon les switchs Été/Hiver — les fenêtres testées font donc 2 pas
de prévision de 30 min en été, 3 pas en hiver.

Sur chaque fenêtre possible de la journée à venir :

    production = somme des prévisions sur la fenêtre (kWh)
    surplus    = production − talon de la maison sur la durée
    import     = besoin du ballon − surplus, borné à 0

Autrement dit : si la production couvre le ballon ET le talon de la maison,
rien n'est acheté (0 €) ; sinon l'écart est acheté au réseau.

On retient la fenêtre dont l'import est le plus faible, et la plus tôt à
import égal.

    COÛT JOUR = import × prix du kWh en heures pleines (couleur du jour)
    COÛT NUIT = besoin du ballon × prix du kWh en heures creuses

Le solaire autoconsommé ne coûte rien : aucun manque à gagner de revente
n'entre dans le calcul. Si « optimiser » est coché, le moins cher des deux
gagne (égalité → journée solaire) ; sinon on garde simplement la fenêtre
retenue ci-dessus.

Les deux données d'entrée — la prévision de production et la tarification —
viennent de **besoins déclarés** (voir ``conf.py`` et
``docs/09-liaisons-entre-modules.md``) : ce module ne connaît aucun autre
module par son nom. Sans prévision branchée, le calcul se rabat sur l'heure
de nuit ; sans tarifs, il garde le créneau solaire sans arbitrer les coûts.

Le calcul n'est JAMAIS automatique : il n'a lieu que sur demande (action de
scénario « recalculer », ou bouton Recalculer de l'onglet). Son résultat est
mémorisé (réglage ``dernier_calcul``) et c'est cette photo que lisent le
tableau de bord, les infos du module et les déclencheurs de scénario. Sinon
l'heure retenue se déplacerait toute seule au fil de la journée : le calcul
ne considère que les créneaux à venir, donc à 12h30 le créneau de 12h30 n'est
déjà plus candidat et l'heure recule devant l'horloge.
"""

import json
import math
from datetime import date, datetime, timedelta

from core.services import get_setting, get_variable, journal, set_setting

MODULE = "heure_demarrage"
CLE_DERNIER = "dernier_calcul"
VARIABLE_HEURE = "heure_demarrage_chauffe_eau"

from . import api


def _forecast_points():
    """Prévision de production du jour, restreinte à ce qui reste à venir.

    La courbe vient du besoin ``prevision_pv`` : ce module ne sait pas qu'un
    module « solcast » existe, il sait qu'il lui faut une prévision en kW.
    Le filtre « à venir » reste ici, c'est sa règle à lui — on ne peut pas
    programmer une chauffe dans le passé.
    """
    from core.liaisons import lire_besoin

    points, err = lire_besoin(MODULE, "prevision_pv")
    if err:
        return [], err
    if not points:
        return [], "prévisions indisponibles"

    now = datetime.now().astimezone()
    today = date.today()
    return [(t, kw) for t, kw in points if t.date() == today and t >= now], ""


def _meilleur_creneau(points, duree_min, talon_kwh_h, besoin_kwh, mode="faible",
                      exiger_surplus=True):
    """Créneau de ``duree_min`` retenu, selon le réglage « ajustement ».

    Sur chaque fenêtre possible (pas Solcast de 30 min, ``points`` daté au
    milieu du pas) :
        production = somme(kW × 0,5 h)
        surplus    = production − talon maison sur la durée
        import     = besoin du ballon − surplus  (borné à 0)

    Deux critères de choix :

    - ``faible`` : l'import le plus faible ; à import égal, la fenêtre la
      plus tôt. Dès que le solaire couvre le besoin, plusieurs fenêtres
      sont à zéro et c'est donc la première qui gagne.
    - ``max`` : le surplus le plus élevé, c'est-à-dire le pic de production.
      La chauffe se cale sur le meilleur moment de la journée même si une
      fenêtre plus tôt aurait suffi — on maximise la part autoconsommée et
      on garde de la marge si la prévision est trop optimiste.

    ``exiger_surplus`` : par défaut, une journée sans le moindre surplus ne
    donne aucun créneau — chauffer de jour n'aurait alors aucun intérêt face
    aux heures creuses. En mode « jour », où les heures creuses ne sont plus
    accessibles, on retient au contraire le moins mauvais créneau.

    Retourne {debut, fin, production_kwh, surplus_kwh, import_kwh,
    couvert_kwh} ou None si aucune fenêtre complète n'est disponible.
    """
    n = max(1, math.ceil(duree_min / 30))
    if len(points) < n:
        return None

    duree_h = n * 0.5
    talon_kwh = talon_kwh_h * duree_h

    best = None
    for i in range(len(points) - n + 1):
        fenetre = points[i:i + n]
        production = sum(kw * 0.5 for _t, kw in fenetre)
        surplus = max(0.0, production - talon_kwh)
        importe = max(0.0, besoin_kwh - surplus)

        # Les points étant triés, ne remplacer que sur amélioration stricte
        # revient à garder la fenêtre la plus tôt en cas d'égalité.
        if mode == "max":
            # Le surplus est borné à 0 : par temps couvert toutes les
            # fenêtres sont à égalité, et sans second critère on garderait
            # bêtement la première. On départage alors sur la production.
            meilleur = (
                best is None
                or surplus > best["surplus_kwh"] + 1e-9
                or (abs(surplus - best["surplus_kwh"]) <= 1e-9
                    and production > best["production_kwh"] + 1e-9)
            )
        else:
            # À import égal — cas fréquent quand tout est couvert, mais aussi
            # quand rien ne l'est — on départage sur la production, sinon
            # « le moins mauvais créneau » serait simplement le premier.
            meilleur = (
                best is None
                or importe < best["import_kwh"] - 1e-9
                or (abs(importe - best["import_kwh"]) <= 1e-9
                    and best["import_kwh"] > 0
                    and production > best["production_kwh"] + 1e-9)
            )

        if meilleur:
            best = {
                "debut": fenetre[0][0] - timedelta(minutes=15),
                "fin": fenetre[-1][0] + timedelta(minutes=15),
                "production_kwh": production,
                "surplus_kwh": surplus,
                "import_kwh": importe,
                "couvert_kwh": min(besoin_kwh, surplus),
                "talon_kwh": talon_kwh,
                "duree_h": duree_h,
            }
    if best is None:
        return None
    if exiger_surplus and best["surplus_kwh"] <= 0:
        return None  # aucun surplus exploitable : chauffe de jour sans intérêt
    return best


def _tarifs():
    """(prix_hp, prix_hc, couleur) du jour, ou None.

    Vient du besoin ``tarifs_jour``. Sans liaison ou sans tarif connu :
    ``None``, et l'arbitrage jour/nuit est abandonné au profit du repli
    décrit dans ``detail_texte``.
    """
    from core.liaisons import lire_besoin

    tarifs, _err = lire_besoin(MODULE, "tarifs_jour")
    if not tarifs:
        return None
    couleur = tarifs.get("couleur")
    prix = (tarifs.get("prix") or {}).get(couleur)
    if not prix:
        return None
    return prix["HP"], prix["HC"], couleur


def detail_texte(r):
    """Explication lisible du calcul, coûts détaillés (Journal et onglet).

    Le détail ne raconte que ce qui a servi à décider. En arbitrage « jour »,
    les heures creuses ne sont pas accessibles : tout le raisonnement de
    comparaison HP/HC est donc omis, il ne ferait qu'égarer la lecture.
    """
    jour_seul = r.get("arbitrage") == "jour"

    entete = (
        f"Données : saison {r['saison']} → durée de chauffe {r['duree_min']} min ; "
        f"besoin du ballon {r['besoin_kwh']:.2f} kWh ; "
        f"talon maison {r['talon_kwh_h']:.2f} kWh/h"
    )
    if jour_seul:
        entete += " ; arbitrage JOUR : les heures creuses sont passées, pas de comparaison."
    else:
        entete += f" ; optimisation {'activée' if r.get('optimiser') else 'désactivée'}."
    lignes = [entete]

    # --- Tarifs : seulement quand ils entrent dans la décision ---
    if not jour_seul:
        if r.get("couleur"):
            lignes.append(
                f"Tarifs du jour ({r['couleur']}) : HP {r['prix_hp']:.4f} €/kWh, "
                f"HC {r['prix_hc']:.4f} €/kWh."
            )
        else:
            lignes.append(
                "Tarifs indisponibles (besoin « tarifs_jour » non branché, "
                "ou couleur du jour inconnue) : aucun coût chiffrable."
            )

    # --- Créneau solaire ---
    if r.get("creneau"):
        c = r["creneau"]
        critere = (
            "pic de production, ajustement Max"
            if r.get("ajustement") == "max"
            else "import le plus faible, le plus tôt — ajustement Faible"
        )
        lignes.append(
            f"Créneau retenu ({critere}) "
            f"{c['debut'].astimezone():%H:%M}–{c['fin'].astimezone():%H:%M} "
            f"({c['duree_h']:.1f} h) : production prévue {c['production_kwh']:.2f} kWh "
            f"− talon maison {c['talon_kwh']:.2f} kWh "
            f"({r['talon_kwh_h']:.2f} × {c['duree_h']:.1f} h) "
            f"= surplus {c['surplus_kwh']:.2f} kWh disponible pour le ballon."
        )
    elif jour_seul:
        lignes.append(
            "Aucun créneau exploitable : plus de prévision pour la journée."
        )
    else:
        lignes.append(
            "Aucun créneau solaire exploitable aujourd'hui : la chauffe de jour "
            "n'est pas chiffrable."
        )

    if jour_seul:
        # En mode jour, un seul chiffre a du sens : ce qu'il faudra acheter.
        if r.get("creneau") is not None:
            c = r["creneau"]
            achat = (
                f"{c['import_kwh']:.2f} kWh restant à acheter au réseau"
                if c["import_kwh"] > 0
                else "aucun achat au réseau, le solaire couvre le besoin"
            )
            cout = ""
            if r.get("cout_jour") is not None:
                cout = f" — soit {r['cout_jour']:.3f} € en heures pleines"
            lignes.append(
                f"Bilan du créneau : {c['couvert_kwh']:.2f} kWh couverts par le "
                f"solaire, {achat}{cout}."
            )
    else:
        # --- Coût de la chauffe en journée ---
        if r.get("cout_jour") is not None:
            c = r["creneau"]
            lignes.append(
                f"COÛT JOUR ({c['debut'].astimezone():%H:%M}) : besoin "
                f"{r['besoin_kwh']:.2f} kWh − surplus {c['surplus_kwh']:.2f} kWh "
                f"= {c['import_kwh']:.2f} kWh à importer × {r['prix_hp']:.4f} € (HP) "
                f"→ {r['cout_jour']:.3f} € "
                f"({c['couvert_kwh']:.2f} kWh couverts par le solaire, gratuits)."
            )
        else:
            lignes.append("COÛT JOUR = non chiffrable.")

        # --- Coût de la chauffe de nuit ---
        if r.get("cout_nuit") is not None:
            lignes.append(
                f"COÛT NUIT ({r['heure_nuit']}) = {r['besoin_kwh']:.2f} kWh "
                f"achetés en HC × {r['prix_hc']:.4f} € → {r['cout_nuit']:.3f} €."
            )
        else:
            lignes.append("COÛT NUIT = non chiffrable.")

        # --- Comparaison et décision ---
        if r.get("cout_jour") is not None and r.get("cout_nuit") is not None:
            moins_cher = "JOUR" if r["cout_jour"] <= r["cout_nuit"] else "NUIT"
            lignes.append(
                f"Comparaison : jour {r['cout_jour']:.3f} € contre nuit "
                f"{r['cout_nuit']:.3f} € → le moins cher est la chauffe de "
                f"{moins_cher} (écart {r['gain']:.3f} €)."
            )
            if not r.get("optimiser"):
                lignes.append(
                    "Optimisation désactivée : la décision ignore ces coûts et "
                    "retient le créneau le plus productif."
                )
        elif r.get("optimiser"):
            lignes.append(
                "Comparaison impossible : "
                + ("tarifs manquants. " if not r.get("couleur") else "")
                + ("pas de créneau solaire. " if not r.get("creneau") else "")
                + "Repli sur "
                + ("la nuit." if r.get("mode") == "nuit" else "le créneau solaire.")
            )

    if r.get("erreur"):
        lignes.append(f"Remarque : {r['erreur']}.")

    if r.get("heure"):
        raison = {
            "nuit": "chauffe en heures creuses",
            "solaire": "chauffe sur le créneau solaire",
        }.get(r.get("mode"), r.get("mode"))
        lignes.append(f"DÉCISION : démarrage à {r['heure']} — {raison}.")
    else:
        lignes.append(
            "DÉCISION : aucune heure retenue, la précédente est conservée."
        )
    return lignes


def _serialiser(valeur):
    """Rend le résultat stockable en JSON (les créneaux portent des dates)."""
    if isinstance(valeur, datetime):
        return {"__dt__": valeur.isoformat()}
    if isinstance(valeur, dict):
        return {k: _serialiser(v) for k, v in valeur.items()}
    if isinstance(valeur, (list, tuple)):
        return [_serialiser(v) for v in valeur]
    return valeur


def _deserialiser(valeur):
    if isinstance(valeur, dict):
        if "__dt__" in valeur and len(valeur) == 1:
            try:
                return datetime.fromisoformat(valeur["__dt__"])
            except (ValueError, TypeError):
                return None
        return {k: _deserialiser(v) for k, v in valeur.items()}
    if isinstance(valeur, list):
        return [_deserialiser(v) for v in valeur]
    return valeur


def memoriser(resultat):
    """Enregistre le résultat d'un calcul comme référence courante."""
    charge = _serialiser(dict(resultat))
    charge["quand"] = datetime.now().isoformat()
    set_setting(CLE_DERNIER, json.dumps(charge), module=MODULE)
    return resultat


def _resultat_vide(erreur):
    """Résultat neutre quand aucun calcul n'est disponible.

    Toutes les clés attendues par les gabarits sont présentes : un bloc de
    tableau de bord ne doit pas tomber en erreur parce que le calcul n'a pas
    encore été lancé.
    """
    vide = dict.fromkeys(
        ("heure", "mode", "creneau", "saison", "duree_min", "optimiser",
         "heure_nuit", "besoin_kwh", "talon_kwh_h", "cout_jour", "cout_nuit",
         "gain", "couleur", "prix_hp", "prix_hc", "part_solaire_kwh",
         "part_reseau_kwh")
    )
    vide.update({
        "detail": [], "erreur": erreur, "quand": None,
        "perime": False, "jamais_calcule": True,
        "arbitrage": "nuit", "ajustement": "faible",
    })
    return vide


def dernier_resultat():
    """Dernier calcul mémorisé, ou un résultat vide si aucun.

    C'est la source unique de vérité du module : tableau de bord, infos et
    déclencheurs lisent ceci, jamais un calcul refait à la volée. La clé
    ``quand`` donne la date du calcul, et ``perime`` indique qu'il date d'un
    autre jour (l'heure retenue ne veut alors plus rien dire).
    """
    raw = get_setting(CLE_DERNIER, module=MODULE)
    if not raw:
        # Même sans calcul mémorisé, une heure saisie à la main doit être
        # rendue : c'est elle la valeur de référence.
        return _appliquer_variable(
            _resultat_vide("aucun calcul effectué : lancer l'action « recalculer »")
        )
    try:
        charge = json.loads(raw)
    except (ValueError, TypeError):
        return _appliquer_variable(_resultat_vide("dernier calcul illisible"))

    resultat = _deserialiser(charge)
    quand = resultat.get("quand")
    resultat["quand"] = datetime.fromisoformat(quand) if quand else None
    resultat["jamais_calcule"] = False
    resultat["perime"] = bool(
        resultat["quand"] and resultat["quand"].date() != date.today()
    )
    return _appliquer_variable(resultat)


def _appliquer_variable(resultat):
    """Fait primer la variable ``heure_demarrage_chauffe_eau`` sur le calcul.

    La variable est la seule valeur modifiable à la main (onglet
    Configuration) et c'est elle que lisent les scénarios : elle doit donc
    aussi être celle qu'affichent le tableau de bord et l'onglet, sinon on
    corrige l'heure quelque part et l'écran continue d'en montrer une autre.

    Le reste du résultat (créneau, coûts, détail) vient toujours du dernier
    calcul : ``heure_calculee`` conserve la valeur qu'il avait retenue et
    ``heure_forcee`` signale l'écart.
    """
    forcee = str(get_variable(VARIABLE_HEURE) or "").strip()
    resultat["heure_calculee"] = resultat.get("heure")
    resultat["heure_forcee"] = bool(forcee and forcee != resultat.get("heure"))
    if forcee:
        resultat["heure"] = forcee
    return resultat


def calculer(tracer=False, arbitrage="nuit"):
    """Retourne un dict décrivant la décision.

    {heure, mode, saison, duree_min, creneau, cout_jour, cout_nuit,
     part_solaire_kwh, part_reseau_kwh, gain, couleur, erreur, detail}
    ``mode`` : "solaire" (créneau du jour) ou "nuit" (heure creuse).
    ``tracer`` : écrit le détail du calcul dans le Journal (tâche périodique
    et bouton Recalculer ; pas à chaque affichage de page).

    ``arbitrage`` :

    - ``"nuit"`` (défaut) : comportement historique. On chiffre la chauffe
      de nuit en heures creuses et, si « optimiser » est coché, la moins
      chère des deux l'emporte.
    - ``"jour"`` : les heures creuses ne sont plus accessibles — typiquement
      un recalcul lancé en matinée. Aucune comparaison avec la nuit, on
      retient le meilleur créneau solaire restant. Et si la journée n'offre
      aucun surplus, on prend le moins mauvais créneau plutôt que de
      renvoyer à une nuit déjà passée.
    """
    arbitrage = "jour" if str(arbitrage).lower() == "jour" else "nuit"
    duree_min = api.duree_chauffe_min()
    besoin = api.conso_chauffe_eau()
    resultat = {
        "saison": api.saison(),
        "duree_min": duree_min,
        "arbitrage": arbitrage,
        "optimiser": api.optimiser() and arbitrage == "nuit",
        "heure_nuit": api.heure_nuit(),
        "besoin_kwh": besoin,
        "talon_kwh_h": api.conso_min_maison(),
        "creneau": None,
        "cout_jour": None,
        "cout_nuit": None,
        "gain": None,
        "couleur": None,
        "erreur": "",
    }

    # --- 1. Créneau solaire du jour (si prévisions disponibles) ---
    # L'origine des prévisions (appel réel ou cache de test) n'est plus
    # remontée ici : elle appartient au module qui les fournit, et son onglet
    # l'affiche déjà. Ce module ne sait plus d'où vient la courbe — c'est
    # exactement le but.
    points, err = _forecast_points()
    resultat["ajustement"] = api.ajustement()
    creneau = (
        _meilleur_creneau(points, duree_min, resultat["talon_kwh_h"], besoin,
                          mode=resultat["ajustement"],
                          # En mode « jour », on chauffe même sans surplus.
                          exiger_surplus=(arbitrage == "nuit"))
        if points else None
    )
    resultat["creneau"] = creneau
    if err:
        resultat["erreur"] = err
    elif creneau is None:
        resultat["erreur"] = (
            "aucun créneau exploitable : plus de prévision pour la journée"
            if arbitrage == "jour"
            else "aucun créneau solaire exploitable aujourd'hui"
        )
    elif arbitrage == "jour" and creneau["surplus_kwh"] <= 0:
        resultat["erreur"] = (
            "aucun surplus solaire aujourd'hui : créneau le moins coûteux retenu"
        )

    # --- 2. Coûts (chiffrés dès que les tarifs sont disponibles) ---
    tarifs = _tarifs()
    if tarifs is not None:
        prix_hp, prix_hc, couleur = tarifs
        resultat.update({
            "couleur": couleur,
            "prix_hp": prix_hp,
            "prix_hc": prix_hc,
            # La chauffe de nuit est toujours chiffrable : tout en HC
            "cout_nuit": besoin * prix_hc,
        })
        if creneau is not None:
            # Coût de jour = uniquement ce qui est acheté au réseau en HP :
            # le solaire autoconsommé est gratuit.
            resultat.update({
                "part_solaire_kwh": creneau["couvert_kwh"],
                "part_reseau_kwh": creneau["import_kwh"],
                "cout_jour": creneau["import_kwh"] * prix_hp,
            })
            resultat["gain"] = abs(resultat["cout_jour"] - resultat["cout_nuit"])

    # --- 3. Décision ---
    heure_solaire = creneau["debut"].astimezone().strftime("%H:%M") if creneau else None

    if creneau is None and arbitrage == "jour":
        # Journée finie ou prévisions absentes : on ne peut rien proposer, et
        # renvoyer à une nuit déjà passée n'aurait aucun sens.
        resultat["mode"] = "solaire"
        resultat["heure"] = None
    elif creneau is None:
        # Pas de chauffe solaire possible : repli heures creuses
        resultat["mode"] = "nuit"
        resultat["heure"] = api.heure_nuit()
    elif not resultat["optimiser"]:
        # Optimisation désactivée : le créneau le plus productif gagne
        resultat["mode"] = "solaire"
        resultat["heure"] = heure_solaire
    elif resultat["cout_jour"] is None:
        # Optimisation demandée mais tarifs manquants : on garde le solaire
        resultat["mode"] = "solaire"
        resultat["heure"] = heure_solaire
        if not resultat["erreur"]:
            resultat["erreur"] = "tarifs indisponibles : arbitrage par le coût impossible"
    elif resultat["cout_nuit"] < resultat["cout_jour"]:
        resultat["mode"] = "nuit"
        resultat["heure"] = api.heure_nuit()
    else:
        resultat["mode"] = "solaire"
        resultat["heure"] = heure_solaire

    return _finaliser(resultat, tracer)


def _finaliser(resultat, tracer):
    """Ajoute le détail lisible, mémorise le résultat et le journalise."""
    resultat["detail"] = detail_texte(resultat)
    if tracer:
        journal(
            "Calcul de l'heure de démarrage — " + " ".join(resultat["detail"]),
            module=api.MODULE,
        )
    # Tout calcul devient la nouvelle référence lue par le reste de l'appli.
    memoriser(resultat)
    resultat["quand"] = datetime.now()
    resultat["perime"] = False
    resultat["jamais_calcule"] = False
    return resultat
