# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

"""Durée et énergie de la prochaine chauffe, d'après la température du ballon.

C'est ce pour quoi le suivi des chauffes a été mis en place : remplacer la
durée fixe par saison par le besoin réel du jour.

Le principe. La résistance chauffe à puissance constante, donc l'énergie
suit la durée, et la durée suit la température de départ : plus le ballon
est froid, plus c'est long. Sur 62 chauffes (août–octobre 2026) la relation
est une droite, ``durée ≈ 2,65 × (65 − T) + 6`` minutes, qui prédit la durée
à 4 min près quand une durée fixe de 60 min se trompe de 17.

La droite n'est pas écrite en dur : elle est **réapprise à chaque lecture**
sur les dernières chauffes (``modele_chauffes_max``, 25 par défaut). Elle
suit ainsi la saison sans qu'on y touche — l'eau froide baisse sur des
mois, la fenêtre glisse avec.

Quelles chauffes apprennent (``ChauffeSession.apprend``) : celles parties à
l'heure prévue par le calcul, sans eau tirée en cours de route. Une chauffe
lancée à la main la veille d'un retour d'absence part d'un ballon bien plus
froid que tout le reste et pèserait à elle seule sur la droite ; une relance
du ballon se fait souvent pendant qu'on tire de l'eau.

Les garde-fous. Le modèle **se tait** plutôt que de deviner, et celui qui
lit l'estimation retombe alors sur ses propres réglages :

- moins de ``modele_chauffes_min`` chauffes (12) : pas assez pour une droite ;
- une droite qui ne descend pas : les chauffes ne disent rien d'exploitable ;
- un ballon plus froid ou plus chaud que tout ce que le modèle a vu
  (à ``modele_marge_degres`` près) : ce serait de l'extrapolation. Le cas
  type est le retour d'absence. La chauffe est enregistrée quand même, la
  plage s'élargit donc toute seule ;
- et quand il répond, la durée reste entre ``modele_duree_min`` et
  ``modele_duree_max`` : quelques chauffes atypiques ne peuvent pas faire
  sortir une valeur absurde.

Entre le calcul et la chauffe. L'estimation est demandée au moment du
calcul, parfois des heures avant le départ ; si de l'eau est tirée
entre-temps, le ballon part plus froid que la température lue. Le suivi
mesure cet écart sur les chauffes passées (``suivi.bilan_depart``), et dès
qu'il en a assez (10), on le **retire de la température lue** avant de
lire la droite : c'est la température attendue au départ qui est estimée,
et c'est elle qui doit tomber dans la plage connue. La température lue est
publiée telle quelle, sans la correction — c'est elle que le suivi garde
pour continuer à mesurer l'écart, sinon la correction se corrigerait
elle-même. Réglage ``modele_corriger_depart`` (« oui » par défaut).

C'est une moyenne : elle suppose que le calcul précède la chauffe d'un
délai à peu près régulier. Un jour où l'on tire beaucoup plus d'eau que
d'habitude, la chauffe durera plus longtemps que prévu ; le bilan « prévu
contre réel » du suivi le montre.
"""

from core.services import get_setting

from . import api

MODULE = "chauffe_eau"

# (réglage, défaut) — tous entiers, modifiables dans l'onglet.
REGLAGES = {
    "modele_chauffes_max": 25,   # fenêtre d'apprentissage
    "modele_chauffes_min": 12,   # en dessous, le modèle se tait
    "modele_duree_min": 15,      # bornes de la durée estimée (minutes)
    "modele_duree_max": 120,
    "modele_marge_degres": 1,    # tolérance autour de la plage connue (°C)
}


def _fr(nombre):
    """« 44,4 » plutôt que « 44.4 » : ces textes vont à l'écran et au Journal."""
    return f"{nombre:g}".replace(".", ",")


def reglage(cle):
    """Valeur entière d'un réglage du modèle ; le défaut s'il est illisible."""
    try:
        return int(float(str(get_setting(cle, module=MODULE, default=REGLAGES[cle]))))
    except (TypeError, ValueError):
        return REGLAGES[cle]


def reglages():
    return {cle: reglage(cle) for cle in REGLAGES}


def correction_active():
    """Vrai si l'écart entre le calcul et la chauffe doit être pris en compte."""
    valeur = str(get_setting("modele_corriger_depart", module=MODULE, default="oui"))
    return valeur.strip().lower() not in ("non", "false", "0", "off")


def correction_depart():
    """L'écart à appliquer à la température lue avant d'estimer.

    ``{"active", "appliquee", "ecart", "chauffes", "minimum"}`` :

    - ``ecart`` : l'écart moyen mesuré entre la température au moment du
      calcul et celle du départ (négatif : le ballon part plus froid), ou
      ``None`` tant que rien n'a été mesuré ;
    - ``appliquee`` : vrai si cet écart est réellement retiré — réglage
      actif **et** assez de chauffes mesurées (``minimum``) ;
    - ``active`` : le réglage seul, pour dire à l'écran pourquoi un écart
      mesuré n'est pas appliqué.

    Ne lève jamais : sans mesure exploitable, pas de correction.
    """
    reponse = {"active": correction_active(), "appliquee": False, "ecart": None,
               "chauffes": 0, "minimum": 0}
    try:
        from . import suivi

        bilan = suivi.bilan_depart()
        reponse.update({
            "ecart": bilan.get("ecart_moyen"),
            "chauffes": bilan["nombre"],
            "minimum": bilan["minimum"],
        })
        reponse["appliquee"] = bool(
            reponse["active"] and bilan["suffisant"] and reponse["ecart"] is not None
        )
    except Exception:
        pass
    return reponse


def chauffes_apprentissage():
    """Les dernières chauffes qui règlent le modèle, de la plus récente."""
    from ..models import ChauffeSession

    maximum = max(2, reglage("modele_chauffes_max"))
    retenues = []
    # Le tri par défaut du modèle est « plus récente d'abord ». On ne lit
    # pas tout l'historique : dix fois la fenêtre laisse largement la place
    # aux chauffes écartées.
    for session in ChauffeSession.objects.filter(fin__isnull=False)[: maximum * 10]:
        if session.apprend and session.temp_debut is not None:
            retenues.append(session)
            if len(retenues) == maximum:
                break
    return retenues


def droite(points):
    """Droite des moindres carrés sur ``[(x, y), …]`` : ``(pente, constante)``.

    ``None`` s'il n'y a pas de droite à tracer (moins de deux points, ou
    tous au même ``x``).
    """
    n = len(points)
    if n < 2:
        return None
    moy_x = sum(x for x, _y in points) / n
    moy_y = sum(y for _x, y in points) / n
    variance = sum((x - moy_x) ** 2 for x, _y in points)
    if variance <= 1e-9:
        return None
    pente = sum((x - moy_x) * (y - moy_y) for x, y in points) / variance
    return pente, moy_y - pente * moy_x


def _mediane(valeurs):
    valeurs = sorted(valeurs)
    milieu = len(valeurs) // 2
    if len(valeurs) % 2:
        return valeurs[milieu]
    return (valeurs[milieu - 1] + valeurs[milieu]) / 2


def modele():
    """Le modèle du moment, et s'il est utilisable.

    ``{"fiable", "raison", "nombre", "minimum", "pente", "minutes_par_degre",
    "constante", "temp_min", "temp_max", "puissance_w", "erreur_min"}`` :

    - ``pente`` en minutes par degré (négative : plus chaud, plus court) et
      ``constante`` en minutes — ``durée = constante + pente × température`` ;
      ``minutes_par_degre`` est la même pente, sans le signe, pour l'écran ;
    - ``temp_min`` / ``temp_max`` : plage des températures de départ vues ;
    - ``puissance_w`` : puissance de chauffe constatée, qui convertit la
      durée en énergie ;
    - ``erreur_min`` : de combien la droite s'écarte en moyenne des chauffes
      qui l'ont réglée ;
    - ``raison`` : pourquoi le modèle se tait, quand ``fiable`` est faux.
    """
    chauffes = chauffes_apprentissage()
    minimum = reglage("modele_chauffes_min")
    resultat = {
        "fiable": False, "raison": "", "nombre": len(chauffes), "minimum": minimum,
        "pente": None, "minutes_par_degre": None, "constante": None,
        "temp_min": None, "temp_max": None, "puissance_w": None, "erreur_min": None,
    }
    if len(chauffes) < max(2, minimum):
        resultat["raison"] = (
            f"{len(chauffes)} chauffe{'s' if len(chauffes) > 1 else ''} "
            f"exploitable{'s' if len(chauffes) > 1 else ''}, il en faut {minimum}"
        )
        return resultat

    points = [(s.temp_debut, s.duree_reelle_min) for s in chauffes]
    ajustement = droite(points)
    if ajustement is None:
        resultat["raison"] = "toutes les chauffes sont parties à la même température"
        return resultat

    pente, constante = ajustement
    resultat.update({
        "pente": round(pente, 3),
        "minutes_par_degre": round(-pente, 2),
        "constante": round(constante, 1),
        "temp_min": min(x for x, _y in points),
        "temp_max": max(x for x, _y in points),
        "erreur_min": round(
            sum(abs(y - (constante + pente * x)) for x, y in points) / len(points), 1
        ),
        # Énergie rapportée à la durée : la puissance de chauffe, sans la
        # supposer. La médiane ignore une chauffe mal relevée.
        "puissance_w": round(_mediane(
            [s.energie_wh / (s.duree_reelle_min / 60.0) for s in chauffes]
        )),
    })
    if pente >= 0:
        resultat["raison"] = (
            "sur ces chauffes, la durée ne baisse pas quand le ballon part plus chaud"
        )
        return resultat

    resultat["fiable"] = True
    return resultat


def _temperature_du_ballon():
    """Température milieu de cuve lue dans le cache du module, ou ``None``."""
    statut, _ts, _err = api.get_status_cached()
    try:
        return float((statut or {}).get("temperature"))
    except (TypeError, ValueError):
        return None


def estimation(temperature=None):
    """Durée et énergie de la chauffe qu'on planifie maintenant.

    Toujours un dictionnaire, pour que celui qui le lit sache **pourquoi**
    il n'y a pas d'estimation ::

        {"disponible": True, "duree_min": 50, "besoin_kwh": 2.0,
         "temperature": 52.0, "temperature_depart": 50.0, "ecart_depart": -2.0,
         "chauffes": 25, "chauffes_ecart": 12, "bornee": False, "raison": ""}

    ``disponible`` faux : ``duree_min`` et ``besoin_kwh`` valent ``None`` et
    ``raison`` dit pourquoi. ``bornee`` : la durée calculée sortait des
    bornes réglées, elle a été ramenée à la borne.

    ``temperature`` : celle du ballon **telle qu'elle est lue** (par défaut
    dans le cache du statut, comme les autres infos du module).
    ``temperature_depart`` : celle qu'on attend au départ de la chauffe, et
    sur laquelle la durée est estimée — la température lue, corrigée de
    l'écart moyen mesuré entre le calcul et le départ. ``ecart_depart`` est
    cet écart (``None`` tant qu'il n'est pas appliqué : les deux
    températures sont alors égales) et ``chauffes_ecart`` le nombre de
    chauffes qui l'ont mesuré.
    """
    m = modele()
    if temperature is None:
        temperature = _temperature_du_ballon()
    correction = correction_depart()
    depart = temperature
    if temperature is not None and correction["appliquee"]:
        depart = round(temperature + correction["ecart"], 1)
    reponse = {
        "disponible": False, "duree_min": None, "besoin_kwh": None,
        "temperature": temperature, "temperature_depart": depart,
        "ecart_depart": correction["ecart"] if correction["appliquee"] else None,
        "chauffes": m["nombre"],
        "chauffes_ecart": correction["chauffes"] if correction["appliquee"] else 0,
        "bornee": False, "raison": "",
    }
    if not m["fiable"]:
        reponse["raison"] = m["raison"]
        return reponse
    if temperature is None:
        reponse["raison"] = "température du ballon inconnue"
        return reponse

    marge = max(0, reglage("modele_marge_degres"))
    if not (m["temp_min"] - marge <= depart <= m["temp_max"] + marge):
        attendu = (
            f", attendu à {_fr(depart)} °C au départ" if correction["appliquee"] else ""
        )
        reponse["raison"] = (
            f"ballon à {_fr(temperature)} °C{attendu}, hors de la plage connue "
            f"({_fr(m['temp_min'])} à {_fr(m['temp_max'])} °C)"
        )
        return reponse

    duree = m["constante"] + m["pente"] * depart
    mini = max(1, reglage("modele_duree_min"))
    maxi = max(mini, reglage("modele_duree_max"))
    bornee = not (mini <= duree <= maxi)
    duree = int(round(max(mini, min(maxi, duree))))
    reponse.update({
        "disponible": True,
        "duree_min": duree,
        "besoin_kwh": round(m["puissance_w"] * duree / 60.0 / 1000.0, 2),
        "bornee": bornee,
    })
    return reponse
