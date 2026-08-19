# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

"""Lecture et pilotage du chauffe-eau Atlantic Cozytouch (via pyoverkiz).

Repris de la v1 (Chauffe_eau/heater.py + functions.py), adapté :
identifiants et réglages lus en base (module « chauffe_eau »), cache du
statut en base avec repli sur la dernière valeur connue.

Économie d'appels — trois principes, dans l'ordre où ils agissent :

1. le cache en base (voir ``get_status_cached``) : toutes les lectures du
   module, y compris les conditions de scénario, passent par lui. Leur
   fréquence n'a donc aucun effet sur le nombre de requêtes envoyées ;
2. l'URL de l'appareil est mémorisée : ``get_setup()``, qui renvoie
   l'installation entière, n'est appelé qu'à la première connexion ou si
   l'URL retenue ne répond plus ;
3. la tâche « actualiser » ne force un relevé que si le suivi est arrêté,
   pour ne pas relire ce que celui-ci vient de lire.

Reste un défaut connu : chaque relevé ouvre une session et se
réauthentifie. Overkiz tolère mal les connexions répétées — c'est le
prochain chantier (session persistante), pas encore traité ici.
"""

import asyncio
import json
import time as time_mod
from datetime import datetime, timedelta

from core.models import LogEntry
from core.services import get_setting, journal, set_setting

MODULE = "chauffe_eau"

_MODE_FR = {
    "manualEcoActive": "Éco (manuel)",
    "manualEcoInactive": "Manuel",
    "autoMode": "Auto",
    "auto": "Auto",
    "boost": "Boost",
}


# ----------------------------------------------------------------------
# Paramètres (base de configuration)
# ----------------------------------------------------------------------

def credentials():
    return (
        get_setting("username", module=MODULE, default=""),
        get_setting("password", module=MODULE, default=""),
    )


def configured():
    user, pwd = credentials()
    return bool(user and pwd)


def v40_max():
    """Volume d'eau à 40°C d'un ballon « plein » (litres), pour le %."""
    raw = get_setting("v40_max", module=MODULE)
    try:
        return float(str(raw).replace(",", "."))
    except (TypeError, ValueError):
        return 260.0


# ----------------------------------------------------------------------
# Interprétation des états bruts (repris de la v1)
# ----------------------------------------------------------------------

def _find(raw, *subs):
    """Première valeur dont le nom d'état contient tous les fragments."""
    for name, value in raw.items():
        low = name.lower()
        if all(s in low for s in subs):
            return value
    return None


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _hot_water_pct(raw):
    """% d'eau chaude = douches restantes / douches max (repli : V40)."""
    remaining = _num(_find(raw, "numberofshowerremaining") or _find(raw, "shower", "remaining"))
    max_sh = _num(_find(raw, "maximalshower")) or 5.0
    if remaining is not None and max_sh:
        return max(0.0, min(100.0, remaining / max_sh * 100.0))
    v40 = _num(_find(raw, "v40"))
    if v40 is not None:
        return max(0.0, min(100.0, (v40 / 100.0) / v40_max() * 100.0))
    return None


def _summarize(raw):
    v40 = _num(_find(raw, "v40"))
    mode = _find(raw, "dhwmode") or _find(raw, "operatingmode")
    return {
        "hot_water_pct": _hot_water_pct(raw),
        "temperature": _find(raw, "middlewatertemperature") or _find(raw, "watertemperature"),
        "bottom_temperature": _find(raw, "bottomtankwatertemperature"),
        "target_temperature": _find(raw, "targetdhwtemperature") or _find(raw, "watertargettemperature") or _find(raw, "targettemperature"),
        "capacity": _find(raw, "dhwcapacity"),
        "hot_water_liters": _find(raw, "remaininghotwater"),
        "v40_liters": round(v40 / 100.0) if v40 is not None else None,
        "showers_expected": _find(raw, "expectednumberofshower"),
        "showers_remaining": _find(raw, "numberofshowerremaining"),
        "max_showers": _num(_find(raw, "maximalshower")) or 5,
        "min_showers": _num(_find(raw, "minimalshower")) or 1,
        "boost": _find(raw, "boostmode"),
        "mode": _MODE_FR.get(mode, mode),
        "heating": _find(raw, "heatingstatus"),
    }


def is_heating(value):
    return str(value or "").lower() in ("on", "heating", "true", "1")


# ----------------------------------------------------------------------
# Client Overkiz (repris de la v1)
# ----------------------------------------------------------------------

def _require_credentials():
    """Lit les identifiants en base (à appeler AVANT le code async : l'ORM
    Django est interdit dans une boucle asyncio)."""
    user, pwd = credentials()
    if not user or not pwd:
        raise RuntimeError(
            "Identifiants Cozytouch manquants : renseigner l'email et le mot "
            "de passe dans le paramétrage de l'onglet Chauffe-eau."
        )
    return user, pwd


def _make_client(user, pwd):
    from pyoverkiz.client import OverkizClient
    from pyoverkiz.enums import Server

    try:
        # API récente (celle de la v1) : credentials + Server enum
        from pyoverkiz.auth.credentials import UsernamePasswordCredentials

        return OverkizClient(
            server=Server.ATLANTIC_COZYTOUCH,
            credentials=UsernamePasswordCredentials(user, pwd),
        )
    except (ModuleNotFoundError, ImportError, TypeError):
        # API pyoverkiz <= 1.20 : (username, password, server=OverkizServer)
        from pyoverkiz.const import SUPPORTED_SERVERS

        return OverkizClient(user, pwd, server=SUPPORTED_SERVERS[Server.ATLANTIC_COZYTOUCH])


def _get_water_heater(setup):
    """Retourne le device chauffe-eau Overkiz."""
    for d in setup.devices:
        if not d.widget:
            continue
        widget = d.widget.lower()
        if "water" in widget or "dhw" in widget:
            return d
    return None


# ----------------------------------------------------------------------
# Appareil mémorisé
# ----------------------------------------------------------------------
#
# L'URL Overkiz d'un équipement ne change pas — sauf ré-appairage du ballon.
# La retrouver coûtait pourtant un « get_setup() » à chaque relevé, soit
# l'installation entière rapatriée pour en extraire une chaîne connue
# d'avance, environ 300 fois par jour. On la retient donc en base, et on ne
# redécouvre que si elle manque ou si elle ne répond plus.

def _appareil_memorise():
    """(url, libellé) retenus lors d'une découverte précédente.

    À appeler AVANT le code async : l'ORM Django est interdit dans une
    boucle asyncio (même raison que ``_require_credentials``).
    """
    return (
        get_setting("device_url", module=MODULE, default="") or None,
        get_setting("device_label", module=MODULE, default="") or None,
    )


def _memoriser_appareil(url, libelle):
    """Enregistre l'appareil découvert, si la valeur a changé."""
    if url and url != get_setting("device_url", module=MODULE, default=""):
        set_setting("device_url", url, module=MODULE)
    if libelle and libelle != get_setting("device_label", module=MODULE, default=""):
        set_setting("device_label", libelle, module=MODULE)


async def _decouvrir(client):
    """Cherche le ballon dans l'installation. Retourne (url, libellé)."""
    setup = await client.get_setup()
    water = _get_water_heater(setup)
    if water is None:
        raise RuntimeError("Chauffe-eau introuvable sur le compte Cozytouch")
    return water.device_url, water.label


async def _fetch_status(user, pwd, device_url=None):
    client = _make_client(user, pwd)
    async with client:
        await client.login()
        libelle, states = None, None
        if device_url:
            try:
                states = await client.get_state(device_url)
            except Exception:
                # URL périmée, ou lecture ratée : on retombe sur la
                # découverte complète plutôt que d'échouer. Le module se
                # répare donc tout seul si le ballon est ré-appairé.
                states = None
        if not states:
            device_url, libelle = await _decouvrir(client)
            states = await client.get_state(device_url)
        raw = {s.name: str(s.value) for s in states}
        return {"label": libelle, "device_url": device_url, "raw": raw}


def get_status():
    user, pwd = _require_credentials()
    url, libelle = _appareil_memorise()  # avant l'async : lecture en base
    result = asyncio.run(_fetch_status(user, pwd, url))
    if not result.get("label"):
        result["label"] = libelle
    _memoriser_appareil(result.get("device_url"), result.get("label"))
    result.update(_summarize(result["raw"]))  # hors async : lit v40_max en base
    return result


async def _execute(user, pwd, commands, label, device_url=None):
    from pyoverkiz.models import Action, Command

    client = _make_client(user, pwd)
    async with client:
        await client.login()
        decouvert = None
        if not device_url:
            device_url, decouvert = await _decouvrir(client)
        action = Action(
            device_url=device_url,
            commands=[Command(name=n, parameters=p) for n, p in commands],
        )
        await client.execute_action_group(actions=[action], label=label)
        return device_url, decouvert


def _lancer(commands, label):
    """Envoie des commandes au ballon, en réutilisant l'URL mémorisée."""
    user, pwd = _require_credentials()
    url, libelle = _appareil_memorise()
    try:
        url, decouvert = asyncio.run(_execute(user, pwd, commands, label, url))
    except Exception:
        if not url:
            raise
        # L'URL mémorisée ne répond plus : une seule nouvelle tentative, en
        # redécouvrant l'appareil. Les commandes envoyées ici règlent une
        # consigne (nombre de douches, mode boost) : les rejouer donne le
        # même résultat, un doublon éventuel est donc sans conséquence.
        url, decouvert = asyncio.run(_execute(user, pwd, commands, label, None))
    _memoriser_appareil(url, decouvert or libelle)


def douches_chauffe():
    """Nombre de douches demandé par l'action de scénario « chauffer »
    (chauffe max), réglable dans le paramétrage. 5 par défaut — la borne
    haute la plus courante des ballons Cozytouch."""
    try:
        return max(1, min(5, int(get_setting("douches_chauffe", module=MODULE, default=5))))
    except (TypeError, ValueError):
        return 5


def douches_veille():
    """Nombre de douches demandé par l'action de scénario « eteindre »
    (chauffe mini), réglable dans le paramétrage. 1 par défaut."""
    try:
        return max(1, min(5, int(get_setting("douches_veille", module=MODULE, default=1))))
    except (TypeError, ValueError):
        return 1


def set_showers(n):
    """Règle le nombre de douches souhaité (1..5)."""
    n = max(1, min(int(n), 5))
    _lancer([("setExpectedNumberOfShower", [n])], "Set shower count")
    journal(f"Nombre de douches souhaité : {n}", module=MODULE)
    _refresh_after_command()
    return n


def set_boost_mode(mode):
    """Active/désactive le boost : "on", "off" ou "prog"."""
    if mode not in ("on", "off", "prog"):
        raise ValueError("mode doit être 'on', 'off' ou 'prog'")
    _lancer([("setBoostMode", [mode])], f"boost={mode}")
    journal(f"Boost : {mode}", module=MODULE)
    _refresh_after_command()
    return mode


def _refresh_after_command():
    """Laisse la passerelle appliquer la commande puis rafraîchit le cache."""
    time_mod.sleep(2)
    try:
        get_status_cached(force=True)
    except Exception:
        pass


# ----------------------------------------------------------------------
# Cache en base (même principe que le module tempo)
# ----------------------------------------------------------------------

def get_status_cached(force=False, ttl_minutes=15):
    """Statut du ballon : (data, ts, erreur). Sert le cache périmé si l'API tombe."""
    now = datetime.now()
    raw = get_setting("cache_status", module=MODULE)
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
        data = get_status()
    except Exception as exc:
        journal(f"Erreur Cozytouch : {exc}", module=MODULE, level=LogEntry.ERROR)
        if cached_data is not None:
            return cached_data, cached_ts, f"API indisponible ({exc}) — dernière valeur connue."
        return None, None, str(exc)

    set_setting("cache_status", json.dumps({"ts": now.isoformat(), "data": data}), module=MODULE)
    return data, now, ""


def tache_actualiser():
    """Tâche périodique (scheduler) : rafraîchit le statut du ballon.

    Le forçage n'a lieu que si le suivi des chauffes est arrêté. Quand il
    tourne, il a relu le ballon moins de « suivi_minutes_veille » minutes
    plus tôt : forcer ici relirait ce qui vient de l'être, au prix d'une
    authentification Overkiz complète, une centaine de fois par jour.

    Sans forçage, l'appel reste un filet : il rafraîchit quand même si le
    cache a dépassé son délai normal. Un suivi en panne ne fige donc pas
    les mesures.
    """
    if not configured():
        return
    try:
        from .suivi import actif as suivi_actif

        suivi_tourne = suivi_actif()
    except Exception:
        suivi_tourne = False
    get_status_cached(force=not suivi_tourne)
