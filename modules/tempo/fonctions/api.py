"""Accès à l'API RTE Tempo — identifiants et tarifs lus en base.

Le « jour Tempo » court de 6h à 6h : sur une journée calendaire,
00h-06h porte la couleur de la veille, 06h-24h celle du jour.

Repris de la v1 (tempo/calendar_api.py), adapté : plus de .env, tout vient
de la table de configuration (module « tempo »), avec cache en base pour
ne pas marteler l'API RTE.
"""

import json
from datetime import date, datetime, time, timedelta

import requests
from requests.auth import HTTPBasicAuth

from core.models import LogEntry
from core.services import get_setting, journal, set_setting

MODULE = "tempo"

TOKEN_URL = "https://digital.iservices.rte-france.com/token/oauth/"
CAL_URL = (
    "https://digital.iservices.rte-france.com/open_api/"
    "tempo_like_supply_contract/v1/tempo_like_calendars"
)

# Quotas annuels EDF Tempo (saison 1er sept -> 31 août)
QUOTAS = {"BLUE": 300, "WHITE": 43, "RED": 22}

# Tarifs par défaut TTC €/kWh (à ajuster dans l'onglet Tempo)
DEFAULT_PRICES = {
    "BLUE": {"HP": 0.1609, "HC": 0.1296},
    "WHITE": {"HP": 0.1894, "HC": 0.1486},
    "RED": {"HP": 0.7562, "HC": 0.1568},
}

# Affichage : nom FR + couleurs HP/HC + couleurs de texte
COLORS = {
    "BLUE": {"name": "Bleu", "hp": "#2563eb", "hc": "#93c5fd", "thp": "#ffffff", "thc": "#0b1220"},
    "WHITE": {"name": "Blanc", "hp": "#cbd5e1", "hc": "#f1f5f9", "thp": "#0b1220", "thc": "#0b1220"},
    "RED": {"name": "Rouge", "hp": "#dc2626", "hc": "#fca5a5", "thp": "#ffffff", "thc": "#0b1220"},
    None: {"name": "Inconnu", "hp": "#6b7280", "hc": "#d1d5db", "thp": "#ffffff", "thc": "#0b1220"},
}


# ----------------------------------------------------------------------
# Paramètres (base de configuration)
# ----------------------------------------------------------------------

def credentials():
    return (
        get_setting("client_id", module=MODULE, default=""),
        get_setting("client_secret", module=MODULE, default=""),
    )


def configured():
    cid, secret = credentials()
    return bool(cid and secret)


def _float(key, default):
    raw = get_setting(key, module=MODULE)
    try:
        return float(str(raw).replace(",", "."))
    except (TypeError, ValueError):
        return default


def _int(key, default):
    raw = get_setting(key, module=MODULE)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def get_prices():
    """Tarifs €/kWh par couleur/période, depuis la base (défauts sinon)."""
    prices = {}
    for color in ("BLUE", "WHITE", "RED"):
        prices[color] = {
            per: _float(f"prix_{color.lower()}_{per.lower()}", DEFAULT_PRICES[color][per])
            for per in ("HP", "HC")
        }
    return prices


def abonnement_mensuel():
    """Abonnement EDF mensuel (€), pour le coût du jour (module énergie)."""
    return _float("abonnement_mensuel", 15.0)


def prix_revente():
    """Prix de revente du surplus injecté (€/kWh)."""
    return _float("prix_revente", 0.04)


def hc_bounds():
    """Plage heures creuses (début, fin), par défaut 22h -> 6h."""
    return _int("hc_debut", 22), _int("hc_fin", 6)


def current_period(dt=None):
    """"HC" ou "HP" à l'instant donné (plage HC à cheval sur minuit gérée)."""
    dt = dt or datetime.now()
    debut, fin = hc_bounds()
    h = dt.hour + dt.minute / 60.0
    if debut > fin:  # ex : 22h -> 6h
        return "HC" if (h >= debut or h < fin) else "HP"
    return "HC" if (debut <= h < fin) else "HP"


def color_of_moment(colors, dt=None):
    """Couleur Tempo à l'instant donné (00h-06h = couleur de la veille)."""
    dt = dt or datetime.now()
    d = dt.date() if dt.hour >= 6 else dt.date() - timedelta(days=1)
    return colors.get(str(d))


# ----------------------------------------------------------------------
# API RTE (repris de la v1)
# ----------------------------------------------------------------------

def _local_offset():
    off = datetime.now().astimezone().strftime("%z")  # ex : +0200
    return off[:3] + ":" + off[3:]


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + _local_offset()


def get_access_token():
    cid, secret = credentials()
    if not cid or not secret:
        raise RuntimeError(
            "Identifiants RTE manquants : renseigner la clé et le secret "
            "dans le paramétrage de l'onglet Tempo."
        )
    r = requests.post(
        TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={"grant_type": "client_credentials"},
        auth=HTTPBasicAuth(cid, secret),
        timeout=15,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def get_calendar(start, end):
    token = get_access_token()
    r = requests.get(
        CAL_URL,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        params={"start_date": _iso(start), "end_date": _iso(end)},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def get_colors(reference=None):
    """Couleurs Tempo veille/jour/lendemain : {'YYYY-MM-DD': 'BLUE'|...}."""
    ref = reference or date.today()
    start = datetime.combine(ref - timedelta(days=1), time.min)

    # RTE ne renvoie un jour que si sa période entière tient dans la fenêtre.
    # Fin à J+2 minuit pour inclure demain ; si RTE refuse (demain pas encore
    # publié), on retombe sur J+1 minuit.
    raw = None
    for delta in (2, 1):
        end = datetime.combine(ref + timedelta(days=delta), time.min)
        try:
            raw = get_calendar(start, end)
            break
        except requests.HTTPError as exc:
            if delta == 2 and getattr(exc.response, "status_code", None) == 400:
                continue
            raise
    if raw is None:
        return {}

    values = raw.get("tempo_like_calendars", {}).get("values", [])
    colors = {}
    for v in values:
        d = str(v.get("start_date", ""))[:10]
        if d:
            colors[d] = v.get("value")
    return colors


def season_start(ref):
    """Début de la saison Tempo (1er septembre) contenant `ref`."""
    return date(ref.year, 9, 1) if ref.month >= 9 else date(ref.year - 1, 9, 1)


def get_season_counts(reference=None):
    """Jours comptés par couleur depuis le début de saison (+ restants)."""
    ref = reference or date.today()
    s = season_start(ref)
    today_str = str(ref)

    start = datetime.combine(s, time.min)
    end = datetime.combine(ref + timedelta(days=2), time.min)

    raw_values = []
    try:
        raw_values = get_calendar(start, end).get(
            "tempo_like_calendars", {}).get("values", [])
    except requests.HTTPError:
        # Repli en tranches chevauchantes si l'API refuse la saison entière
        cur = s
        last = ref + timedelta(days=2)
        while cur < last:
            ce = min(cur + timedelta(days=45), last)
            try:
                vals = get_calendar(
                    datetime.combine(cur, time.min),
                    datetime.combine(ce, time.min),
                ).get("tempo_like_calendars", {}).get("values", [])
            except requests.HTTPError:
                break
            raw_values.extend(vals)
            cur = ce - timedelta(days=2)

    colors = {}
    for v in raw_values:
        d = str(v.get("start_date", ""))[:10]
        if d and d <= today_str:  # demain pas encore consommé
            colors[d] = v.get("value")

    counts = {"BLUE": 0, "WHITE": 0, "RED": 0}
    by_color = {"BLUE": [], "WHITE": [], "RED": []}
    for d, c in sorted(colors.items()):
        if c in counts:
            counts[c] += 1
            by_color[c].append(d)

    return {
        "counts": counts,
        "remaining": {k: QUOTAS[k] - counts[k] for k in QUOTAS},
        "quotas": QUOTAS,
        "season_start": str(s),
        "days_counted": len(colors),
        "by_color": by_color,
    }


# ----------------------------------------------------------------------
# Cache en base (le scheduler prendra le relais à l'étape 5)
# ----------------------------------------------------------------------

def _cached(key, fetch, ttl_minutes, force=False):
    """Cache JSON en base : {ts, data}. Retourne (data, ts, erreur).

    En cas d'échec de l'API, sert le cache périmé s'il existe.
    """
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
        journal(f"Erreur API RTE : {exc}", module=MODULE, level=LogEntry.ERROR)
        if cached_data is not None:
            return cached_data, cached_ts, f"API indisponible ({exc}) — données en cache."
        return None, None, str(exc)

    set_setting(key, json.dumps({"ts": now.isoformat(), "data": data}), module=MODULE)
    return data, now, ""


def get_colors_cached(force=False):
    """Couleurs veille/jour/lendemain, cache 30 min."""
    data, ts, err = _cached("cache_colors", get_colors, 30, force=force)
    return (data or {}), ts, err


def get_season_cached(force=False):
    """Compteurs de saison, cache 6 h."""
    return _cached("cache_season", get_season_counts, 360, force=force)


def tache_actualiser():
    """Tâche périodique (scheduler) : rafraîchit couleurs et compteurs."""
    if not configured():
        return
    get_colors_cached(force=True)
    get_season_cached(force=True)
