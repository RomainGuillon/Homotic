# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

"""Historique local de la production du jour (courbe « réel » des graphiques).

Pourquoi ici et pas via une API : la courbe « réel » du module Solaire venait
de Solcast (``estimated_actuals``), qui coûte un appel par site et n'était
rafraîchie qu'une fois le matin — la courbe s'arrêtait donc vers 9h. La
passerelle Envoy est locale, interrogée toutes les 2 minutes, sans quota :
il suffit de mémoriser chaque mesure pour reconstituer la courbe réelle de la
journée, gratuitement et en continu.

Stockage : un seul réglage ``courbe_jour`` du module, remis à zéro au
changement de jour. Un point tous les 5 minutes (288 au maximum) : la clé
« HH:MM » écrase le point du même pas si plusieurs mesures tombent dedans.
"""

import json
from datetime import date, datetime, timedelta

from core.services import get_setting, set_setting

MODULE = "enphase"
CLE = "courbe_jour"
PAS_MINUTES = 5


def _charger():
    raw = get_setting(CLE, module=MODULE)
    if not raw:
        return {"jour": "", "points": {}}
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return {"jour": "", "points": {}}
    if not isinstance(payload.get("points"), dict):
        payload["points"] = {}
    return payload


def _kw(watts):
    try:
        return round(float(watts) / 1000.0, 3)
    except (TypeError, ValueError):
        return 0.0


def enregistrer(data_energie):
    """Mémorise un point de mesure du jour.

    ``data_energie`` est le dict de ``api.get_energy()``. On stocke un
    triplet [production, consommation, réseau] en kW, le réseau étant signé :
    positif = importé, négatif = exporté. Import et export s'en déduisent,
    inutile de stocker les deux.

    Accepte aussi un simple nombre de watts (ancienne signature) : seule la
    production est alors enregistrée.
    """
    if isinstance(data_energie, dict):
        prod = _kw(data_energie.get("production_w"))
        conso = _kw(data_energie.get("consumption_w"))
        reseau = _kw(data_energie.get("net_w"))
    else:
        prod, conso, reseau = _kw(data_energie), 0.0, 0.0

    data = _charger()
    aujourdhui = str(date.today())
    if data.get("jour") != aujourdhui:  # nouveau jour : on repart de zéro
        data = {"jour": aujourdhui, "points": {}}

    now = datetime.now()
    cle = f"{now.hour:02d}:{now.minute - now.minute % PAS_MINUTES:02d}"
    data["points"][cle] = [prod, conso, reseau]
    set_setting(CLE, json.dumps(data), module=MODULE)


def _triplet(valeur):
    """Normalise un point stocké (ancien format = production seule)."""
    if isinstance(valeur, list):
        valeur = valeur + [0.0, 0.0]
        return float(valeur[0]), float(valeur[1]), float(valeur[2])
    try:
        return float(valeur), 0.0, 0.0
    except (TypeError, ValueError):
        return 0.0, 0.0, 0.0


def mesures_du_jour():
    """Mesures d'aujourd'hui : [(datetime local, prod, conso, réseau)] triées.

    ``réseau`` est signé : positif = importé du réseau, négatif = exporté.
    """
    data = _charger()
    today = date.today()
    if data.get("jour") != str(today):
        return []  # historique d'un autre jour : rien à montrer

    points = []
    for cle, valeur in sorted(data.get("points", {}).items()):
        try:
            h, m = (int(x) for x in cle.split(":"))
        except (ValueError, TypeError):
            continue
        prod, conso, reseau = _triplet(valeur)
        quand = datetime(today.year, today.month, today.day, h, m).astimezone()
        points.append((quand, prod, conso, reseau))
    return points


def points_du_jour():
    """Production mesurée aujourd'hui : [(datetime local, kW)] triée.

    Conservé pour la courbe « réel » du module Solaire.
    """
    return [(t, prod) for t, prod, _conso, _reseau in mesures_du_jour()]


def _maintenant():
    """Heure locale courante — isolée pour pouvoir être figée dans les tests."""
    return datetime.now().astimezone()


def points_par_pas(pas_minutes=15, depuis=None):
    """Production mesurée aujourd'hui, moyennée par pas : [(datetime, kW)].

    Sert à **prolonger une courbe venue d'ailleurs** (le cloud Enlighten,
    relevé toutes les deux heures pour tenir son quota) avec ce que l'Envoy
    a mesuré depuis : même pas, même convention d'horodatage — le milieu du
    pas —, pour que les deux morceaux se raccordent sans couture et que les
    kWh par heure restent justes (ils se calculent en kW × durée du pas, et
    un mélange de pas de 15 et de 5 minutes les fausserait).

    - ``depuis`` : ne rend que les pas qui commencent à cette heure ou
      après (typiquement la fin du dernier pas connu du cloud) ;
    - un pas encore en cours n'est pas rendu : sa moyenne changerait à
      chaque mesure, et il compterait pour un pas entier dans les kWh.
    """
    pas = timedelta(minutes=pas_minutes)
    maintenant = _maintenant()

    seaux = {}
    for quand, prod, _conso, _reseau in mesures_du_jour():
        debut = quand - timedelta(minutes=quand.minute % pas_minutes)
        if depuis is not None and debut < depuis:
            continue
        if debut + pas > maintenant:
            continue
        seaux.setdefault(debut, []).append(prod)

    return [
        (debut + pas / 2, round(sum(valeurs) / len(valeurs), 3))
        for debut, valeurs in sorted(seaux.items())
    ]
