"""Prévisions de production solaire via l'API Solcast.

Repris de la v1 (solcast/forecast.py), adapté : clé API et sites en base,
caches en base (au lieu du disque).

Quota gratuit Solcast : **10 appels/jour pour le compte**, et **un appel par
site** à chaque requête (2 sites ici → 2 appels par rafraîchissement, donc
5 rafraîchissements par jour au maximum). Garde-fous cumulés :

- **appels réservés au rafraîchissement planifié** : un affichage de page ne
  peut pas appeler l'API (voir ``appels_autorises()``). Les heures d'appel
  sont celles des TACHES du ``conf.py``, donc connues d'avance ;
- **compteur d'appels du jour** plafonné (réglage ``quota_jour``, 10 par
  défaut) : au-delà, plus aucun appel jusqu'au lendemain ;
- **backoff après erreur** : une erreur réseau bloque les appels 30 min, un
  429 (quota épuisé) les bloque jusqu'au lendemain 6h ;
- TTL 4 h sur les prévisions, 24 h sur l'estimation passée, et repli sur le
  cache périmé en cas d'erreur.

Historique : avant ces garde-fous, chaque rendu de page pouvait appeler
l'API dès le cache périmé, et un 429 n'écrivant aucun cache, le rendu
suivant réessayait — d'où des rafales de dizaines d'appels refusés.
L'historique des prévisions déjà vues comble les créneaux passés que
/forecasts ne renvoie plus.
"""

import json
import threading
import time as time_mod
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone

import requests

from core.models import LogEntry
from core.services import get_setting, journal, set_setting, set_variable

MODULE = "solcast"
BASE_URL = "https://api.solcast.com.au"

# TTL 4 h : sur la fenêtre 6h-18h cela fait 3 rafraîchissements par jour,
# soit 3 × (1 appel par site) = 6 appels avec 2 sites, + 2 pour l'estimation
# passée = 8 appels/jour, sous le quota gratuit de 10.
FORECAST_TTL_S = 4 * 3600
ESTIMATED_TTL_S = 24 * 3600
DAY_START_HOUR, DAY_END_HOUR = 6, 18
HISTORY_MAX_AGE_S = 36 * 3600

QUOTA_JOUR_DEFAUT = 10  # appels/jour du plan gratuit Solcast
BACKOFF_ERREUR_S = 30 * 60  # pause après une erreur réseau/serveur


class AppelRefuse(RuntimeError):
    """Appel non effectué (quota du jour atteint ou backoff en cours)."""


# ----------------------------------------------------------------------
# Paramètres
# ----------------------------------------------------------------------

def api_key():
    return get_setting("api_key", module=MODULE, default="")


def resource_ids():
    raw = get_setting("resource_ids", module=MODULE, default="") or ""
    return [x.strip() for x in raw.split(",") if x.strip()]


def site_labels():
    raw = get_setting("site_labels", module=MODULE, default="") or ""
    labels = [x.strip() for x in raw.split(",") if x.strip()]
    rids = resource_ids()
    return {
        rid: (labels[i] if i < len(labels) else f"Site {i + 1}")
        for i, rid in enumerate(rids)
    }


def configured():
    return bool(api_key() and resource_ids())


def _check():
    if not api_key():
        raise RuntimeError("Clé Solcast manquante : renseigner le paramétrage de l'onglet Solaire.")
    if not resource_ids():
        raise RuntimeError("Identifiant de site Solcast manquant (resource id).")


# ----------------------------------------------------------------------
# Utilitaires temps (repris v1)
# ----------------------------------------------------------------------

def _parse_time(period_end):
    s = period_end.replace("Z", "")
    if "." in s:
        s = s.split(".")[0]
    dt = datetime.strptime(s, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    return dt.astimezone()


def _period_hours(period):
    if isinstance(period, str) and period.startswith("PT") and period.endswith("M"):
        try:
            return int(period[2:-1]) / 60.0
        except ValueError:
            pass
    return 0.5


# ----------------------------------------------------------------------
# Garde-fous quota : autorisation, compteur du jour, backoff après erreur
# ----------------------------------------------------------------------

# Un affichage de page ne doit JAMAIS appeler l'API : sinon le nombre
# d'appels du jour dépend du nombre de fois où on regarde le tableau de
# bord, et le quota part en fumée (c'est ce qui s'est produit le 27/07/2026).
# Seul le code qui s'exécute dans ``appels_autorises()`` peut appeler
# Solcast : la tâche à heures fixes et le bouton « Actualiser ».
_ctx = threading.local()


@contextmanager
def appels_autorises():
    """Autorise les appels API dans ce bloc (et ce thread uniquement)."""
    precedent = getattr(_ctx, "ok", False)
    _ctx.ok = True
    try:
        yield
    finally:
        _ctx.ok = precedent

def quota_jour():
    try:
        return int(get_setting("quota_jour", module=MODULE, default=QUOTA_JOUR_DEFAUT))
    except (TypeError, ValueError):
        return QUOTA_JOUR_DEFAUT


def _compteur_lire():
    """Retourne (jour ISO, nombre d'appels) du compteur, remis à zéro chaque jour."""
    raw = get_setting("appels_jour", module=MODULE)
    aujourdhui = str(date.today())
    if not raw:
        return aujourdhui, 0
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return aujourdhui, 0
    if payload.get("jour") != aujourdhui:
        return aujourdhui, 0  # nouveau jour : compteur reparti de zéro
    try:
        return aujourdhui, int(payload.get("n", 0))
    except (TypeError, ValueError):
        return aujourdhui, 0


def appels_du_jour():
    return _compteur_lire()[1]


def compteur_sature():
    """Vrai si le compteur a été aligné sur un 429 plutôt que compté localement."""
    raw = get_setting("appels_jour", module=MODULE)
    if not raw:
        return False
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return False
    return payload.get("jour") == str(date.today()) and bool(payload.get("sature"))


def _compteur_incrementer():
    jour, n = _compteur_lire()
    set_setting("appels_jour", json.dumps({"jour": jour, "n": n + 1}), module=MODULE)
    return n + 1


def _compteur_saturer():
    """Marque le quota du jour comme épuisé.

    Le compteur local ne voit que les appels passés par ce code : il ignore
    ceux de la v1, d'un autre client, ou d'avant sa mise en place. Un 429
    signifie que Solcast, lui, a bien compté 10 appels : c'est la seule
    source de vérité, on aligne le compteur dessus.
    """
    jour, n = _compteur_lire()
    set_setting(
        "appels_jour",
        json.dumps({"jour": jour, "n": max(n, quota_jour()), "sature": True}),
        module=MODULE,
    )


def _demain_6h():
    d = datetime.now() + timedelta(days=1)
    return d.replace(hour=DAY_START_HOUR, minute=0, second=0, microsecond=0)


def _poser_backoff(jusqua, raison):
    """Bloque les appels jusqu'à ``jusqua`` et journalise une seule fois."""
    set_setting(
        "backoff",
        json.dumps({"jusqua": jusqua.isoformat(), "raison": raison}),
        module=MODULE,
    )
    journal(
        f"Appels Solcast suspendus jusqu'à {jusqua.strftime('%d/%m %H:%M')} — {raison}",
        module=MODULE,
        level=LogEntry.WARNING,
    )


def backoff():
    """Retourne {jusqua: datetime, raison} si les appels sont suspendus, sinon None."""
    raw = get_setting("backoff", module=MODULE)
    if not raw:
        return None
    try:
        payload = json.loads(raw)
        jusqua = datetime.fromisoformat(payload["jusqua"])
    except (ValueError, TypeError, KeyError):
        return None
    if datetime.now() >= jusqua:
        return None  # période écoulée
    return {"jusqua": jusqua, "raison": payload.get("raison", "")}


def horaires_planifies():
    """Horaires de rafraîchissement : [(heure, minute)] triés.

    Vient du réglage ``tache_previsions_heures``, ou à défaut de la liste du
    ``conf.py`` du module.
    """
    from core.scheduler import horaires_texte, parse_horaires

    defaut = ""
    try:
        from modules.solcast import conf

        for t in getattr(conf, "TACHES", []):
            if t.get("heures"):
                defaut = horaires_texte(t["heures"])
                break
    except Exception:
        pass
    return parse_horaires(get_setting("tache_previsions_heures", module=MODULE, default=defaut))


def etat_quota():
    """État des garde-fous et du budget d'appels, pour l'onglet Solaire."""
    bo = backoff()
    appels, quota = appels_du_jour(), quota_jour()
    # Un backoff en cours = plus aucun appel possible : afficher 0 restant,
    # jamais un reliquat théorique qui contredirait le bandeau de suspension.
    restants = 0 if bo else max(0, quota - appels)
    cout = max(1, len(resource_ids()))  # un appel par site à chaque requête

    horaires = horaires_planifies()
    maintenant = datetime.now()
    apres = (maintenant.hour, maintenant.minute)
    a_venir = [h for h in horaires if h > apres]
    prevus = len(a_venir) * cout
    fmt = lambda hm: f"{hm[0]:02d}:{hm[1]:02d}"  # noqa: E731

    return {
        "appels": appels,
        "quota": quota,
        "restants": restants,
        "sature": compteur_sature(),
        "cout_par_refresh": cout,
        # Nombre de rafraîchissements encore finançables avec ce qui reste
        "refresh_restants": restants // cout,
        "horaires": [fmt(h) for h in horaires],
        "heures_a_venir": [fmt(h) for h in a_venir],
        "prochaine_heure": fmt(a_venir[0]) if a_venir else (
            fmt(horaires[0]) if horaires else None
        ),
        "appels_prevus": prevus,
        # Vrai si les passages restants de la journée ne tiennent pas dans le budget
        "insuffisant": prevus > restants,
        "suspendu_jusqua": bo["jusqua"] if bo else None,
        "raison": bo["raison"] if bo else "",
    }


def _autoriser_appel(nb=1):
    """Lève ``AppelRefuse`` si le quota est atteint ou un backoff est en cours.

    ``nb`` : nombre d'appels que l'on s'apprête à faire (un par site). On
    vérifie le lot entier d'avance, pour ne pas gaspiller un appel sur un
    rafraîchissement qui ne pourra pas aboutir.
    """
    if not getattr(_ctx, "ok", False):
        raise AppelRefuse("lecture du cache seule (hors rafraîchissement planifié)")
    bo = backoff()
    if bo:
        raise AppelRefuse(
            f"appels suspendus jusqu'à {bo['jusqua'].strftime('%H:%M')} ({bo['raison']})"
        )
    n, plafond = appels_du_jour(), quota_jour()
    if n + nb > plafond:
        raise AppelRefuse(f"quota du jour atteint ({n}/{plafond} appels)")


def _get(resource_id, endpoint):
    _autoriser_appel()
    url = f"{BASE_URL}/rooftop_sites/{resource_id}/{endpoint}?format=json"
    _compteur_incrementer()  # compté même en cas d'échec : l'appel est parti
    try:
        r = requests.get(url, headers={"Authorization": f"Bearer {api_key()}"}, timeout=20)
        r.raise_for_status()
    except requests.HTTPError as exc:
        code = getattr(exc.response, "status_code", None)
        if code == 429:
            # Quota épuisé côté Solcast : inutile de réessayer aujourd'hui.
            # Le compteur est aligné sur cette vérité, sinon l'onglet
            # afficherait des appels restants alors qu'il n'y en a plus.
            _compteur_saturer()
            _poser_backoff(_demain_6h(), "quota Solcast épuisé (429)")
        else:
            _poser_backoff(
                datetime.now() + timedelta(seconds=BACKOFF_ERREUR_S),
                f"erreur HTTP {code}",
            )
        raise
    except requests.RequestException as exc:
        _poser_backoff(
            datetime.now() + timedelta(seconds=BACKOFF_ERREUR_S),
            f"erreur réseau ({type(exc).__name__})",
        )
        raise
    return r.json()


# ----------------------------------------------------------------------
# Caches en base ({ts (epoch), data}), remplaçant les fichiers de la v1
# ----------------------------------------------------------------------

def _cache_read(key):
    raw = get_setting(key, module=MODULE)
    if not raw:
        return None, None
    try:
        payload = json.loads(raw)
        return payload.get("data"), float(payload.get("ts", 0))
    except (ValueError, TypeError):
        return None, None


def _cache_fresh(key, ttl):
    data, ts = _cache_read(key)
    if data is not None and ts and (time_mod.time() - ts) < ttl:
        return data
    return None


def _cache_any(key):
    data, _ts = _cache_read(key)
    return data


def _cache_write(key, data):
    set_setting(key, json.dumps({"ts": time_mod.time(), "data": data}), module=MODULE)


# ----------------------------------------------------------------------
# Origine des données : date du dernier appel réel à l'API
# ----------------------------------------------------------------------

def marquer_source(origine):
    """Mémorise la date du dernier appel réel (``origine`` vaut « api »)."""
    set_setting(
        "source_donnees",
        json.dumps({"origine": origine, "quand": datetime.now().isoformat()}),
        module=MODULE,
    )


def source_donnees():
    """Retourne {origine, quand} — origine « api » ou « inconnue »."""
    raw = get_setting("source_donnees", module=MODULE)
    if not raw:
        return {"origine": "inconnue", "quand": None}
    try:
        payload = json.loads(raw)
        return {
            "origine": payload.get("origine", "inconnue"),
            "quand": datetime.fromisoformat(payload["quand"]) if payload.get("quand") else None,
        }
    except (ValueError, TypeError, KeyError):
        return {"origine": "inconnue", "quand": None}


# ----------------------------------------------------------------------
# Historique des prévisions déjà vues (repris v1)
# ----------------------------------------------------------------------

def _cle_history(site=""):
    """Réglage où est mémorisé l'historique — global, ou d'un pan de toiture."""
    return f"forecast_history_site_{site}" if site else "forecast_history"


def _history_load(site=""):
    raw = get_setting(_cle_history(site), module=MODULE)
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return {}


def _history_merge(by_time, site=""):
    history = _history_load(site)
    history.update(by_time)
    cutoff = (datetime.now().astimezone() - timedelta(seconds=HISTORY_MAX_AGE_S)).isoformat()
    history = {t: v for t, v in history.items() if t >= cutoff}
    set_setting(_cle_history(site), json.dumps(history), module=MODULE)


def _completer(periods, site=""):
    """Ajoute les pas déjà vus que le dernier appel ne rapporte plus.

    L'horizon Solcast part de l'heure de l'appel : un rafraîchissement de 15h
    ne dit plus rien du matin. Sans ce recollage, la courbe et le cumul de la
    journée en cours perdraient toute leur matinée.
    """
    history = _history_load(site)
    if not history:
        return periods
    seen = {p["time"] for p in periods}
    extra = [
        {"time": t, "pv_kw": v[0], "pv10": v[1], "pv90": v[2]}
        for t, v in history.items() if t not in seen
    ]
    if not extra:
        return periods
    return sorted(periods + extra, key=lambda p: p["time"])


def _augment_periods_with_history(data):
    periods = _completer(data.get("periods", []))
    sites = {
        label: {**site, "periods": _completer(site.get("periods", []), site=label)}
        for label, site in (data.get("sites") or {}).items()
    }
    return {**data, "periods": periods, "sites": sites} if sites else {**data, "periods": periods}


# ----------------------------------------------------------------------
# Prévisions (repris v1)
# ----------------------------------------------------------------------

def _fetch_forecast():
    _autoriser_appel(len(resource_ids()))  # un appel par site : on vérifie le lot
    by_time = {}
    daily = {}
    sites = {}
    labels = site_labels()
    for resource_id in resource_ids():
        label = labels.get(resource_id, resource_id)
        site_by_time = {}
        site_daily = {}
        for f in _get(resource_id, "forecasts").get("forecasts", []):
            pe = f.get("period_end")
            if not pe:
                continue
            hours = _period_hours(f.get("period", "PT30M"))
            # Valeur placée au milieu de l'intervalle pour s'aligner sur le réel
            t = (_parse_time(pe) - timedelta(hours=hours / 2)).isoformat()
            pv = float(f.get("pv_estimate", 0) or 0)
            pv10 = float(f.get("pv_estimate10", 0) or 0)
            pv90 = float(f.get("pv_estimate90", 0) or 0)

            acc = by_time.setdefault(t, [0.0, 0.0, 0.0])
            acc[0] += pv
            acc[1] += pv10
            acc[2] += pv90
            day = t[:10]
            daily[day] = daily.get(day, 0.0) + pv * hours

            site_acc = site_by_time.setdefault(t, [0.0, 0.0, 0.0])
            site_acc[0] += pv
            site_acc[1] += pv10
            site_acc[2] += pv90
            site_daily[day] = site_daily.get(day, 0.0) + pv * hours

        sites[label] = {
            "periods": [{"time": t, "pv_kw": v[0], "pv10": v[1], "pv90": v[2]}
                        for t, v in sorted(site_by_time.items())],
            "daily": site_daily,
        }
        # Même historique que la courbe globale, mais par pan de toiture :
        # sans lui, la courbe et le cumul d'un pan s'arrêtent à l'heure du
        # dernier appel et le matin de la journée en cours disparaît.
        _history_merge(site_by_time, site=label)

    periods = [{"time": t, "pv_kw": v[0], "pv10": v[1], "pv90": v[2]}
               for t, v in sorted(by_time.items())]
    _history_merge(by_time)
    marquer_source("api")  # appel réel : les données ne viennent plus de la v1
    return {"periods": periods, "daily": daily, "sites": sites}


def _deser_forecast(data):
    periods = [{**p, "time": datetime.fromisoformat(p["time"])} for p in data["periods"]]
    # Détail par site (pan de toiture) : sous-produit du même appel API
    sites = {
        label: {
            "periods": [
                {**p, "time": datetime.fromisoformat(p["time"])}
                for p in site.get("periods", [])
            ],
            "daily": site.get("daily", {}),
        }
        for label, site in (data.get("sites") or {}).items()
    }
    return {"periods": periods, "daily": data["daily"], "sites": sites}


def get_sites():
    """Prévisions détaillées par pan de toiture : {libellé: {periods, daily}}.

    Vient du même appel/cache que get_forecast() : aucun coût de quota
    supplémentaire. Vide si le cache a été écrit avant l'ajout du détail.
    """
    try:
        return get_forecast().get("sites", {})
    except Exception:
        return {}


def get_forecast(force=False):
    """Prévisions sommées : {periods: [{time, pv_kw, pv10, pv90}], daily: {j: kWh}}.

    ``force`` : ignore le TTL et rafraîchit (réservé aux heures planifiées).
    Sans lui, deux heures rapprochées (15h puis 17h) auraient été avalées par
    le TTL de 4 h et le passage de 17h n'aurait rien fait.
    """
    _check()
    cached = None if force else _cache_fresh("cache_forecast", FORECAST_TTL_S)
    if cached:
        return _deser_forecast(_augment_periods_with_history(cached))

    stale = _cache_any("cache_forecast")
    # Plus de garde-fou « fenêtre 6h-18h » : les heures d'appel sont
    # désormais explicites (TACHES du conf.py), et le passage de 3h doit
    # pouvoir rafraîchir pour le calcul de l'heure de démarrage.
    try:
        result = _fetch_forecast()
    except Exception as exc:
        # AppelRefuse : déjà journalisé une fois par _poser_backoff, on se
        # contente du cache périmé sans remplir le journal à chaque page.
        if not isinstance(exc, AppelRefuse):
            journal(f"Erreur API Solcast : {exc}", module=MODULE, level=LogEntry.ERROR)
        if stale:
            return _deser_forecast(_augment_periods_with_history(stale))
        raise
    _cache_write("cache_forecast", result)
    return _deser_forecast(_augment_periods_with_history(result))


# ----------------------------------------------------------------------
# Estimation des heures écoulées (repris v1)
# ----------------------------------------------------------------------

def _fetch_estimated_actuals():
    _autoriser_appel(len(resource_ids()))  # un appel par site : on vérifie le lot
    by_time = {}
    for resource_id in resource_ids():
        for a in _get(resource_id, "estimated_actuals").get("estimated_actuals", []):
            pe = a.get("period_end")
            if not pe:
                continue
            hours = _period_hours(a.get("period", "PT30M"))
            t = (_parse_time(pe) - timedelta(hours=hours / 2)).isoformat()
            by_time[t] = by_time.get(t, 0.0) + float(a.get("pv_estimate", 0) or 0)
    return [{"time": t, "pv_kw": v} for t, v in sorted(by_time.items())]


def _covers_today(cached):
    if not cached:
        return False
    today_str = datetime.now().date().isoformat()
    return any(p.get("time", "").startswith(today_str) for p in cached)


def get_estimated_actuals():
    """Production estimée des heures écoulées : [{time, pv_kw}]."""
    _check()
    cached = _cache_fresh("cache_estimated", ESTIMATED_TTL_S)
    if cached is not None and _covers_today(cached):
        return [{"time": datetime.fromisoformat(p["time"]), "pv_kw": p["pv_kw"]} for p in cached]

    try:
        result = _fetch_estimated_actuals()
        _cache_write("cache_estimated", result)
        return [{"time": datetime.fromisoformat(p["time"]), "pv_kw": p["pv_kw"]} for p in result]
    except Exception as exc:
        if not isinstance(exc, AppelRefuse):
            journal(
                f"Erreur API Solcast (estimation) : {exc}",
                module=MODULE,
                level=LogEntry.ERROR,
            )
        fallback = cached if cached is not None else (_cache_any("cache_estimated") or [])
        return [{"time": datetime.fromisoformat(p["time"]), "pv_kw": p["pv_kw"]} for p in fallback]


# ----------------------------------------------------------------------
# Résumé : cumuls + meilleur créneau chauffe-eau (repris v1, sans pandas)
# ----------------------------------------------------------------------

def window_minutes(d):
    """Durée de chauffe à utiliser pour le meilleur créneau du jour ``d``.

    Vient du besoin ``creneau_chauffe`` quand il est branché : c'est la
    durée réellement retenue par le module qui décide de la chauffe (saison
    Été/Hiver, températures de consigne réglées), pas une approximation.

    Repli sur 60 min du 1er mai au 15 oct, 90 min sinon, quand la liaison
    n'est pas branchée (module Heure de démarrage absent, ou source non
    choisie dans Configuration → Liaisons) — mieux vaut une durée
    approximative que pas de meilleur créneau du tout.
    """
    from core.liaisons import lire_besoin

    creneau, _err = lire_besoin(MODULE, "creneau_chauffe")
    if creneau and creneau.get("duree_min"):
        try:
            return int(creneau["duree_min"])
        except (TypeError, ValueError):
            pass

    md = (d.month, d.day)
    return 60 if (5, 1) <= md <= (10, 15) else 90


def _best_window(points, n):
    """Créneau de n pas de 30 min consécutifs maximisant la production.

    ``points`` : [(datetime_milieu_de_pas, kw)]. Retourne
    {start, end, kwh} ou None.
    """
    if len(points) < n:
        return None
    best_i, best_sum = None, 0.0
    for i in range(len(points) - n + 1):
        s = sum(kw for _t, kw in points[i:i + n])
        if s > best_sum:
            best_sum, best_i = s, i
    if best_i is None or best_sum <= 0:
        return None
    return {
        "start": points[best_i][0] - timedelta(minutes=15),
        "end": points[best_i + n - 1][0] + timedelta(minutes=15),
        "kwh": best_sum * 0.5,
    }


def journee_complete(points):
    """Les prévisions couvrent-elles la journée entière ?

    L'horizon Solcast fait 48 h à partir de l'appel : le rafraîchissement de
    17h s'arrête donc vers 17h le surlendemain, et la journée de « demain »
    est amputée de sa fin d'après-midi. Le cumul de cette journée-là vaut
    alors nettement moins que la réalité — un chiffre faux, pas un chiffre
    approximatif.

    Le pas étant daté au milieu de l'intervalle, une journée complète va
    jusqu'à 23:45 ; on tolère 23:00 pour ne pas dépendre du pas exact ni des
    changements d'heure.
    """
    if not points:
        return False
    dernier = max(t for t, _kw in points)
    return (dernier.hour, dernier.minute) >= (23, 0)


def cumul_kwh(points):
    """Énergie d'une liste de pas de 30 min : somme des kW × 0,5 h."""
    return sum(kw for _t, kw in points) * 0.5


def journee_entiere(points):
    """La journée est-elle couverte du début à la fin ?

    ``journee_complete`` ne regarde que la fin — suffisant pour demain, qui
    commence toujours à minuit. Pour la journée en cours, c'est le **début**
    qui manque tant que l'historique n'a pas recollé la matinée : un cumul
    calculé sur une demi-journée serait faux sans en avoir l'air.
    """
    if not points:
        return False
    premier = min(t for t, _kw in points)
    return (premier.hour, premier.minute) <= (0, 30) and journee_complete(points)


def get_summary(force=False):
    """{ok, today_kwh, tomorrow_kwh, tomorrow_partiel, window, window_tomorrow,
    periods}.

    Les cumuls sont calculés depuis ``periods``, et non depuis le champ
    ``daily`` du cache. La différence est de taille : ``daily`` ne contient
    que ce que l'appel API a rapporté, c'est-à-dire **de l'heure de l'appel
    à la fin de la journée**. Un rafraîchissement à 15h faisait donc afficher
    « prévu aujourd'hui : 11,6 kWh » pour une journée à 30 kWh, alors que la
    courbe, elle, montrait la journée entière — elle est complétée par
    l'historique des prévisions déjà vues (``_augment_periods_with_history``).
    En repartant de ``periods``, le cumul et la courbe racontent enfin la
    même chose.

    ``tomorrow_kwh`` vaut ``None`` quand la journée de demain n'est pas
    couverte en entier par le cache : mieux vaut ne rien afficher qu'un
    chiffre faux — voir ``journee_complete``.
    """
    fc = get_forecast(force=force)
    today = date.today()
    tomorrow = today + timedelta(days=1)
    now = datetime.now().astimezone()

    n = window_minutes(today) // 30
    today_pts = [
        (p["time"], p["pv_kw"]) for p in fc["periods"] if p["time"].date() == today
    ]
    future_today = [(t, kw) for t, kw in today_pts if t >= now]
    tomorrow_pts = [
        (p["time"], p["pv_kw"]) for p in fc["periods"] if p["time"].date() == tomorrow
    ]
    demain_complet = journee_complete(tomorrow_pts)

    return {
        "ok": True,
        "today_kwh": cumul_kwh(today_pts) if journee_entiere(today_pts) else None,
        "tomorrow_kwh": cumul_kwh(tomorrow_pts) if demain_complet else None,
        "tomorrow_partiel": bool(tomorrow_pts) and not demain_complet,
        "window": _best_window(future_today, n),
        "window_tomorrow": (
            _best_window(tomorrow_pts, window_minutes(tomorrow) // 30)
            if demain_complet
            else None
        ),
        "periods": fc["periods"],
    }


def tache_actualiser():
    """Rafraîchit les prévisions et publie les variables (heures fixes).

    Seul point d'entrée autorisé à appeler l'API (avec le bouton
    « Actualiser » de l'onglet). Coût : 1 appel par site, une seule fois,
    car le TTL de 4 h est plus court que l'écart entre deux passages.

    L'estimation du réel (``get_estimated_actuals``, 1 appel par site) n'est
    volontairement PAS rafraîchie ici : la courbe « réel » vient d'Enphase,
    et ces 2 appels ne rentrent pas dans le quota des 10. Pour la réactiver
    faute d'Enphase, ajouter une tâche dédiée dans ``conf.py`` et retirer
    une heure de ``tache_previsions_heures``.
    """
    if not configured():
        return
    with appels_autorises():
        try:
            summary = get_summary(force=True)
        except Exception:
            return  # déjà journalisé par get_forecast
    if summary.get("today_kwh") is not None:
        set_variable("solcast_prevu_aujourdhui_kwh", f"{summary['today_kwh']:.1f}")
    # Prévision de demain incomplète (horizon 48 h) : on vide la variable au
    # lieu d'y laisser la valeur de la veille. Une variable vide se voit ;
    # une valeur périmée passe pour une valeur du jour.
    if summary.get("tomorrow_kwh") is not None:
        set_variable("solcast_prevu_demain_kwh", f"{summary['tomorrow_kwh']:.1f}")
    else:
        set_variable("solcast_prevu_demain_kwh", "")
