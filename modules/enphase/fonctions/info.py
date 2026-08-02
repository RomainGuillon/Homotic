"""Fonctions d'INFO du module Énergie (contrat INFOS) — depuis les caches."""

from . import api, journee


def _live():
    data, _ts, _err = api.get_energy_cached()
    return data or {}


def production_w():
    """Production solaire instantanée (W)."""
    v = _live().get("production_w")
    return round(v) if v is not None else None


def conso_w():
    """Consommation maison instantanée (W)."""
    v = _live().get("consumption_w")
    return round(v) if v is not None else None


def import_w():
    """Puissance prise au réseau (W)."""
    v = _live().get("grid_import_w")
    return round(v) if v is not None else None


def export_w():
    """Puissance rejetée au réseau (W)."""
    v = _live().get("grid_export_w")
    return round(v) if v is not None else None


def solaire_vers_maison_w():
    """Solaire consommé directement par la maison (W)."""
    v = _live().get("solar_to_house_w")
    return round(v) if v is not None else None


def production_jour_kwh():
    """Production cumulée du jour (kWh)."""
    snap, _ts, _err, _cloud = journee.energy_snapshot()
    return round(snap["production_wh_today"] / 1000.0, 2) if snap else None


def conso_jour_kwh():
    """Consommation cumulée du jour (kWh)."""
    snap, _ts, _err, _cloud = journee.energy_snapshot()
    return round(snap["consumption_wh_today"] / 1000.0, 2) if snap else None


def production_reelle():
    """Courbe de production mesurée aujourd'hui : ``[(datetime, kW)]``.

    Liaison entre modules (type « serie », unité kW). Deux sources, la plus
    fidèle d'abord : la courbe 15 min du cloud Enlighten si le compte est
    lié, sinon l'historique local de l'Envoy. Le décalage de 450 s ramène
    l'horodatage de fin de pas au milieu du pas — un détail de format
    Enphase, qui n'a rien à faire chez le consommateur.
    """
    from datetime import datetime

    from . import cloud, historique

    try:
        if cloud.cloud_configured():
            curve, _ts, _err = cloud.get_production_curve_cached()
            points = [
                (datetime.fromtimestamp(p["end_at"] - 450).astimezone(), p["kw"])
                for p in (curve or [])
            ]
            if points:
                return points
        return historique.points_du_jour()
    except Exception:
        return None


INFOS = [
    {"nom": "production_reelle", "type": "serie", "unite": "kW",
     "description": "Production mesurée du jour (courbe)"},
    {"nom": "production_w", "description": "Production instantanée (W)"},
    {"nom": "conso_w", "description": "Consommation instantanée (W)"},
    {"nom": "import_w", "description": "Pris au réseau (W)"},
    {"nom": "export_w", "description": "Rejeté au réseau (W)"},
    {"nom": "solaire_vers_maison_w", "description": "Solaire vers la maison (W)"},
    {"nom": "production_jour_kwh", "description": "Produit aujourd'hui (kWh)"},
    {"nom": "conso_jour_kwh", "description": "Consommé aujourd'hui (kWh)"},
]


def build_info_entries():
    return [
        {"type": "valeur", **e, "fonction": f"fonctions.info.{e['nom']}"} for e in INFOS
    ]
