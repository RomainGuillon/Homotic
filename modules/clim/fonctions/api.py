"""Climatisations Hitachi Hi-Kumo via l'API Overkiz (pyoverkiz).

Repris de la v1 (clim/clim_client.py), adapté : identifiants en base,
unités découvertes dynamiquement (plus de deviceURL codés en dur),
cache avec repli sur la dernière valeur connue.
"""

import asyncio
import json
import time as time_mod
import unicodedata
from datetime import datetime, timedelta

from core.models import LogEntry
from core.services import get_setting, journal, set_setting

MODULE = "clim"

# States Overkiz -> clés lisibles (repris v1)
STATE_MAP = {
    "power": "hlrrwifi:MainOperationState",
    "temperature": "core:TargetTemperatureState",
    "fan_mode": "hlrrwifi:FanSpeedState",
    "mode": "hlrrwifi:ModeChangeState",
    "swing": "hlrrwifi:SwingState",
    "leave_home": "hlrrwifi:LeaveHomeState",
    "room_temperature": "hlrrwifi:RoomTemperatureState",
}

MODE_OPTIONS = [
    ("auto", "Auto"),
    ("cooling", "Froid"),
    ("heating", "Chauffage"),
    ("dehumidify", "Déshumidification"),
    ("fan", "Ventilation"),
]
FAN_OPTIONS = ["auto", "silent", "low", "medium", "high"]
SWING_OPTIONS = ["stop", "horizontal", "vertical", "both"]
TEMP_MIN, TEMP_MAX = 16, 32

MODES_FR = dict(MODE_OPTIONS)


def swing_state_to_command(value):
    """Le state renvoie stop/swing, la commande attend une direction (v1)."""
    if value in SWING_OPTIONS:
        return value
    if value == "swing":
        return "both"
    return "stop"


def slug(name):
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    s = "".join(c if c.isalnum() else "_" for c in s.lower())
    while "__" in s:
        s = s.replace("__", "_")
    return s.strip("_")


# ----------------------------------------------------------------------
# Paramètres
# ----------------------------------------------------------------------

def credentials():
    return (
        get_setting("username", module=MODULE, default=""),
        get_setting("password", module=MODULE, default=""),
    )


def configured():
    user, pwd = credentials()
    return bool(user and pwd)


def _require_credentials():
    user, pwd = credentials()
    if not user or not pwd:
        raise RuntimeError(
            "Identifiants Hi-Kumo manquants : renseigner l'email et le mot "
            "de passe dans le paramétrage de l'onglet Climatisation."
        )
    return user, pwd


def _make_client(user, pwd):
    from pyoverkiz.client import OverkizClient

    try:
        # API récente : credentials + Server enum
        from pyoverkiz.auth.credentials import UsernamePasswordCredentials
        from pyoverkiz.enums import Server

        return OverkizClient(
            server=Server.HI_KUMO_EUROPE,
            credentials=UsernamePasswordCredentials(user, pwd),
        )
    except (ModuleNotFoundError, ImportError, TypeError, AttributeError):
        # API pyoverkiz <= 1.20
        from pyoverkiz.const import SUPPORTED_SERVERS

        return OverkizClient(user, pwd, server=SUPPORTED_SERVERS["hi_kumo_europe"])


# ----------------------------------------------------------------------
# Lecture : découverte + états des unités
# ----------------------------------------------------------------------

def _extract_status(device):
    states = device.states
    status = {
        "label": device.label,
        "room": slug(device.label),
        "device_url": device.device_url,
    }
    for key, overkiz_name in STATE_MAP.items():
        state = states.get(overkiz_name)
        status[key] = state.value if state is not None else None
    status["on"] = status.get("power") == "on"
    return status


async def _fetch_units(user, pwd):
    """Découvre les climatisations (devices avec MainOperationState)."""
    client = _make_client(user, pwd)
    async with client:
        await client.login()
        devices = await client.get_devices()
        units = []
        for device in devices:
            if device.states and device.states.get("hlrrwifi:MainOperationState"):
                units.append(_extract_status(device))
        return units


def get_units():
    user, pwd = _require_credentials()
    return asyncio.run(_fetch_units(user, pwd))


# ----------------------------------------------------------------------
# Écriture : globalControl (6 paramètres, fusion avec l'état courant, v1)
# ----------------------------------------------------------------------

async def _set_clim(user, pwd, device_url, power=None, temperature=None,
                    fan_mode=None, mode=None, swing=None, leave_home=None):
    from pyoverkiz.models import Action, Command

    client = _make_client(user, pwd)
    async with client:
        await client.login()
        devices = await client.get_devices()
        current = None
        for device in devices:
            if device.device_url == device_url:
                current = device.states
                break
        if current is None:
            raise ValueError(f"Climatisation {device_url} introuvable")

        swing_param = swing if swing is not None else swing_state_to_command(
            current["hlrrwifi:SwingState"].value
        )
        params = [
            power if power is not None else current["hlrrwifi:MainOperationState"].value,
            temperature if temperature is not None else current["core:TargetTemperatureState"].value,
            fan_mode if fan_mode is not None else current["hlrrwifi:FanSpeedState"].value,
            mode if mode is not None else current["hlrrwifi:ModeChangeState"].value,
            swing_param,
            leave_home if leave_home is not None else current["hlrrwifi:LeaveHomeState"].value,
        ]
        command = Command(name="globalControl", parameters=params)
        action = Action(device_url=device_url, commands=[command])
        return await client.execute_action_group(actions=[action], label="globalControl")


def set_unit(room, **kwargs):
    """Pilote une unité par son slug. Seuls les paramètres précisés changent."""
    if kwargs.get("temperature") is not None:
        kwargs["temperature"] = max(TEMP_MIN, min(TEMP_MAX, int(kwargs["temperature"])))

    units, _ts, _err = get_units_cached()
    unit = next((u for u in units or [] if u["room"] == room), None)
    if unit is None:
        raise RuntimeError(f"Climatisation « {room} » introuvable")

    user, pwd = _require_credentials()
    result = asyncio.run(_set_clim(user, pwd, unit["device_url"], **kwargs))
    detail = ", ".join(f"{k}={v}" for k, v in kwargs.items() if v is not None)
    journal(f"Clim « {unit['label']} » : {detail}", module=MODULE)
    time_mod.sleep(2)
    try:
        get_units_cached(force=True)
    except Exception:
        pass
    return result


# ----------------------------------------------------------------------
# Cache en base
# ----------------------------------------------------------------------

def get_units_cached(force=False, ttl_minutes=10):
    now = datetime.now()
    raw = get_setting("cache_units", module=MODULE)
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
        data = get_units()
    except Exception as exc:
        journal(f"Erreur Hi-Kumo : {exc}", module=MODULE, level=LogEntry.ERROR)
        if cached_data is not None:
            return cached_data, cached_ts, f"API indisponible ({exc}) — dernière valeur connue."
        return None, None, str(exc)

    set_setting("cache_units", json.dumps({"ts": now.isoformat(), "data": data}), module=MODULE)
    return data, now, ""


def tache_actualiser():
    if not configured():
        return
    get_units_cached(force=True)
