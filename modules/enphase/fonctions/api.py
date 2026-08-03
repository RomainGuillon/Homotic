"""Passerelle Enphase Envoy (locale) : production, consommation, flux réseau.

Repris de la v1 (enphase/energy.py), adapté : configuration, token JWT et
référence de début de journée stockés en base. La tâche périodique publie
aussi des variables globales pour les scénarios.
"""

import json
import threading
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
        # Timeouts (connexion, lecture) courts : l'Envoy est sur le réseau
        # local, s'il n'a pas répondu en 5 s il ne répondra pas. Un timeout
        # long ne rend service à personne — il fige la page qui attend.
        return requests.get(
            url, headers={"Authorization": f"Bearer {tok}"}, verify=False,
            timeout=(2, 5),
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


def _meters_index():
    """Carte ``{measurementType: {eid, state}}`` des pinces, gardée en base.

    Le brochage des pinces est une propriété du matériel : il ne change pas
    d'un relevé à l'autre. Le redemander à chaque mesure, c'était une requête
    passée à réapprendre ce qu'on savait déjà.
    """
    cached = get_setting("meters_index", module=MODULE)
    if cached:
        try:
            idx = json.loads(cached)
            if isinstance(idx, dict) and idx:
                return idx
        except (ValueError, TypeError):
            pass

    idx = {}
    for m in _get_json("/ivp/meters") or []:
        mtype, eid = m.get("measurementType"), m.get("eid")
        if mtype and eid is not None:
            idx[mtype] = {"eid": eid, "state": m.get("state", "")}
    set_setting("meters_index", json.dumps(idx), module=MODULE)
    return idx


def _read_via_meters():
    """Relevé complet via les pinces, en une seule requête. None si impossible.

    Source préférée à ``/production.json`` : cet endpoint fait interroger les
    micro-onduleurs un par un par l'Envoy, et une seule radio qui ne répond
    pas le fait pendre plusieurs dizaines de secondes. Les pinces, elles,
    répondent en quelques centaines de millisecondes — mêmes grandeurs,
    mesurées au tableau plutôt qu'en toiture.
    """
    def utilisable(pince):
        return bool(pince) and pince.get("state") in ("enabled", "")

    idx = _meters_index()
    prod, net = idx.get("production"), idx.get("net-consumption")
    if not (utilisable(prod) and utilisable(net)):
        return None  # une pince absente ou désactivée renverrait des zéros crédibles

    readings = {
        r["eid"]: r for r in (_get_json("/ivp/meters/readings") or [])
        if isinstance(r, dict) and r.get("eid") is not None
    }
    rp, rn = readings.get(prod["eid"]), readings.get(net["eid"])
    if not (rp and rn):
        return None

    def f(reading, cle):
        return float(reading.get(cle) or 0)

    imp_life, exp_life = f(rn, "actEnergyDlvd"), f(rn, "actEnergyRcvd")
    net_w, net_life = f(rn, "activePower"), imp_life - exp_life
    prod_w, prod_life = f(rp, "activePower"), f(rp, "actEnergyDlvd")

    # La pince de consommation totale est optionnelle : beaucoup
    # d'installations n'en ont que deux (production + soutirage réseau). La
    # consommation maison se déduit alors — ce qui est produit, plus ce qui
    # est pris au réseau, moins ce qui y repart. C'est le même calcul que
    # fait l'Envoy lui-même quand la troisième pince manque.
    conso = idx.get("total-consumption")
    rc = readings.get(conso["eid"]) if utilisable(conso) else None
    if rc:
        conso_w, conso_life = f(rc, "activePower"), f(rc, "actEnergyDlvd")
    else:
        conso_w, conso_life = prod_w + net_w, prod_life + net_life

    return {
        "source": "meters" if rc else "meters2",
        "prod_w": prod_w,
        "conso_w": conso_w,
        "net_w": net_w,
        "prod_life": prod_life,
        "conso_life": conso_life,
        "net_life": net_life,
        "imp_life": imp_life,
        "exp_life": exp_life,
    }


def _read_via_production_json():
    """Ancien chemin : production sur les micro-onduleurs. Lent mais universel.

    Conservé pour les installations sans pince de production, où les pinces
    ne suffisent pas.
    """
    data = _get_json("/production.json")
    production = data.get("production", [])
    prod = _find(production, "type", "inverters") or _find(production, "measurementType", "production")
    conso = _find(data.get("consumption", []), "measurementType", "total-consumption")
    net = _find(data.get("consumption", []), "measurementType", "net-consumption")
    releve = {
        "source": "production_json",
        "prod_w": float(prod.get("wNow", 0) or 0),
        "conso_w": float(conso.get("wNow", 0) or 0),
        "net_w": float(net.get("wNow", 0) or 0),
        "prod_life": float(prod.get("whLifetime", 0) or 0),
        "conso_life": float(conso.get("whLifetime", 0) or 0),
        "net_life": float(net.get("whLifetime", 0) or 0),
    }

    # Compteurs bruts réseau : import et export séparés, que production.json
    # ne distingue pas (il n'expose que leur solde).
    try:
        net_meter = _meters_index().get("net-consumption")
        if net_meter:
            for r in _get_json("/ivp/meters/readings") or []:
                if r.get("eid") == net_meter["eid"]:
                    imp, exp = r.get("actEnergyDlvd"), r.get("actEnergyRcvd")
                    if imp is not None and exp is not None:
                        releve["imp_life"] = float(imp)
                        releve["exp_life"] = float(exp)
                    break
    except Exception:
        pass  # les cumuls du jour se rabattront sur le solde net
    return releve


def _read_meters():
    """Relevé instantané + cumuls, par le chemin le plus rapide disponible."""
    releve = None
    try:
        releve = _read_via_meters()
    except Exception as exc:
        journal(f"Lecture par les pinces impossible ({exc}) — repli sur "
                f"production.json", module=MODULE, level=LogEntry.WARNING)
    return releve if releve is not None else _read_via_production_json()


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

    state = _load_state()
    current = {"prod": m["prod_life"], "conso": m["conso_life"], "net": m["net_life"]}
    if "imp_life" in m and "exp_life" in m:
        current["imp"] = m["imp_life"]
        current["exp"] = m["exp_life"]

    # Les compteurs cumulés des pinces et ceux des micro-onduleurs ne partent
    # pas du même zéro : comparer le relevé d'une source à une référence prise
    # sur l'autre donnerait une journée aberrante (souvent des mégawattheures).
    # Un changement de source repart donc sur une référence neuve, quitte à
    # perdre le début de la journée en cours.
    # Un état sans source vient d'une version antérieure, où tout passait par
    # production.json : il faut le traiter comme un changement, sinon les
    # cumuls du jour se calculeraient contre une référence de l'autre source.
    source = m.get("source", "")
    if state and state.get("source") != source:
        journal(
            f"Source des mesures : {state.get('source') or 'production_json'} "
            f"→ {source}. Cumuls du jour repartis de zéro.", module=MODULE,
        )
        state = None

    if not state or state.get("date") != today:
        # Nouveau jour : référence = dernier relevé de la veille, sinon l'actuel
        base = state.get("last", current) if state else current
        state = {"date": today, "base": base, "last": current}
    else:
        state["last"] = current
    state["source"] = source
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

# Coupe-circuit : après plusieurs échecs d'affilée, on arrête d'appeler
# l'Envoy pendant quelques minutes. Sans ça, une passerelle injoignable fait
# attendre chaque affichage du tableau de bord (3 requêtes × timeout) et
# remplit le journal de milliers de lignes identiques.
_CIRCUIT = {"echecs": 0, "rouvre_a": None, "dernier_log": None}
CIRCUIT_SEUIL = 3          # échecs consécutifs avant ouverture
CIRCUIT_PAUSE_MIN = 5      # minutes sans aucun appel une fois ouvert
CIRCUIT_RAPPEL_MIN = 60    # rappel au journal tant que la panne dure

# Un seul relevé Envoy à la fois. Le rendu d'une page appelle les fonctions
# d'info les unes après les autres (production, conso, import, export...) et
# chacune passe par get_energy_cached : sans verrou, un cache expiré fait
# partir autant d'interrogations simultanées vers la passerelle, qui sature.
# Avec le verrou, la première rafraîchit, les autres trouvent le cache prêt.
_VERROU = threading.Lock()


def circuit_ouvert():
    """True si l'on est en pause après une série d'échecs."""
    jusqu = _CIRCUIT["rouvre_a"]
    return bool(jusqu and datetime.now() < jusqu)


def _circuit_succes():
    if _CIRCUIT["echecs"]:
        journal(f"Envoy de nouveau joignable après {_CIRCUIT['echecs']} échecs",
                module=MODULE)
    _CIRCUIT.update({"echecs": 0, "rouvre_a": None, "dernier_log": None})


def _circuit_echec(exc):
    """Compte l'échec et ne journalise que ce qui apprend quelque chose.

    Mille fois « Read timed out » ne dit rien de plus que la première fois et
    noie le reste du journal. Mais se taire complètement serait pire : une
    panne qui dure deviendrait indiscernable d'un fonctionnement normal. D'où
    un rappel horaire, qui porte le compte des échecs accumulés.
    """
    maintenant = datetime.now()
    _CIRCUIT["echecs"] += 1
    n = _CIRCUIT["echecs"]
    dernier = _CIRCUIT["dernier_log"]

    if n < CIRCUIT_SEUIL:
        journal(f"Envoy injoignable ({n}/{CIRCUIT_SEUIL}) : {exc}",
                module=MODULE, level=LogEntry.WARNING)
        _CIRCUIT["dernier_log"] = maintenant
    elif n == CIRCUIT_SEUIL:
        journal(
            f"Envoy injoignable après {n} tentatives — appels suspendus "
            f"{CIRCUIT_PAUSE_MIN} min, rappel toutes les "
            f"{CIRCUIT_RAPPEL_MIN} min : {exc}",
            module=MODULE, level=LogEntry.ERROR,
        )
        _CIRCUIT["dernier_log"] = maintenant
    elif dernier is None or maintenant - dernier >= timedelta(minutes=CIRCUIT_RAPPEL_MIN):
        journal(f"Envoy toujours injoignable ({n} échecs) : {exc}",
                module=MODULE, level=LogEntry.ERROR)
        _CIRCUIT["dernier_log"] = maintenant

    if n >= CIRCUIT_SEUIL:
        _CIRCUIT["rouvre_a"] = maintenant + timedelta(minutes=CIRCUIT_PAUSE_MIN)


def get_energy_cached(force=False, ttl_minutes=2):
    """Dernières mesures, du cache si possible, de l'Envoy sinon.

    Sérialisé par ``_VERROU`` : les appels concurrents attendent le premier
    et repartent avec le cache qu'il vient d'écrire, au lieu d'interroger la
    passerelle chacun de leur côté.
    """
    with _VERROU:
        return _relever(force=force, ttl_minutes=ttl_minutes)


def _relever(force=False, ttl_minutes=2):
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

    # Coupe-circuit ouvert : on rend la main tout de suite avec la dernière
    # valeur connue. C'est ce qui empêche la page d'énergie et le tableau de
    # bord de rester bloqués tant que la passerelle ne répond pas.
    if circuit_ouvert():
        msg = "Passerelle injoignable — appels suspendus, dernière valeur connue."
        if cached_data is not None:
            return cached_data, cached_ts, msg
        return None, None, "Passerelle Envoy injoignable (appels suspendus)."

    try:
        data = get_energy()
    except Exception as exc:
        _circuit_echec(exc)
        if cached_data is not None:
            return cached_data, cached_ts, f"Passerelle injoignable ({exc}) — dernière valeur connue."
        return None, None, str(exc)

    _circuit_succes()

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
