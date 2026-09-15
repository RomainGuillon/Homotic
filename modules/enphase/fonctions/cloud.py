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

**Ce module est soumis à un quota mensuel** (plan Watt du portail développeur
Enphase : 1 000 requêtes/mois, soit ~33 par jour). Contrairement à l'Envoy,
qui est local et gratuit, chaque appel ici se paie. Trois garde-fous, à ne pas
retirer sans refaire le calcul ``appels/cycle × cycles/jour × 30`` :

- **un seul relevé pour tout** : les cumuls du jour, la courbe de production
  et celle de consommation viennent d'un unique couple d'appels, mis en cache
  ensemble. Avant, six appels partaient par cycle dont quatre en double ;
- **un intervalle propre au cloud** (``cloud_intervalle_minutes``, 120 min par
  défaut), distinct de la cadence de l'Envoy. Les deux ne doivent jamais
  repartager le même réglage : les scénarios ont besoin de l'Envoy à la
  minute, le cloud ne le supporterait pas ;
- **une pause après échec** : sans elle, un appel en erreur n'écrit aucun
  cache et chaque affichage de page repart interroger l'API. C'est ce qui a
  vidé le quota du mois en quelques heures le 2026-09-15.
"""

import json
import re
import threading
import time as time_mod
from datetime import datetime, timedelta

import requests
from requests.auth import HTTPBasicAuth

from core.models import LogEntry
from core.services import get_setting, journal, set_setting

MODULE = "enphase"

REDIRECT_URI = "https://api.enphaseenergy.com/oauth/redirect_uri"
AUTH_BASE = "https://api.enphaseenergy.com/oauth"
API_BASE = "https://api.enphaseenergy.com/api/v4"

ACCESS_TTL_S = 23 * 3600  # jeton d'accès ~24 h, rafraîchi avant expiration

INTERVALLE_DEFAUT_MIN = 120   # rafraîchissement cloud par défaut
INTERVALLE_PLANCHER_MIN = 30  # en dessous, le quota mensuel ne tient pas
BACKOFF_ECHEC_MIN = 15        # pause après un appel en erreur
DELAI_MINIMAL_S = 60          # deux appels réels ne peuvent pas être plus rapprochés

CACHE_JOUR = "cache_cloud_jour"


# ----------------------------------------------------------------------
# Masquage des secrets dans les messages journalisés
# ----------------------------------------------------------------------

_CHAMPS_SENSIBLES = ("key", "refresh_token", "code", "access_token", "client_secret")
_MOTIF_SENSIBLE = re.compile(
    r"(?i)\b(" + "|".join(_CHAMPS_SENSIBLES) + r")=[^&\s'\"]+"
)


def _masquer(texte):
    """Retire les valeurs sensibles d'un texte avant journalisation.

    Les exceptions de ``requests`` reprennent l'URL complète, donc la clé API
    en clair dans la query string — et pour les échanges de jetons, le refresh
    token ou le code d'autorisation. Le journal d'Homotic se lit depuis
    l'interface web : rien de tout cela n'a à y figurer. Constaté le
    2026-09-15, la clé exposée a dû être régénérée.
    """
    if not texte:
        return texte
    return _MOTIF_SENSIBLE.sub(r"\1=***", str(texte))


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


def intervalle_minutes():
    """Minutes entre deux relevés cloud (réglage ``cloud_intervalle_minutes``).

    Plancher à ``INTERVALLE_PLANCHER_MIN`` : à 2 appels par relevé, descendre
    en dessous fait dépasser les 1 000 requêtes mensuelles du plan Watt.
    """
    try:
        valeur = int(get_setting("cloud_intervalle_minutes", module=MODULE,
                                 default=INTERVALLE_DEFAUT_MIN))
    except (TypeError, ValueError):
        valeur = INTERVALLE_DEFAUT_MIN
    return max(INTERVALLE_PLANCHER_MIN, valeur)


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


def _post_token(params, cfg):
    """POST /oauth/token, erreurs remontées sans les secrets de la requête."""
    r = requests.post(
        f"{AUTH_BASE}/token",
        params=params,
        auth=HTTPBasicAuth(cfg["client_id"], cfg["client_secret"]),
        timeout=20,
    )
    if r.status_code >= 400:
        raise RuntimeError(
            f"{r.status_code} sur {AUTH_BASE}/token : {_masquer(r.text)[:200]}"
        )
    return r.json()


def exchange_code(code):
    """Échange le code d'autorisation contre des jetons (enregistrés en base)."""
    cfg = _check_app_config()
    data = _post_token(
        {"grant_type": "authorization_code", "redirect_uri": REDIRECT_URI, "code": code},
        cfg,
    )
    _save_tokens(data)
    journal("Compte Enphase cloud lié (jetons OAuth enregistrés)", module=MODULE)
    return data


def _refresh_tokens():
    cfg = _check_app_config()
    tokens = _load_tokens()
    if not tokens or "refresh_token" not in tokens:
        raise RuntimeError("Aucun refresh token : relancer l'autorisation cloud.")
    data = _post_token(
        {"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]},
        cfg,
    )
    _save_tokens(data)
    return data


def _get_access_token():
    tokens = _load_tokens()
    if not tokens:
        raise RuntimeError("Système non autorisé : lier le compte dans le paramétrage cloud.")
    if int(time_mod.time()) - tokens.get("obtained_at", 0) >= ACCESS_TTL_S:
        tokens = _refresh_tokens()
    return tokens["access_token"]


# ----------------------------------------------------------------------
# Accès générique
# ----------------------------------------------------------------------

class _NonAutorise(Exception):
    """Réponse 401 : le jeton d'accès présenté n'est plus accepté."""


def _appel(url, params, token):
    r = requests.get(
        url, params=params,
        headers={"Authorization": f"Bearer {token}"},
        timeout=20,
    )
    if r.status_code == 401:
        raise _NonAutorise(_masquer(r.text)[:200])
    if r.status_code >= 400:
        raise RuntimeError(
            f"{r.status_code} {r.reason} sur {_masquer(url)} : {_masquer(r.text)[:200]}"
        )
    return r.json()


def _get_authentifie(url, params):
    """GET authentifié, jeton renouvelé puis requête rejouée une fois sur 401.

    L'âge du jeton ne suffit pas à décider du renouvellement : régénérer la
    clé API, refaire l'autorisation ou une révocation côté portail Enphase
    invalident le jeton d'accès bien avant la fin de son TTL. Tant que le code
    ne se fiait qu'à ``obtained_at + ACCESS_TTL_S``, il repassait indéfiniment
    un jeton mort — la boucle de 401 du 2026-09-15, que quatre redémarrages du
    service n'ont pas dénouée.
    """
    try:
        return _appel(url, params, _get_access_token())
    except _NonAutorise:
        journal(
            "Jeton d'accès Enphase refusé (401) — renouvellement et nouvelle tentative",
            module=MODULE, level=LogEntry.WARNING,
        )
        token = _refresh_tokens()["access_token"]
        try:
            return _appel(url, params, token)
        except _NonAutorise as exc:
            raise RuntimeError(
                "401 même après renouvellement du jeton : l'autorisation OAuth est "
                "à refaire (onglet Énergie → lier le compte). " + str(exc)
            ) from exc


def list_systems():
    cfg = _check_app_config()
    return _get_authentifie(f"{API_BASE}/systems", {"key": cfg["api_key"]})


def _get(path, params=None):
    cfg = _check_app_config()
    if not cfg["system_id"]:
        raise RuntimeError("system_id Enphase manquant (paramétrage cloud).")
    query = {"key": cfg["api_key"]}
    if params:
        query.update(params)
    return _get_authentifie(f"{API_BASE}/systems/{cfg['system_id']}{path}", query)


# ----------------------------------------------------------------------
# Relevé du jour : deux appels, tout en découle
# ----------------------------------------------------------------------

def _sum_intervals(payload, key):
    total = 0.0
    by_time = {}
    for interval in payload.get("intervals", []):
        value = interval.get(key)
        end_at = interval.get("end_at")
        if value is None or end_at is None:
            continue
        v = float(value)
        by_time[end_at] = v
        total += v
    return total, by_time


def _en_kw(by_time):
    """Wh sur un pas de 15 min -> kW moyens sur le pas (Wh / 0,25 h / 1000)."""
    return [{"end_at": t, "kw": wh / 250.0} for t, wh in sorted(by_time.items())]


def get_cloud_day():
    """Tout ce que le cloud fournit pour aujourd'hui, en deux appels.

    Les deux séries télémétriques 15 min portent déjà l'information complète :
    les cumuls du jour sont la somme de leurs intervalles, les courbes sont
    ces mêmes intervalles en kW, et le détail import/export par pas sert au
    chiffrage par tranche.

    ``/summary`` et ``/consumption_lifetime`` ont été retirés : ils donnaient
    des totaux que l'on peut déduire, au prix de deux appels supplémentaires
    par cycle — et de totaux qui pouvaient diverger du coût calculé sur les
    intervalles. Ne pas les remettre sans refaire le calcul de quota.
    """
    prod_tel = _get("/telemetry/production_meter", {"granularity": "day"})
    cons_tel = _get("/telemetry/consumption_meter", {"granularity": "day"})

    production_today, prod_by = _sum_intervals(prod_tel, "wh_del")
    consumption_today, cons_by = _sum_intervals(cons_tel, "enwh")

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
        "production_curve": _en_kw(prod_by),
        "consumption_curve": _en_kw(cons_by),
    }


# ----------------------------------------------------------------------
# Cache unique + pause après échec
# ----------------------------------------------------------------------

# Un seul relevé cloud à la fois : le rendu d'une page demande les cumuls puis
# les deux courbes, et l'onglet comme le tableau de bord passent par là. Sans
# verrou, un cache expiré ferait partir autant de relevés simultanés.
_VERROU = threading.Lock()

# Pause après échec — l'équivalent du coupe-circuit de l'Envoy (api.py), mais
# ici ce n'est pas le temps d'attente que l'on protège, c'est le quota.
_ECHEC = {"jusqu_a": None, "message": ""}
_DERNIER_APPEL = {"a": None}


def _lire_cache():
    raw = get_setting(CACHE_JOUR, module=MODULE)
    if not raw:
        return None, None
    try:
        payload = json.loads(raw)
        return payload["data"], datetime.fromisoformat(payload["ts"])
    except (ValueError, KeyError, TypeError):
        return None, None


def _perime(ts, now):
    """Un cache d'hier ne vaut rien pour des données « du jour »."""
    return ts is None or ts.date() != now.date()


def jour_cached(force=False):
    """Relevé du jour, du cache si possible. Retourne ``(data, ts, erreur)``.

    ``force`` (bouton « Actualiser ») ignore le TTL et la pause après échec,
    mais pas le délai minimal entre deux appels réels : un clic répété ne doit
    pas pouvoir vider le quota.
    """
    with _VERROU:
        return _relever(force=force)


def _relever(force=False):
    now = datetime.now()
    data, ts = _lire_cache()
    if _perime(ts, now):
        data, ts = None, None

    if data is not None and not force and now - ts < timedelta(minutes=intervalle_minutes()):
        return data, ts, ""

    dernier = _DERNIER_APPEL["a"]
    if dernier and (now - dernier).total_seconds() < DELAI_MINIMAL_S:
        if data is not None:
            return data, ts, ""
        return None, None, "Appel cloud trop rapproché du précédent — réessayer dans un instant."

    jusqu = _ECHEC["jusqu_a"]
    if jusqu and now < jusqu and not force:
        msg = _ECHEC["message"]
        if data is not None:
            return data, ts, f"Cloud indisponible ({msg}) — dernière valeur connue."
        return None, None, msg

    _DERNIER_APPEL["a"] = now
    try:
        neuf = get_cloud_day()
    except Exception as exc:
        msg = _masquer(str(exc))
        _ECHEC.update({"jusqu_a": now + timedelta(minutes=BACKOFF_ECHEC_MIN), "message": msg})
        journal(
            f"Erreur Enphase cloud : {msg} — appels suspendus {BACKOFF_ECHEC_MIN} min",
            module=MODULE, level=LogEntry.ERROR,
        )
        if data is not None:
            return data, ts, f"Cloud indisponible ({msg}) — dernière valeur connue."
        return None, None, msg

    if _ECHEC["jusqu_a"]:
        journal("Cloud Enphase de nouveau joignable", module=MODULE)
    _ECHEC.update({"jusqu_a": None, "message": ""})

    set_setting(CACHE_JOUR, json.dumps({"ts": now.isoformat(), "data": neuf}), module=MODULE)
    return neuf, now, ""


def etat_cloud():
    """État du relevé cloud, pour l'affichage du paramétrage."""
    _data, ts = _lire_cache()
    return {
        "dernier_releve": ts,
        "intervalle": intervalle_minutes(),
        "suspendu_jusqu_a": _ECHEC["jusqu_a"],
        "derniere_erreur": _ECHEC["message"],
    }


# ----------------------------------------------------------------------
# Vues du relevé (même cache, aucun appel supplémentaire)
# ----------------------------------------------------------------------

_CLES_TOTAUX = (
    "production_wh_today", "consumption_wh_today",
    "import_wh_today", "export_wh_today",
)


def get_daily_totals_cached(force=False):
    """Cumuls du jour (Wh) + détail 15 min pour le chiffrage par tranche."""
    data, ts, err = jour_cached(force=force)
    if data is None:
        return None, None, err
    totaux = {cle: data.get(cle, 0.0) for cle in _CLES_TOTAUX}
    totaux["intervals"] = data.get("intervals", [])
    return totaux, ts, err


def get_production_curve_cached(force=False):
    """Production du jour par pas de 15 min : [{end_at, kw}]."""
    data, ts, err = jour_cached(force=force)
    return (data.get("production_curve") if data else None), ts, err


def get_consumption_curve_cached(force=False):
    """Consommation du jour par pas de 15 min : [{end_at, kw}]."""
    data, ts, err = jour_cached(force=force)
    return (data.get("consumption_curve") if data else None), ts, err


# Compatibilité : ces trois noms étaient l'API publique du module avant le
# passage au relevé unique. Ils restent, mais chacun déclenchait son propre
# aller-retour — passer par les versions « _cached » ci-dessus.
def get_daily_totals():
    data = get_cloud_day()
    totaux = {cle: data[cle] for cle in _CLES_TOTAUX}
    totaux["intervals"] = data["intervals"]
    return totaux


def get_production_curve():
    return get_cloud_day()["production_curve"]


def get_consumption_curve():
    return get_cloud_day()["consumption_curve"]
