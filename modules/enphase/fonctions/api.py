"""Passerelle Enphase Envoy (locale) : production, consommation, flux réseau.

Repris de la v1 (enphase/energy.py), adapté : configuration, token JWT et
référence de début de journée stockés en base. La tâche périodique publie
aussi des variables globales pour les scénarios.
"""

import json
from datetime import datetime, timedelta

import requests

from core.models import LogEntry
from core.services import get_setting, journal, set_setting, set_variable

MODULE = "enphase"

requests.packages.urllib3.disable_warnings()  # certificat auto-signé local


# ----------------------------------------------------------------------
# Paramètres
# ----------------------------------------------------------------------

def config():
    return {
        "username": get_setting("username", module=MODULE, default=""),
        "password": get_setting("password", module=MODULE, default=""),
        "serial": get_setting("envoy_serial", module=MODULE, default=""),
        "host": get_setting("envoy_host", module=MODULE, default=""),
    }


def configured():
    return all(config().values())


def _check_config():
    cfg = config()
    missing = [k for k, v in cfg.items() if not v]
    if missing:
        raise RuntimeError(
            "Configuration Enphase incomplète : renseigner "
            + ", ".join(missing)
            + " dans le paramétrage de l'onglet Énergie."
        )
    return cfg


# ----------------------------------------------------------------------
# Token JWT (login Enlighten, mémorisé en base)
# ----------------------------------------------------------------------

def _get_new_token(cfg):
    login = requests.post(
        "https://enlighten.enphaseenergy.com/login/login.json",
        data={"user[email]": cfg["username"], "user[password]": cfg["password"]},
        timeout=15,
    )
    login.raise_for_status()
    session_id = login.json()["session_id"]

    token_resp = requests.post(
        "https://entrez.enphaseenergy.com/tokens",
        json={
            "session_id": session_id,
            "serial_num": cfg["serial"],
            "username": cfg["username"],
        },
        timeout=15,
    )
    token_resp.raise_for_status()
    return token_resp.text.strip()


def _get_json(path):
    """GET https://<envoy><path> avec token (renouvelé si 401)."""
    cfg = _check_config()
    token = get_setting("envoy_token", module=MODULE, default="")
    if not token:
        token = _get_new_token(cfg)
        set_setting("envoy_token", token, module=MODULE, secret=True)

    url = f"https://{cfg['host']}{path}"

    def _fetch(tok):
        return requests.get(
            url, headers={"Authorization": f"Bearer {tok}"}, verify=False, timeout=10
        )

    resp = _fetch(token)
    if resp.status_code == 401:
        token = _get_new_token(cfg)
        set_setting("envoy_token", token, module=MODULE, secret=True)
        resp = _fetch(token)

    resp.raise_for_status()
    return resp.json()


# ----------------------------------------------------------------------
# Lecture des compteurs (repris v1)
# ----------------------------------------------------------------------

def _find(items, key, value):
    for it in items:
        if it.get(key) == value:
            return it
    return {}


def _read_meters():
    """Production lue sur les micro-onduleurs, conso/réseau sur les pinces."""
    data = _get_json("/production.json")
    production = data.get("production", [])
    prod = _find(production, "type", "inverters") or _find(production, "measurementType", "production")
    conso = _find(data.get("consumption", []), "measurementType", "total-consumption")
    net = _find(data.get("consumption", []), "measurementType", "net-consumption")
    return {
        "prod_w": float(prod.get("wNow", 0) or 0),
        "conso_w": float(conso.get("wNow", 0) or 0),
        "net_w": float(net.get("wNow", 0) or 0),
        "prod_life": float(prod.get("whLifetime", 0) or 0),
        "conso_life": float(conso.get("whLifetime", 0) or 0),
        "net_life": float(net.get("whLifetime", 0) or 0),
    }


def _meter_gross():
    """Compteurs bruts réseau (Wh) : (import_life, export_life) ou (None, None)."""
    try:
        meters = _get_json("/ivp/meters")
        readings = _get_json("/ivp/meters/readings")
    except Exception:
        return None, None

    net_eid = None
    for m in meters if isinstance(meters, list) else []:
        if m.get("measurementType") == "net-consumption":
            net_eid = m.get("eid")
            break
    if net_eid is None:
        return None, None

    for r in readings if isinstance(readings, list) else []:
        if r.get("eid") == net_eid:
            imp = r.get("actEnergyDlvd")
            exp = r.get("actEnergyRcvd")
            if imp is not None and exp is not None:
                return float(imp), float(exp)
    return None, None


# ----------------------------------------------------------------------
# Référence de début de journée (en base, reprise de la logique v1)
# ----------------------------------------------------------------------

def _load_state():
    raw = get_setting("daily_state", module=MODULE)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def _save_state(state):
    set_setting("daily_state", json.dumps(state), module=MODULE)


def get_energy():
    """Instantané + cumuls du jour (lifetime - référence de début de journée)."""
    m = _read_meters()
    today = datetime.now().strftime("%Y-%m-%d")

    imp_life, exp_life = _meter_gross()

    state = _load_state()
    current = {"prod": m["prod_life"], "conso": m["conso_life"], "net": m["net_life"]}
    if imp_life is not None and exp_life is not None:
        current["imp"] = imp_life
        current["exp"] = exp_life

    if not state or state.get("date") != today:
        # Nouveau jour : référence = dernier relevé de la veille, sinon l'actuel
        base = state.get("last", current) if state else current
        state = {"date": today, "base": base, "last": current}
    else:
        state["last"] = current
    _save_state(state)

    base = state["base"]
    production_today = max(current["prod"] - base["prod"], 0.0)
    consumption_today = max(current["conso"] - base["conso"], 0.0)
    grid_today = current["net"] - base["net"]

    if "imp" in current and "imp" in base:
        import_today = max(current["imp"] - base["imp"], 0.0)
        export_today = max(current["exp"] - base["exp"], 0.0)
    else:
        import_today = max(grid_today, 0.0)
        export_today = max(-grid_today, 0.0)

    net_w = m["net_w"]
    prod_w = max(m["prod_w"], 0.0)  # bruit de mesure la nuit
    return {
        "production_w": prod_w,
        "production_wh_today": production_today,
        "consumption_w": m["conso_w"],
        "consumption_wh_today": consumption_today,
        "net_w": net_w,
        "import_wh_today": import_today,
        "export_wh_today": export_today,
        "grid_import_w": max(net_w, 0.0),
        "grid_export_w": max(-net_w, 0.0),
        "solar_to_house_w": max(min(prod_w, m["conso_w"]), 0.0),
    }


# ----------------------------------------------------------------------
# Cache + tâche périodique
# ----------------------------------------------------------------------

def get_energy_cached(force=False, ttl_minutes=2):
    now = datetime.now()
    raw = get_setting("cache_energy", module=MODULE)
    cached_data, cached_ts = None, None
    if raw:
        try:
            payload = json.loads(raw)
            cached_ts = datetime.fromisoformat(payload["ts"])
            cached_data = payload["data"]
        except (ValueError, KeyError, TypeError):
            pass

    if cached_data is not None and not force:
        if now - cached_ts < timedelta(minutes=ttl_minutes):
            return cached_data, cached_ts, ""

    try:
        data = get_energy()
    except Exception as exc:
        journal(f"Erreur Envoy : {exc}", module=MODULE, level=LogEntry.ERROR)
        if cached_data is not None:
            return cached_data, cached_ts, f"Passerelle injoignable ({exc}) — dernière valeur connue."
        return None, None, str(exc)

    set_setting("cache_energy", json.dumps({"ts": now.isoformat(), "data": data}), module=MODULE)
    # Chaque mesure fraîche alimente la courbe réelle du jour (voir
    # historique.py) : c'est elle qui trace le « réel » des graphiques du
    # module Solaire, sans consommer de quota API.
    try:
        from . import historique

        historique.enregistrer(data)
    except Exception:
        pass  # l'historique ne doit jamais faire échouer une lecture Envoy
    return data, now, ""


def tache_actualiser():
    """Rafraîchit les mesures et publie les variables pour les scénarios."""
    if not configured():
        return
    data, _ts, err = get_energy_cached(force=True)
    if data is None:
        return
    set_variable("enphase_production_w", str(round(data["production_w"])))
    set_variable("enphase_conso_w", str(round(data["consumption_w"])))
    set_variable("enphase_import_w", str(round(data["grid_import_w"])))
    set_variable("enphase_export_w", str(round(data["grid_export_w"])))
