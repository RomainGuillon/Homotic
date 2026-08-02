"""API Cloud Tuya : capteurs température/humidité et prises connectées.

Repris de la v1 (tuya/tuya_client_v2.py + sensors.py), adapté :
identifiants en base (module « tuya »), caches en base avec repli sur la
dernière valeur connue.
"""

import hashlib
import hmac
import json
import time as time_mod
from datetime import datetime, timedelta

import requests

from core.models import LogEntry
from core.services import get_setting, journal, set_setting

MODULE = "tuya"

# Appareils à masquer, par mot-clé dans le nom (device virtuel, passerelle).
EXCLUDE_KEYWORDS = ("vdevo", "gateway")


# ----------------------------------------------------------------------
# Paramètres
# ----------------------------------------------------------------------

def credentials():
    return (
        get_setting("access_id", module=MODULE, default=""),
        get_setting("access_secret", module=MODULE, default=""),
    )


def configured():
    cid, secret = credentials()
    return bool(cid and secret)


def base_url():
    region = get_setting("region", module=MODULE, default="eu") or "eu"
    return f"https://openapi.tuya{region}.com"


# ----------------------------------------------------------------------
# Client HTTP (signature v2) — repris de la v1
# ----------------------------------------------------------------------

class TuyaClientV2:
    def __init__(self, client_id, client_secret, base_url):
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token = None
        self.base_url = base_url

    def _sign(self, method, path, query="", body=""):
        t = str(int(time_mod.time() * 1000))
        body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        string_to_sign = "\n".join([
            method,
            body_hash,
            "",
            path + ("?" + query if query else ""),
        ])
        sign_str = self.client_id + (self.access_token or "") + t + string_to_sign
        sign = hmac.new(
            self.client_secret.encode("utf-8"),
            sign_str.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest().upper()
        return sign, t

    def _headers(self, method, path, query="", body=""):
        sign, t = self._sign(method, path, query, body)
        headers = {
            "client_id": self.client_id,
            "sign": sign,
            "t": t,
            "sign_method": "HMAC-SHA256",
            "Content-Type": "application/json",
        }
        if self.access_token:
            headers["access_token"] = self.access_token
        return headers

    def get_token(self):
        path = "/v1.0/token?grant_type=1"
        r = requests.get(self.base_url + path, headers=self._headers("GET", path), timeout=15)
        payload = r.json()
        if not payload.get("success", False):
            raise RuntimeError(f"Authentification Tuya refusée : {payload.get('msg', payload)}")
        self.access_token = payload["result"]["access_token"]
        return self.access_token

    def get_devices_v2(self):
        path = "/v2.0/cloud/thing/device"
        query = "page_size=20"
        headers = self._headers("GET", path, query)
        r = requests.get(self.base_url + path + "?" + query, headers=headers, timeout=15)
        return r.json()

    def get_device_properties(self, device_id):
        path = f"/v2.0/cloud/thing/{device_id}/shadow/properties"
        r = requests.get(self.base_url + path, headers=self._headers("GET", path), timeout=15)
        return r.json()

    def get_device_timers(self, device_id):
        path = f"/v1.0/devices/{device_id}/timers"
        r = requests.get(self.base_url + path, headers=self._headers("GET", path), timeout=15)
        return r.json()

    def send_commands(self, device_id, commands):
        path = f"/v1.0/devices/{device_id}/commands"
        body = json.dumps({"commands": commands}, separators=(",", ":"))
        headers = self._headers("POST", path, "", body)
        r = requests.post(self.base_url + path, headers=headers, data=body, timeout=15)
        return r.json()


def _make_client():
    cid, secret = credentials()
    if not cid or not secret:
        raise RuntimeError(
            "Identifiants Tuya manquants : renseigner l'Access ID et le "
            "secret dans le paramétrage de l'onglet Capteurs."
        )
    client = TuyaClientV2(cid, secret, base_url())
    client.get_token()
    return client


# ----------------------------------------------------------------------
# Interprétation (reprise de la v1)
# ----------------------------------------------------------------------

def _is_excluded(name):
    low = str(name).lower()
    return any(k in low for k in EXCLUDE_KEYWORDS)


def _device_list(payload):
    result = payload.get("result", [])
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        return result.get("list", result.get("devices", []))
    return []


def _scale(value):
    """Tuya renvoie souvent les mesures en dixièmes (235 -> 23.5)."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return value
    return v / 10 if abs(v) >= 100 else v


def _extract_measures(properties):
    temperature = humidity = None
    for prop in properties:
        code = str(prop.get("code", "")).lower()
        value = prop.get("value")
        if "temp" in code and temperature is None:
            temperature = _scale(value)
        elif "humid" in code and humidity is None:
            humidity = _scale(value)
    return temperature, humidity


def _first_switch(properties):
    for prop in properties:
        code = str(prop.get("code", ""))
        if code.lower().startswith("switch") and isinstance(prop.get("value"), bool):
            return code, prop["value"]
    return None, None


def _collect_timers(obj, out):
    if isinstance(obj, dict):
        if "time" in obj and "functions" in obj:
            out.append(obj)
        for value in obj.values():
            _collect_timers(value, out)
    elif isinstance(obj, list):
        for value in obj:
            _collect_timers(value, out)


def _parse_timers(payload):
    timers = []
    _collect_timers(payload, timers)
    slots = []
    for tm in timers:
        if tm.get("status", 1) == 0:
            continue
        time_s = tm.get("time")
        if not time_s:
            continue
        on = None
        for func in tm.get("functions", []):
            if str(func.get("code", "")).lower().startswith("switch"):
                on = bool(func.get("value"))
                break
        slots.append({"time": time_s, "on": on, "loops": tm.get("loops")})
    slots.sort(key=lambda s: s["time"])
    return slots


# ----------------------------------------------------------------------
# Lectures
# ----------------------------------------------------------------------

def get_sensors():
    """Capteurs température/humidité : [{id, name, online, temperature, humidity}]."""
    client = _make_client()
    sensors = []
    for dev in _device_list(client.get_devices_v2()):
        device_id = dev.get("id")
        name = dev.get("customName") or dev.get("custom_name") or dev.get("name") or device_id
        if not device_id or _is_excluded(name):
            continue
        try:
            result = client.get_device_properties(device_id).get("result", {})
            properties = result.get("properties", []) if isinstance(result, dict) else []
        except Exception:
            continue
        temperature, humidity = _extract_measures(properties)
        if temperature is not None or humidity is not None:
            sensors.append({
                "id": device_id,
                "name": name,
                "online": dev.get("isOnline", dev.get("online", True)),
                "temperature": temperature,
                "humidity": humidity,
            })
    return sensors


def get_plugs():
    """Prises connectées : [{id, name, online, switch_code, state, schedule_slots}]."""
    client = _make_client()
    plugs = []
    for dev in _device_list(client.get_devices_v2()):
        device_id = dev.get("id")
        name = dev.get("customName") or dev.get("custom_name") or dev.get("name") or device_id
        if not device_id or _is_excluded(name):
            continue
        try:
            result = client.get_device_properties(device_id).get("result", {})
            properties = result.get("properties", []) if isinstance(result, dict) else []
        except Exception:
            continue
        switch_code, state = _first_switch(properties)
        if switch_code is None:
            continue  # pas une prise pilotable
        try:
            slots = _parse_timers(client.get_device_timers(device_id))
        except Exception:
            slots = []
        plugs.append({
            "id": device_id,
            "name": name,
            "online": dev.get("isOnline", dev.get("online", True)),
            "switch_code": switch_code,
            "state": state,
            "schedule_slots": slots,
        })
    return plugs


# ----------------------------------------------------------------------
# Commandes
# ----------------------------------------------------------------------

def set_plug(device_id, switch_code, on, name=None):
    """Allume/éteint une prise, journalise et rafraîchit le cache."""
    client = _make_client()
    payload = client.send_commands(device_id, [{"code": switch_code, "value": bool(on)}])
    if not payload.get("success", False):
        raise RuntimeError(payload.get("msg") or str(payload))
    journal(f"Prise « {name or device_id} » -> {'ON' if on else 'OFF'}", module=MODULE)
    time_mod.sleep(1)
    try:
        get_plugs_cached(force=True)
    except Exception:
        pass
    return payload


def slug(name):
    """« Prise Machine à laver » -> « prise_machine_a_laver »."""
    import unicodedata

    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    s = "".join(c if c.isalnum() else "_" for c in s.lower())
    while "__" in s:
        s = s.replace("__", "_")
    return s.strip("_")


def set_plug_by_slug(plug_slug, on):
    """Allume/éteint une prise repérée par le slug de son nom (scénarios)."""
    plugs, _ts, _err = get_plugs_cached()
    for p in plugs or []:
        if slug(p["name"]) == plug_slug:
            return set_plug(p["id"], p["switch_code"], on, name=p["name"])
    raise RuntimeError(f"Prise « {plug_slug} » introuvable")


# ----------------------------------------------------------------------
# Caches en base
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
        if now - cached_ts < timedelta(minutes=ttl_minutes):
            return cached_data, cached_ts, ""

    try:
        data = fetch()
    except Exception as exc:
        journal(f"Erreur API Tuya : {exc}", module=MODULE, level=LogEntry.ERROR)
        if cached_data is not None:
            return cached_data, cached_ts, f"API indisponible ({exc}) — dernière valeur connue."
        return None, None, str(exc)

    set_setting(key, json.dumps({"ts": now.isoformat(), "data": data}), module=MODULE)
    return data, now, ""


def get_sensors_cached(force=False):
    return _cached("cache_sensors", get_sensors, 10, force=force)


def get_plugs_cached(force=False):
    return _cached("cache_plugs", get_plugs, 10, force=force)


def tache_actualiser():
    """Tâche périodique (scheduler) : capteurs + prises."""
    if not configured():
        return
    get_sensors_cached(force=True)
    get_plugs_cached(force=True)
