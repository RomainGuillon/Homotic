"""API Cloud Tuya : capteurs température/humidité et prises connectées.

Repris de la v1 (tuya/tuya_client_v2.py + sensors.py), adapté :
identifiants en base (module « tuya »), caches en base avec repli sur la
dernière valeur connue.

Budget d'appels
---------------
L'édition Trial d'IoT Core est plafonnée à 26 000 appels API par mois, soit
~867/jour. Ce module est donc écrit pour consommer le moins d'appels
possible :

- le jeton est mis en cache en mémoire (validité 2 h annoncée par Tuya) ;
- la liste des appareils est mise en cache en base (``DEVICES_TTL_MIN``) :
  elle ne change que quand un appareil est ajouté ou renommé ;
- les états sont lus par lot via ``/v1.0/iot-03/devices/status`` :
  1 appel pour 20 appareils, au lieu d'un appel par appareil ;
- les programmations (timers) ne sont rechargées qu'une fois par jour :
  elles ne changent que si on les modifie dans l'app Tuya ;
- capteurs et prises sont collectés en une seule passe, pas deux.

Coût observé avec 11 appareils et un cycle de 10 min : ~180 appels/jour
(144 statuts + 24 listes + 12 jetons), soit ~21 % du quota mensuel.
"""

import hashlib
import hmac
import json
import time as time_mod
from datetime import datetime, timedelta

import requests

from core.models import LogEntry
from core.services import get_setting, journal, set_setting

from . import local

MODULE = "tuya"

# Appareils à masquer, par mot-clé dans le nom (device virtuel, passerelle).
EXCLUDE_KEYWORDS = ("vdevo", "gateway")

# Nombre maximal d'identifiants acceptés par l'endpoint de statut par lot.
BATCH_MAX = 20

# Durées de vie des caches, en minutes.
ETATS_TTL_MIN = 10      # températures, humidités, état des prises
DEVICES_TTL_MIN = 60    # liste des appareils (nom, présence en ligne)
TIMERS_TTL_MIN = 1440   # programmations des prises

# Marge de sécurité sur l'expiration du jeton, en secondes.
TOKEN_MARGE_S = 300

# Deux appels consécutifs à get_sensors_cached(force=True) puis
# get_plugs_cached(force=True) ne doivent pas déclencher deux collectes :
# on ignore un « force » qui suit de très près une collecte réussie.
ANTI_DOUBLON_S = 5

# Jeton partagé par le processus (l'app tourne en --noreload, un seul worker).
_token_cache = {}          # client_id -> {"valeur": str, "expire": float}
_derniere_collecte = 0.0   # time.time() de la dernière collecte réussie


# ----------------------------------------------------------------------
# Paramètres
# ----------------------------------------------------------------------

def credentials():
    return (
        get_setting("access_id", module=MODULE, default=""),
        get_setting("access_secret", module=MODULE, default=""),
    )


def configured_cloud():
    cid, secret = credentials()
    return bool(cid and secret)


def mode():
    """« local » (réseau) ou « cloud » (API Tuya). Le cloud reste le défaut :
    une installation existante ne doit pas changer de comportement toute
    seule au premier déploiement."""
    valeur = str(get_setting("mode", module=MODULE, default="cloud")).lower()
    return "local" if valeur == "local" else "cloud"


def repli_cloud_actif():
    """En mode local, autorise-t-on le cloud quand le réseau ne répond pas ?

    Activé par défaut : mieux vaut une donnée obtenue par un chemin plus
    coûteux que pas de donnée du tout. À désactiver si l'on veut la
    garantie qu'aucun appel cloud ne part (quota épuisé, par exemple).
    """
    return str(get_setting("repli_cloud", module=MODULE, default="1")) not in ("0", "false", "False", "")


def configured():
    """Le module a-t-il de quoi fonctionner, dans le mode choisi ?"""
    if mode() == "local":
        return local.configured() or (repli_cloud_actif() and configured_cloud())
    return configured_cloud()


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

    def _get(self, path, query=""):
        """GET signé. La query est signée telle qu'elle est envoyée : on
        construit l'URL à la main pour que requests ne la ré-encode pas
        (sinon erreur de signature 1004)."""
        headers = self._headers("GET", path, query)
        url = self.base_url + path + ("?" + query if query else "")
        return requests.get(url, headers=headers, timeout=15).json()

    def get_token(self):
        """1 appel. Coûteux à répéter : passer par _make_client()."""
        path = "/v1.0/token?grant_type=1"
        r = requests.get(self.base_url + path, headers=self._headers("GET", path), timeout=15)
        payload = r.json()
        if not payload.get("success", False):
            raise RuntimeError(f"Authentification Tuya refusée : {payload.get('msg', payload)}")
        result = payload["result"]
        self.access_token = result["access_token"]
        return self.access_token, result.get("expire_time", 7200)

    def get_devices_v2(self):
        """1 appel : liste des appareils du compte (nom, en ligne)."""
        return self._get("/v2.0/cloud/thing/device", "page_size=20")

    def get_devices_status(self, device_ids):
        """1 appel pour 20 appareils au maximum : états courants par lot."""
        return self._get("/v1.0/iot-03/devices/status", "device_ids=" + ",".join(device_ids))

    def get_device_properties(self, device_id):
        """1 appel pour 1 appareil. Conservé pour le diagnostic ; la
        collecte périodique utilise get_devices_status()."""
        return self._get(f"/v2.0/cloud/thing/{device_id}/shadow/properties")

    def get_device_timers(self, device_id):
        return self._get(f"/v1.0/devices/{device_id}/timers")

    def send_commands(self, device_id, commands):
        path = f"/v1.0/devices/{device_id}/commands"
        body = json.dumps({"commands": commands}, separators=(",", ":"))
        headers = self._headers("POST", path, "", body)
        r = requests.post(self.base_url + path, headers=headers, data=body, timeout=15)
        return r.json()


def _make_client():
    """Client prêt à l'emploi, jeton réutilisé tant qu'il est valide."""
    cid, secret = credentials()
    if not cid or not secret:
        raise RuntimeError(
            "Identifiants Tuya manquants : renseigner l'Access ID et le "
            "secret dans le paramétrage de l'onglet Capteurs."
        )
    client = TuyaClientV2(cid, secret, base_url())

    entree = _token_cache.get(cid)
    if entree and entree["expire"] > time_mod.time():
        client.access_token = entree["valeur"]
        return client

    jeton, duree = client.get_token()
    _token_cache[cid] = {
        "valeur": jeton,
        "expire": time_mod.time() + max(60, duree - TOKEN_MARGE_S),
    }
    return client


def _oublier_token():
    """À appeler si l'API répond « token invalid » : force un nouveau jeton."""
    _token_cache.clear()


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


# Codes connus, testés en premier. Le repli par mot-clé sert aux appareils
# dont le modèle n'utilise pas ces codes.
_CODES_TEMP = ("va_temperature", "temp_current", "temperature", "temp_value")
_CODES_HUM = ("va_humidity", "humidity_value", "humidity", "hum_value")

# Un code qui contient l'un de ces fragments n'est pas une mesure : c'est un
# réglage (« temp_set »), une unité (« temp_unit_convert ») ou une correction.
_FRAGMENTS_NON_MESURE = ("unit", "set", "correct", "calib", "alarm", "max", "min")


def _numerique(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _mesure(props, codes_connus, mot_cle):
    for prop in props:
        if str(prop.get("code", "")).lower() in codes_connus and _numerique(prop.get("value")):
            return _scale(prop["value"])
    for prop in props:
        code = str(prop.get("code", "")).lower()
        if mot_cle not in code or not _numerique(prop.get("value")):
            continue
        if any(f in code for f in _FRAGMENTS_NON_MESURE):
            continue
        return _scale(prop["value"])
    return None


def _extract_measures(properties):
    return (
        _mesure(properties, _CODES_TEMP, "temp"),
        _mesure(properties, _CODES_HUM, "humid"),
    )


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
# Caches en base
# ----------------------------------------------------------------------

def _lire_cache(key):
    """(data, ts) ou (None, None) si absent ou illisible."""
    raw = get_setting(key, module=MODULE)
    if not raw:
        return None, None
    try:
        payload = json.loads(raw)
        return payload["data"], datetime.fromisoformat(payload["ts"])
    except (ValueError, KeyError, TypeError):
        return None, None


def _ecrire_cache(key, data, ts=None):
    ts = ts or datetime.now()
    set_setting(key, json.dumps({"ts": ts.isoformat(), "data": data}), module=MODULE)


def _perime(ts, ttl_minutes):
    return ts is None or datetime.now() - ts >= timedelta(minutes=ttl_minutes)


# ----------------------------------------------------------------------
# Collecte (une seule passe pour capteurs + prises)
# ----------------------------------------------------------------------

def _devices(client, force=False):
    """Liste des appareils retenus : [{id, name, online}]. 1 appel au plus
    toutes les DEVICES_TTL_MIN minutes."""
    data, ts = _lire_cache("cache_devices")
    if data is not None and not force and not _perime(ts, DEVICES_TTL_MIN):
        return data

    payload = client.get_devices_v2()
    if not payload.get("success", False):
        # Sans ce garde-fou, une réponse d'erreur (quota épuisé, jeton
        # refusé) serait lue comme « aucun appareil » : collecter()
        # écraserait les caches par des listes vides et l'onglet se
        # viderait en silence. On lève, pour que _assurer_fraicheur()
        # journalise et conserve la dernière valeur connue.
        raise RuntimeError(payload.get("msg") or str(payload))

    devices = []
    for dev in _device_list(payload):
        device_id = dev.get("id")
        name = dev.get("customName") or dev.get("custom_name") or dev.get("name") or device_id
        if not device_id or _is_excluded(name):
            continue
        devices.append({
            "id": device_id,
            "name": name,
            "online": dev.get("isOnline", dev.get("online", True)),
        })
    _ecrire_cache("cache_devices", devices)
    return devices


def _statuts(client, device_ids):
    """{device_id: [{code, value}, ...]} — 1 appel par lot de BATCH_MAX."""
    statuts = {}
    for debut in range(0, len(device_ids), BATCH_MAX):
        lot = device_ids[debut:debut + BATCH_MAX]
        payload = client.get_devices_status(lot)
        if not payload.get("success", False):
            raise RuntimeError(payload.get("msg") or str(payload))
        for entree in payload.get("result") or []:
            statuts[entree.get("id")] = entree.get("status") or []
    return statuts


def _timers(client, plug_ids, force=False):
    """{device_id: slots}. Rechargé une fois par jour, ou pour les prises
    apparues depuis le dernier chargement."""
    data, ts = _lire_cache("cache_timers")
    data = data if isinstance(data, dict) else {}

    if force or _perime(ts, TIMERS_TTL_MIN):
        a_charger = list(plug_ids)
    else:
        a_charger = [pid for pid in plug_ids if pid not in data]
        if not a_charger:
            return data

    for device_id in a_charger:
        try:
            data[device_id] = _parse_timers(client.get_device_timers(device_id))
        except Exception:
            data.setdefault(device_id, [])
    # On garde le ts d'origine si on n'a chargé que les manquants, pour ne
    # pas repousser indéfiniment le rechargement quotidien.
    _ecrire_cache("cache_timers", data, ts=None if (force or _perime(ts, TIMERS_TTL_MIN)) else ts)
    return data


def collecter(force_devices=False, force_timers=False):
    """Rafraîchit cache_sensors et cache_plugs, par le chemin choisi.

    En mode local, le cloud n'est sollicité qu'en dernier recours, et
    seulement si le repli est autorisé.
    """
    global _derniere_collecte

    if mode() == "local":
        try:
            return _collecter_local()
        except Exception as exc:
            if not (repli_cloud_actif() and configured_cloud()):
                raise
            journal(
                f"Lecture locale indisponible ({exc}) — repli sur l'API Cloud.",
                module=MODULE,
                level=LogEntry.WARNING,
            )
    return _collecter_cloud(force_devices=force_devices, force_timers=force_timers)


def _collecter_local():
    """Collecte par le réseau local. Aucun appel à Tuya, aucun quota consommé.

    Les programmations ne sont pas lisibles en local : on conserve celles
    déjà connues du cache plutôt que d'afficher « pas de programmation »
    à tort.
    """
    global _derniere_collecte

    sensors, plugs = local.collecter()

    programmations, _ts = _lire_cache("cache_timers")
    if isinstance(programmations, dict):
        for plug in plugs:
            plug["schedule_slots"] = programmations.get(plug["id"], [])

    maintenant = datetime.now()
    _ecrire_cache("cache_sensors", sensors, ts=maintenant)
    _ecrire_cache("cache_plugs", plugs, ts=maintenant)
    _derniere_collecte = time_mod.time()
    return sensors, plugs


def _collecter_cloud(force_devices=False, force_timers=False):
    """Collecte par l'API Cloud, en une seule passe.

    Coût : 1 appel de statut par lot de 20 appareils, plus la liste des
    appareils et les timers quand leurs caches ont expiré.
    """
    global _derniere_collecte

    client = _make_client()
    devices = _devices(client, force=force_devices)
    if not devices:
        _ecrire_cache("cache_sensors", [])
        _ecrire_cache("cache_plugs", [])
        _derniere_collecte = time_mod.time()
        return [], []

    statuts = _statuts(client, [d["id"] for d in devices])

    sensors, plugs, plug_ids = [], [], []
    for dev in devices:
        props = statuts.get(dev["id"])
        if props is None:
            continue

        temperature, humidity = _extract_measures(props)
        if temperature is not None or humidity is not None:
            sensors.append({
                "id": dev["id"],
                "name": dev["name"],
                "online": dev["online"],
                "temperature": temperature,
                "humidity": humidity,
            })

        switch_code, state = _first_switch(props)
        if switch_code is not None:
            plug_ids.append(dev["id"])
            plugs.append({
                "id": dev["id"],
                "name": dev["name"],
                "online": dev["online"],
                "switch_code": switch_code,
                "state": state,
                "schedule_slots": [],
            })

    programmations = _timers(client, plug_ids, force=force_timers) if plug_ids else {}
    for plug in plugs:
        plug["schedule_slots"] = programmations.get(plug["id"], [])

    maintenant = datetime.now()
    _ecrire_cache("cache_sensors", sensors, ts=maintenant)
    _ecrire_cache("cache_plugs", plugs, ts=maintenant)
    _derniere_collecte = time_mod.time()
    return sensors, plugs


def _assurer_fraicheur(force=False):
    """Collecte si nécessaire. Renvoie un message d'erreur, vide si tout va bien."""
    sensors, ts_s = _lire_cache("cache_sensors")
    plugs, ts_p = _lire_cache("cache_plugs")
    connu = sensors is not None or plugs is not None

    if force and time_mod.time() - _derniere_collecte < ANTI_DOUBLON_S:
        return ""  # collecte tout juste effectuée par l'appel précédent
    if not force and connu and not _perime(ts_s, ETATS_TTL_MIN) and not _perime(ts_p, ETATS_TTL_MIN):
        return ""

    try:
        collecter()
    except Exception as exc:
        journal(f"Erreur API Tuya : {exc}", module=MODULE, level=LogEntry.ERROR)
        if connu:
            return f"API indisponible ({exc}) — dernière valeur connue."
        return str(exc)
    return ""


def get_sensors_cached(force=False):
    """(capteurs, ts, erreur) — [{id, name, online, temperature, humidity}]."""
    erreur = _assurer_fraicheur(force)
    data, ts = _lire_cache("cache_sensors")
    return data, ts, erreur


def get_plugs_cached(force=False):
    """(prises, ts, erreur) — [{id, name, online, switch_code, state, schedule_slots}]."""
    erreur = _assurer_fraicheur(force)
    data, ts = _lire_cache("cache_plugs")
    return data, ts, erreur


def rafraichir(complet=False):
    """Rafraîchissement demandé par l'utilisateur (bouton de l'onglet).

    ``complet=True`` recharge aussi la liste des appareils et les
    programmations : à réserver au cas où un appareil vient d'être ajouté
    ou une programmation modifiée dans l'app Tuya.
    """
    try:
        collecter(force_devices=complet, force_timers=complet)
        return ""
    except Exception as exc:
        journal(f"Erreur API Tuya : {exc}", module=MODULE, level=LogEntry.ERROR)
        return str(exc)


def get_sensors():
    """Collecte immédiate des capteurs (sans passer par le cache)."""
    return collecter()[0]


def get_plugs():
    """Collecte immédiate des prises (sans passer par le cache)."""
    return collecter()[1]


# ----------------------------------------------------------------------
# Commandes
# ----------------------------------------------------------------------

def _appliquer_etat_cache(device_id, on):
    """Met l'état de la prise à jour dans le cache, sans rappeler l'API.

    Le ``ts`` du cache n'est volontairement pas rafraîchi : la prochaine
    collecte périodique confirmera l'état réel.
    """
    plugs, ts = _lire_cache("cache_plugs")
    if not plugs:
        return
    for plug in plugs:
        if plug.get("id") == device_id:
            plug["state"] = bool(on)
            break
    _ecrire_cache("cache_plugs", plugs, ts=ts)


def set_plug(device_id, switch_code, on, name=None):
    """Allume/éteint une prise, journalise et met le cache à jour.

    En mode local, la commande part sur le réseau : effet immédiat, pas
    d'aller-retour par les serveurs Tuya. Le repli cloud vaut ici aussi —
    une prise qui ne répond pas en LAN reste pilotable.
    """
    if mode() == "local":
        try:
            nom = local.set_plug(device_id, on)
            journal(f"Prise « {name or nom} » -> {'ON' if on else 'OFF'} (local)", module=MODULE)
            _appliquer_etat_cache(device_id, on)
            return {"success": True, "local": True}
        except Exception as exc:
            if not (repli_cloud_actif() and configured_cloud()):
                raise
            journal(
                f"Commande locale impossible ({exc}) — repli sur l'API Cloud.",
                module=MODULE,
                level=LogEntry.WARNING,
            )

    client = _make_client()
    payload = client.send_commands(device_id, [{"code": switch_code, "value": bool(on)}])
    if not payload.get("success", False):
        raise RuntimeError(payload.get("msg") or str(payload))
    journal(f"Prise « {name or device_id} » -> {'ON' if on else 'OFF'}", module=MODULE)
    _appliquer_etat_cache(device_id, on)
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
# Tâche périodique
# ----------------------------------------------------------------------

def tache_actualiser():
    """Tâche périodique (scheduler) : capteurs + prises, en une passe."""
    if not configured():
        return
    collecter()
