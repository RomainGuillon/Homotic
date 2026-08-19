# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

"""Cumuls et coût de la journée (repris de la v1 web/services.py).

- ``energy_snapshot()`` : instantané local (Envoy) + cumuls du jour remplacés
  par ceux du cloud quand ils sont disponibles (plus précis, avec le détail
  15 min qui permet le chiffrage par tranche).
- ``daily_cost()`` : coût EDF du jour tranche par tranche (couleur Tempo ×
  heures pleines/creuses, tarifs du module tempo), revente séparée.
"""

import calendar as _calendar
from datetime import date, datetime

from . import api, cloud

# Les libellés et couleurs d'affichage viennent désormais du fournisseur de
# tarifs (besoin « tarifs_jour ») : ce module n'a plus à connaître la
# nomenclature Tempo. Seul l'ordre d'affichage des lignes reste ici, c'est
# une préférence de présentation, pas une donnée du fournisseur.
_BUCKET_ORDER = {"BLUE": 0, "WHITE": 1, "RED": 2}


def energy_snapshot():
    """Retourne (data, ts, erreur, cloud_ok).

    ``data`` reprend la forme de get_energy(), cumuls du jour remplacés par
    ceux du cloud si disponibles (+ ``grid_intervals`` pour le coût).
    """
    data, ts, err = api.get_energy_cached()
    if data is None:
        return None, ts, err, False

    data = dict(data)
    cloud_ok = False
    if cloud.cloud_configured():
        daily, _dts, derr = cloud.get_daily_totals_cached()
        if daily:
            data["production_wh_today"] = daily["production_wh_today"]
            data["consumption_wh_today"] = daily["consumption_wh_today"]
            data["import_wh_today"] = daily["import_wh_today"]
            data["export_wh_today"] = daily["export_wh_today"]
            data["grid_intervals"] = daily.get("intervals", [])
            cloud_ok = True
        elif not err:
            err = derr
    return data, ts, err, cloud_ok


def daily_cost(energy):
    """Coût de la journée par tranche. Retourne un dict, ou None.

    Les tarifs viennent du besoin ``tarifs_jour`` (voir ``conf.py`` et
    ``docs/09-liaisons-entre-modules.md``) : ce module ne sait pas qu'un
    module « tempo » existe, il sait qu'il lui faut une tarification du jour.
    Pas de liaison, ou pas de tarif connu : pas de coût affiché, le reste de
    la page continue de fonctionner.
    """
    from core.liaisons import lire_besoin

    tarifs, _err = lire_besoin(api.MODULE, "tarifs_jour")
    if not tarifs:
        return None

    prices = tarifs.get("prix") or {}
    c_today = tarifs.get("couleur")
    c_yest = tarifs.get("couleur_veille")
    today = date.today()
    if c_today is None:
        return None

    libelles = tarifs.get("libelles") or {}
    hex_couleurs = tarifs.get("couleurs_hex") or {}
    hc_debut, hc_fin = tarifs.get("hc_debut", 22.0), tarifs.get("hc_fin", 6.0)

    def _period(hour):
        if hc_debut > hc_fin:  # ex : 22h -> 6h
            return "HC" if (hour >= hc_debut or hour < hc_fin) else "HP"
        return "HC" if (hc_debut <= hour < hc_fin) else "HP"

    import_kwh = energy["import_wh_today"] / 1000.0
    export_kwh = energy["export_wh_today"] / 1000.0

    # Chaque pas de 15 min valorisé au prix de sa tranche (couleur à 6 h)
    buckets = {}
    for interval in energy.get("grid_intervals") or []:
        dt = datetime.fromtimestamp(interval["end_at"] - 450)  # milieu du pas
        color = c_yest if dt.hour < 6 else c_today
        buckets[(color, _period(dt.hour + dt.minute / 60.0))] = (
            buckets.get((color, _period(dt.hour + dt.minute / 60.0)), 0.0)
            + interval["import_wh"]
        )

    cost_import = 0.0
    rows = []
    priced = bool(buckets)
    for (color, period), wh in sorted(
        buckets.items(), key=lambda kv: _BUCKET_ORDER.get(kv[0][0], 9)
    ):
        price = (prices.get(color) or {}).get(period)
        kwh = wh / 1000.0
        if price is None:
            priced = False
            continue
        line = kwh * price
        cost_import += line
        rows.append({
            "label": f"{libelles.get(color, '?')} {period}",
            "color_hex": hex_couleurs.get(color, "#5a6473"),
            "kwh": round(kwh, 2),
            "price": price,
            "cost": round(line, 2),
        })

    if not priced:
        # Repli : détail 15 min indisponible -> prix courant appliqué
        price = (prices.get(c_today) or {}).get(tarifs.get("periode_courante"))
        if price is None:
            return None
        cost_import = import_kwh * price
        rows = []

    days_in_month = _calendar.monthrange(today.year, today.month)[1]
    subscription = (tarifs.get("abonnement_mensuel") or 0.0) / days_in_month
    edf = subscription + cost_import
    resale = export_kwh * (tarifs.get("prix_revente") or 0.0)

    return {
        "import_kwh": import_kwh,
        "export_kwh": export_kwh,
        "subscription": subscription,
        "cost_import": cost_import,
        "edf": edf,
        "resale": resale,
        "net": edf - resale,
        "rows": rows,
    }
