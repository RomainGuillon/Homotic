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

import ast
import asyncio
import json
import re
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


# --- Dates Overkiz ----------------------------------------------------
#
# La passerelle échange les dates sous forme de dictionnaire
# ``{'year': 2026, 'month': 7, 'day': 19, 'hour': 12, 'minute': 0,
# 'second': 0, 'weekday': 6}``. Les états lus arrivent en texte (repr
# Python, guillemets simples) : d'où ``ast.literal_eval`` plutôt que JSON.
#
# ``weekday`` suit la convention de Python (lundi = 0) : vérifié sur les
# valeurs relevées en production (3 juillet 2026, vendredi → 4).

def _parse_date_state(value):
    """Un état de date Overkiz → ``datetime``, ou None si non réglé.

    Une date jamais réglée revient avec ``year: 1970`` et des champs
    « ?? » : c'est un « aucune date », pas une date de 1970.
    """
    if value in (None, "", "None"):
        return None
    data = value
    if isinstance(data, str):
        try:
            data = ast.literal_eval(data)
        except (ValueError, SyntaxError):
            return None
    if not isinstance(data, dict):
        return None
    try:
        annee, mois, jour = int(data["year"]), int(data["month"]), int(data["day"])
        heure, minute = int(data.get("hour", 0)), int(data.get("minute", 0))
    except (KeyError, TypeError, ValueError):
        return None
    if annee < 2000:
        return None
    try:
        return datetime(annee, mois, jour, heure, minute)
    except ValueError:
        return None


def _date_overkiz(moment):
    """``datetime`` → dictionnaire attendu par les commandes Overkiz."""
    return {
        "year": moment.year,
        "month": moment.month,
        "day": moment.day,
        "hour": moment.hour,
        "minute": moment.minute,
        "second": 0,
        "weekday": moment.weekday(),
    }


_FORMATS_DATE = (
    "%Y-%m-%dT%H:%M",      # champ « datetime-local » du navigateur
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%d/%m/%y %H:%M",
    "%d/%m/%Y",
    "%Y-%m-%d",
)

# « +3j », « +3j 18:00 », « +12h », « +90m »
_RELATIF = re.compile(
    r"^\+\s*(\d+(?:[.,]\d+)?)\s*([jhm])\s*(?:(\d{1,2})[h:](\d{2}))?$", re.IGNORECASE
)


def parse_moment(texte, reference=None):
    """Interprète une date écrite à la main, dans l'onglet ou un scénario.

    Trois écritures acceptées, pour que la même fonction serve au
    formulaire (date absolue) et aux scénarios récurrents (date relative,
    seule à garder un sens d'une exécution à l'autre) :

    - absolue : « 20/09/2026 18:00 », « 2026-09-20T18:00 » ;
    - relative : « +3j », « +3j 18:00 » (dans 3 jours à 18 h), « +12h » ;
    - « maintenant ».

    Retourne un ``datetime``, ou None si le texte est vide.
    """
    ref = (reference or datetime.now()).replace(second=0, microsecond=0)
    brut = str(texte or "").strip()
    if not brut:
        return None
    if brut.lower() in ("maintenant", "now", "immediat", "immédiat"):
        return ref

    trouve = _RELATIF.match(brut)
    if trouve:
        nombre = float(trouve.group(1).replace(",", "."))
        unite = trouve.group(2).lower()
        moment = ref + {
            "j": timedelta(days=nombre),
            "h": timedelta(hours=nombre),
            "m": timedelta(minutes=nombre),
        }[unite]
        if trouve.group(3):  # heure imposée : « +3j 18:00 »
            moment = moment.replace(hour=int(trouve.group(3)), minute=int(trouve.group(4)))
        return moment

    for fmt in _FORMATS_DATE:
        try:
            return datetime.strptime(brut, fmt)
        except ValueError:
            continue
    raise ValueError(
        f"date « {brut} » incomprise — attendu « JJ/MM/AAAA HH:MM », "
        "« maintenant » ou « +3j 18:00 »"
    )


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
    abs_debut = _parse_date_state(_find(raw, "absencestartdate"))
    abs_fin = _parse_date_state(_find(raw, "absenceenddate"))
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
        # Absence : le mode brut de la passerelle, et les deux dates au
        # format ISO (le résumé part en cache JSON, donc pas d'objet
        # datetime ici).
        "absence": _find(raw, "absencemode"),
        "absence_debut": abs_debut.isoformat() if abs_debut else None,
        "absence_fin": abs_fin.isoformat() if abs_fin else None,
    }


def is_heating(value):
    return str(value or "").lower() in ("on", "heating", "true", "1")


def is_absence(data):
    """Vrai si le ballon est effectivement en absence *maintenant*.

    Le mode seul ne suffit pas : les dates restent inscrites dans la
    passerelle après le retour, et le mode « prog » désigne une absence
    programmée, pas forcément commencée.
    """
    data = data or {}
    if str(data.get("absence") or "").lower() in ("", "off", "none"):
        return False
    debut = parse_iso(data.get("absence_debut"))
    fin = parse_iso(data.get("absence_fin"))
    maintenant = datetime.now()
    if debut and maintenant < debut:
        return False
    if fin and maintenant >= fin:
        return False
    return True


def parse_iso(texte):
    """Texte ISO du résumé → ``datetime``, ou None."""
    if not texte:
        return None
    try:
        return datetime.fromisoformat(texte)
    except (TypeError, ValueError):
        return None


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


# ----------------------------------------------------------------------
# Mode absence
# ----------------------------------------------------------------------
#
# Le ballon connaît un mode « absence » borné par deux dates (départ et
# retour), exactement comme dans l'application Cozytouch : il cesse de
# chauffer pendant la période et reprend juste avant le retour.
#
# Trois commandes, envoyées dans un seul groupe d'actions :
# ``setAbsenceStartDate``, ``setAbsenceEndDate``, ``setAbsenceMode``.
# Leurs noms varient selon les modèles (certains n'ont que les variantes
# « ...DateTime »), et les valeurs acceptées par le mode aussi. Plutôt que
# de les deviner, on les lit UNE FOIS dans la définition de l'appareil et
# on les mémorise : voir ``capacites()``.

MODE_ABSENCE_DEFAUT = "prog"


async def _decouvrir_capacites(user, pwd):
    """Commandes et valeurs de mode déclarées par le ballon (un get_setup)."""
    client = _make_client(user, pwd)
    async with client:
        await client.login()
        setup = await client.get_setup()
        water = _get_water_heater(setup)
        if water is None:
            raise RuntimeError("Chauffe-eau introuvable sur le compte Cozytouch")
        definition = water.definition
        commandes = sorted(
            {c.command_name for c in definition.commands if getattr(c, "command_name", None)}
        )
        modes = []
        for etat in getattr(definition, "states", None) or []:
            if "absencemode" in str(getattr(etat, "qualified_name", "")).lower():
                modes = [str(v) for v in (getattr(etat, "values", None) or [])]
                break
        return {
            "url": water.device_url,
            "label": water.label,
            "commandes": commandes,
            "modes_absence": modes,
        }


def capacites(force=False):
    """Ce que le ballon sait faire : ``{"commandes": [...], "modes_absence": [...]}``.

    Relevé une seule fois puis mémorisé en réglage : la définition d'un
    appareil ne change pas, et ``get_setup()`` rapatrie l'installation
    entière (voir l'entête sur l'économie d'appels). ``force=True`` pour
    la relire depuis le bouton du paramétrage.
    """
    brut = get_setting("capacites", module=MODULE, default="")
    if brut and not force:
        try:
            return json.loads(brut)
        except ValueError:
            pass

    user, pwd = _require_credentials()
    trouve = asyncio.run(_decouvrir_capacites(user, pwd))
    _memoriser_appareil(trouve.get("url"), trouve.get("label"))
    caps = {
        "commandes": trouve["commandes"],
        "modes_absence": trouve["modes_absence"],
    }
    set_setting("capacites", json.dumps(caps), module=MODULE)
    journal(
        f"Capacités du ballon relevées : {len(caps['commandes'])} commandes, "
        f"modes d'absence {caps['modes_absence'] or 'non déclarés'}",
        module=MODULE,
    )
    return caps


def mode_absence_actif():
    """Valeur de ``setAbsenceMode`` qui déclenche l'absence.

    Réglable (« mode_absence » dans le paramétrage) car tous les modèles
    ne nomment pas cet état pareil : « prog » chez le LINEO, « on »
    ailleurs. À défaut de réglage, on suit ce que l'appareil déclare.
    """
    choisi = (get_setting("mode_absence", module=MODULE, default="") or "").strip()
    if choisi:
        return choisi
    try:
        modes = [m.lower() for m in capacites().get("modes_absence") or []]
    except Exception:
        modes = []
    if MODE_ABSENCE_DEFAUT in modes:
        return MODE_ABSENCE_DEFAUT
    if "on" in modes:
        return "on"
    return MODE_ABSENCE_DEFAUT


def set_absence(depart="maintenant", retour=""):
    """Programme une absence entre deux dates.

    ``depart`` et ``retour`` acceptent les écritures de ``parse_moment``
    (« 20/09/2026 18:00 », « maintenant », « +7j 18:00 »). Retourne un
    texte décrivant la période programmée.
    """
    debut = parse_moment(depart) or datetime.now().replace(second=0, microsecond=0)
    fin = parse_moment(retour)
    if fin is None:
        raise ValueError("date de retour manquante : l'absence a besoin d'une fin")
    if fin <= debut:
        raise ValueError(
            f"le retour ({fin:%d/%m %H:%M}) doit être après le départ ({debut:%d/%m %H:%M})"
        )

    try:
        commandes = capacites().get("commandes") or []
    except Exception:
        commandes = []  # capacités illisibles : on tente les noms usuels

    if not commandes or "setAbsenceStartDate" in commandes:
        dates = [
            ("setAbsenceStartDate", [_date_overkiz(debut)]),
            ("setAbsenceEndDate", [_date_overkiz(fin)]),
        ]
    elif "setAbsenceStartDateTime" in commandes:
        dates = [
            ("setAbsenceStartDateTime", [debut.strftime("%Y-%m-%dT%H:%M:%S")]),
            ("setAbsenceEndDateTime", [fin.strftime("%Y-%m-%dT%H:%M:%S")]),
        ]
    else:
        raise RuntimeError(
            "Ce ballon ne déclare aucune commande d'absence "
            f"(commandes connues : {', '.join(commandes) or 'aucune'})"
        )

    mode = mode_absence_actif()
    _lancer(dates + [("setAbsenceMode", [mode])], "absence")
    journal(
        f"Absence programmée du {debut:%d/%m/%Y %H:%M} au {fin:%d/%m/%Y %H:%M} "
        f"(mode « {mode} »)",
        module=MODULE,
    )
    _refresh_after_command()
    _verifier_absence(mode)
    return f"absence du {debut:%d/%m %H:%M} au {fin:%d/%m %H:%M}"


def _verifier_absence(mode_envoye):
    """Relit l'état après coup : le mode a-t-il vraiment été pris ?

    Le groupe d'actions est accepté par le serveur avant d'être appliqué
    par la passerelle : une valeur de mode refusée par le ballon ne
    remonte donc aucune erreur, l'état reste simplement à « off ». Sans
    ce contrôle, la panne serait muette.
    """
    data, _ts, _err = get_status_cached()
    etat = str((data or {}).get("absence") or "").lower()
    if etat in ("", "off", "none"):
        journal(
            f"Absence : le ballon est resté sur « {etat or 'inconnu'} » après un "
            f"« setAbsenceMode {mode_envoye} ». Essayer l'autre valeur dans le "
            "paramétrage du module (réglage « mode_absence »).",
            module=MODULE,
            level=LogEntry.WARNING,
        )


def arreter_absence():
    """Annule l'absence en cours ou programmée."""
    try:
        commandes = capacites().get("commandes") or []
    except Exception:
        commandes = []
    if "cancelAbsence" in commandes:
        _lancer([("cancelAbsence", [])], "absence off")
    else:
        _lancer([("setAbsenceMode", ["off"])], "absence off")
    journal("Absence annulée", module=MODULE)
    _refresh_after_command()
    return "absence annulée"


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
