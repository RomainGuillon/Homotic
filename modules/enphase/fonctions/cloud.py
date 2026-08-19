# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

"""API cloud Enphase Enlighten (v4) : cumuls du jour et courbes 15 min.

Repris de la v1 (enphase_cloud/auth.py + cloud.py), adapté : configuration
et jetons OAuth en base. Les jetons de la v1 (enphase_cloud/enphase_tokens.json)
sont importés automatiquement au premier passage — pas besoin de refaire
l'autorisation.
"""

import json
import time as time_mod
from datetime import date, datetime, timedelta

import requests
from requests.auth import HTTPBasicAuth

from core.models import LogEntry
from core.services import get_setting, journal, set_setting

MODULE = "enphase"

REDIRECT_URI = "https://api.enphaseenergy.com/oauth/redirect_uri"
AUTH_BASE = "https://api.enphaseenergy.com/oauth"
API_BASE = "https://api.enphaseenergy.com/api/v4"

ACCESS_TTL_S = 23 * 3600  # jeton d'accès ~24 h, rafraîchi avant expiration


# ----------------------------------------------------------------------
# Configuration & jetons (en base)
# ----------------------------------------------------------------------

def cloud_config():
    return {
        "api_key": get_setting("cloud_api_key", module=MODULE, default=""),
        "client_id": get_setting("cloud_client_id", module=MODULE, default=""),
        "client_secret": get_setting("cloud_client_secret", module=MODULE, default=""),
        "system_id": get_setting("cloud_system_id", module=MODULE, default=""),
    }


def _load_tokens():
    raw = get_setting("cloud_tokens", module=MODULE)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def _save_tokens(data):
    data["obtained_at"] = int(time_mod.time())
    set_setting("cloud_tokens", json.dumps(data), module=MODULE, secret=True)


def cloud_configured():
    cfg = cloud_config()
    return bool(
        cfg["api_key"] and cfg["client_id"] and cfg["client_secret"]
        and cfg["system_id"] and _load_tokens()
    )


def _check_app_config():
    cfg = cloud_config()
    missing = [k for k in ("api_key", "client_id", "client_secret") if not cfg[k]]
    if missing:
        raise RuntimeError("Configuration Enphase cloud manquante : " + ", ".join(missing))
    return cfg


def authorize_url():
    cfg = _check_app_config()
    return (f"{AUTH_BASE}/authorize?response_type=code"
            f"&client_id={cfg['client_id']}&redirect_uri={REDIRECT_URI}")


def exchange_code(code):
    """Échange le code d'autorisation contre des jetons (enregistrés en base)."""
    cfg = _check_app_config()
    r = requests.post(
        f"{AUTH_BASE}/token",
        params={"grant_type": "authorization_code", "redirect_uri": REDIRECT_URI, "code": code},
        auth=HTTPBasicAuth(cfg["client_id"], cfg["client_secret"]),
        timeout=20,
    )
    r.raise_for_status()
    data = r.json()
    _save_tokens(data)
    journal("Compte Enphase cloud lié (jetons OAuth enregistrés)", module=MODULE)
    return data


def _refresh_tokens():
    cfg = _check_app_config()
    tokens = _load_tokens()
    if not tokens or "refresh_token" not in tokens:
        raise RuntimeError("Aucun refresh token : relancer l'autorisation cloud.")
    r = requests.post(
        f"{AUTH_BASE}/token",
        params={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]},
        auth=HTTPBasicAuth(cfg["client_id"], cfg["client_secret"]),
        timeout=20,
    )
    r.raise_for_status()
    data = r.json()
    _save_tokens(data)
    return data


def _get_access_token():
    tokens = _load_tokens()
    if not tokens:
        raise RuntimeError("Système non autorisé : lier le compte dans le paramétrage cloud.")
    if int(time_mod.time()) - tokens.get("obtained_at", 0) >= ACCESS_TTL_S:
        tokens = _refresh_tokens()
    return tokens["access_token"]


def list_systems():
    cfg = _check_app_config()
    token = _get_access_token()
    r = requests.get(
        f"{API_BASE}/systems",
        params={"key": cfg["api_key"]},
        headers={"Authorization": f"Bearer {token}"},
        timeout=20,
    )
    r.raise_for_status()
    return r.json()


# ----------------------------------------------------------------------
# Accès générique + lectures (repris v1)
# ----------------------------------------------------------------------

def _get(path, params=None):
    cfg = _check_app_config()
    if not cfg["system_id"]:
        raise RuntimeError("system_id Enphase manquant (paramétrage cloud).")
    token = _get_access_token()
    query = {"key": cfg["api_key"]}
    if params:
        query.update(params)
    r = requests.get(
        f"{API_BASE}/systems/{cfg['system_id']}{path}",
        params=query,
        headers={"Authorization": f"Bearer {token}"},
        timeout=20,
    )
    r.raise_for_status()
    return r.json()


def _sum_intervals(payload, key):
    total = 0.0
    by_time = {}
    for interval in payload.get("intervals", []):
        value = interval.get(key)
        if value is not None:
            v = float(value)
            by_time[interval.get("end_at")] = v
            total += v
    return total, by_time


def get_daily_totals():
    """Cumuls du jour (Wh) : production, consommation, import, export +
    détail 15 min pour le chiffrage par tranche."""
    today = date.today().isoformat()

    summary = _get("/summary")
    consumption = _get("/consumption_lifetime", {"start_date": today})
    prod_tel = _get("/telemetry/production_meter", {"granularity": "day"})
    cons_tel = _get("/telemetry/consumption_meter", {"granularity": "day"})

    production_today = float(summary.get("energy_today", 0) or 0)
    conso_series = consumption.get("consumption", [])
    consumption_today = float(conso_series[0]) if conso_series else 0.0

    _, prod_by = _sum_intervals(prod_tel, "wh_del")
    _, cons_by = _sum_intervals(cons_tel, "enwh")

    import_today = export_today = 0.0
    intervals = []
    for end_at, conso in sorted(cons_by.items()):
        prod = prod_by.get(end_at, 0.0)
        net = conso - prod
        imp = net if net > 0 else 0.0
        exp = -net if net < 0 else 0.0
        import_today += imp
        export_today += exp
        intervals.append({"end_at": end_at, "import_wh": imp, "export_wh": exp})

    return {
        "production_wh_today": production_today,
        "consumption_wh_today": consumption_today,
        "import_wh_today": import_today,
        "export_wh_today": export_today,
        "intervals": intervals,
    }


def get_production_curve():
    """Production réelle du jour par pas de 15 min : [{end_at, kw}]."""
    tel = _get("/telemetry/production_meter", {"granularity": "day"})
    points = []
    for interval in tel.get("intervals", []):
        wh = interval.get("wh_del")
        end_at = interval.get("end_at")
        if wh is None or end_at is None:
            continue
        points.append({"end_at": end_at, "kw": float(wh) / 250.0})
    return points


def get_consumption_curve():
    """Consommation réelle du jour par pas de 15 min : [{end_at, kw}]."""
    tel = _get("/telemetry/consumption_meter", {"granularity": "day"})
    points = []
    for interval in tel.get("intervals", []):
        wh = interval.get("enwh")
        end_at = interval.get("end_at")
        if wh is None or end_at is None:
            continue
        points.append({"end_at": end_at, "kw": float(wh) / 250.0})
    return points


# ----------------------------------------------------------------------
# Caches en base (repli sur la dernière valeur connue)
# ----------------------------------------------------------------------

def _cached(key, fetch, ttl_minutes, force=False):
    now = datetime.now()
    raw = get_setting(key, module=MODULE)
    cached_data, cached_ts = None, None
    if raw:
        try:
            payload = json.loads(raw)
            cached_ts = datetime.fromisoformat(payload["ts"])
            cached_data = payload["data"]
        except (ValueError, KeyError, TypeError):
            pass

    if cached_data is not None and not force:
        # Un cache d'hier ne vaut rien pour des données « du jour »
        if cached_ts.date() == now.date() and now - cached_ts < timedelta(minutes=ttl_minutes):
            return cached_data, cached_ts, ""

    try:
        data = fetch()
    except Exception as exc:
        journal(f"Erreur Enphase cloud : {exc}", module=MODULE, level=LogEntry.ERROR)
        if cached_data is not None and cached_ts.date() == now.date():
            return cached_data, cached_ts, f"Cloud indisponible ({exc}) — dernière valeur connue."
        return None, None, str(exc)

    set_setting(key, json.dumps({"ts": now.isoformat(), "data": data}), module=MODULE)
    return data, now, ""


def get_daily_totals_cached(force=False):
    return _cached("cache_cloud_daily", get_daily_totals, 10, force=force)


def get_production_curve_cached(force=False):
    return _cached("cache_cloud_prod_curve", get_production_curve, 15, force=force)


def get_consumption_curve_cached(force=False):
    return _cached("cache_cloud_cons_curve", get_consumption_curve, 15, force=force)
