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
from datetime import date, datetime

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
