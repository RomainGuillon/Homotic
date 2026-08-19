# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

"""Onglet Énergie (Enphase Envoy + cloud Enlighten) : présentation v1."""

from datetime import date, datetime

from django.contrib import messages
from django.shortcuts import redirect, render

from core.services import get_setting, journal, set_setting

from ..fonctions import affichage, api, cloud, journee


def _save_params(request):
    for key in ("username", "envoy_serial", "envoy_host"):
        set_setting(key, request.POST.get(key, "").strip(), module=api.MODULE)
    pwd = request.POST.get("password", "").strip()
    if pwd:
        set_setting("password", pwd, module=api.MODULE, secret=True)

    raw = request.POST.get("tache_actualiser_minutes", "").strip()
    try:
        set_setting("tache_actualiser_minutes", str(max(0, int(raw))), module=api.MODULE)
    except ValueError:
        pass

    journal("Paramètres mis à jour", module=api.MODULE)
    messages.success(request, "Paramètres Enphase enregistrés.")


def _display_context(data, compact=False):
    """Pourcentages des barres + valeurs dérivées (repris de la v1)."""
    max_w = max(data["production_w"], data["consumption_w"],
                data["grid_import_w"], data["grid_export_w"], 1.0)
    importing = data["grid_import_w"] >= data["grid_export_w"]
    autonomy = 0
    if data["consumption_w"] > 0:
        autonomy = round(min(data["solar_to_house_w"] / data["consumption_w"], 1.0) * 100)

    # Autonomie de la journée : part de la consommation couverte par le
    # solaire depuis minuit. Ce qui n'a pas été pris au réseau a forcément
    # été produit sur place — inutile de mesurer l'autoconsommation à part.
    # None (et pas 0) tant qu'il n'y a rien à diviser : à 00h05 « 0 % »
    # ferait croire à une maison entièrement sur le réseau.
    autonomy_day = None
    conso_jour = data.get("consumption_wh_today") or 0.0
    if conso_jour > 0:
        autoconso = max(conso_jour - (data.get("import_wh_today") or 0.0), 0.0)
        autonomy_day = round(min(autoconso / conso_jour, 1.0) * 100)

    return {
        "flow": affichage.energy_flow_chart(data, compact=compact),
        "production_pct": round(data["production_w"] / max_w * 100),
        "consumption_pct": round(data["consumption_w"] / max_w * 100),
        "grid_pct": round(max(data["grid_import_w"], data["grid_export_w"]) / max_w * 100),
        "importing": importing,
        "autonomy_pct": autonomy,
        "autonomy_day_pct": autonomy_day,
        "autonomy_day_kwh": (conso_jour - (data.get("import_wh_today") or 0.0)) / 1000.0,
        "consumption_kwh_today": conso_jour / 1000.0,
    }


def onglet(request):
    if request.method == "POST":
        action = request.POST.get("action", "")
        try:
            if action == "params":
                _save_params(request)
            elif action == "refresh":
                api.get_energy_cached(force=True)
                if cloud.cloud_configured():
                    cloud.get_daily_totals_cached(force=True)
                    cloud.get_production_curve_cached(force=True)
                    cloud.get_consumption_curve_cached(force=True)
                messages.success(request, "Mesures actualisées.")
            elif action == "cloud_params":
                for key in ("cloud_api_key", "cloud_client_id", "cloud_system_id"):
                    set_setting(key, request.POST.get(key, "").strip(), module=api.MODULE)
                secret = request.POST.get("cloud_client_secret", "").strip()
                if secret:
                    set_setting("cloud_client_secret", secret, module=api.MODULE, secret=True)
                journal("Paramètres cloud mis à jour", module=api.MODULE)
                messages.success(request, "Paramètres cloud enregistrés.")
            elif action == "cloud_link":
                code = request.POST.get("code", "").strip()
                if not code:
                    messages.error(request, "Coller le code d'autorisation.")
                else:
                    cloud.exchange_code(code)
                    if not cloud.cloud_config()["system_id"]:
                        systems = cloud.list_systems().get("systems", [])
                        if systems:
                            sid = str(systems[0].get("system_id", ""))
                            set_setting("cloud_system_id", sid, module=api.MODULE)
                            messages.success(request, f"Compte lié — système {sid} détecté.")
                        else:
                            messages.warning(request, "Compte lié, mais aucun système trouvé.")
                    else:
                        messages.success(request, "Compte Enphase cloud lié.")
        except Exception as exc:
            messages.error(request, f"Échec : {exc}")
        return redirect("core:module_tab", name="enphase")

    configured = api.configured()
    data, ts, erreur = (None, None, "")
    if configured:
        data, ts, erreur = api.get_energy_cached()

    cfg = api.config()
    ccfg = cloud.cloud_config()
    try:
        auth_url = cloud.authorize_url() if (ccfg["client_id"] and ccfg["client_secret"] and ccfg["api_key"]) else ""
    except Exception:
        auth_url = ""

    context = {
        "active_tab": "module:enphase",
        "configured": configured,
        "e": data,
        "ts": ts,
        "erreur": erreur,
        "params": {
            "username": cfg["username"],
            "has_password": bool(cfg["password"]),
            "envoy_serial": cfg["serial"],
            "envoy_host": cfg["host"],
            "tache_minutes": get_setting("tache_actualiser_minutes", module=api.MODULE, default="5"),
        },
        "cloud_params": {
            "api_key": ccfg["api_key"],
            "client_id": ccfg["client_id"],
            "has_secret": bool(ccfg["client_secret"]),
            "system_id": ccfg["system_id"],
            "linked": cloud.cloud_configured(),
            "auth_url": auth_url,
        },
    }
    if data:
        context.update(_display_context(data))
        context.update(build_journee_context())
        context.update(build_bilan_context())

    return render(request, "enphase/onglet.html", context)


def build_bilan_context(compact=False):
    """Courbe production / consommation / réseau de la journée.

    Construite depuis l'historique local de l'Envoy : aucun appel réseau,
    aucun quota. ``compact`` allège le rendu pour le tableau de bord.
    """
    from ..fonctions import graphique, historique

    mesures = historique.mesures_du_jour()
    if not mesures:
        return {"bilan": None}

    return {
        "bilan": {
            "chart": graphique.journee_chart(mesures, compact=compact),
            "points": len(mesures),
            "depuis": mesures[0][0],
            "import_max": max((r for _t, _p, _c, r in mesures if r > 0), default=0.0),
            "export_max": max((-r for _t, _p, _c, r in mesures if r < 0), default=0.0),
        }
    }


def build_journee_context():
    """Carte « La journée » + courbes du jour (partagé onglet / dashboard)."""
    snap, _ts, err, cloud_ok = journee.energy_snapshot()
    context = {"journee": None, "cout": None, "courbes": None, "journee_cloud": cloud_ok}
    if snap is None:
        context["journee_erreur"] = err
        return context

    context["journee"] = {
        "production_kwh": snap["production_wh_today"] / 1000.0,
        "consumption_kwh": snap["consumption_wh_today"] / 1000.0,
    }
    try:
        context["cout"] = journee.daily_cost(snap)
    except Exception:
        context["cout"] = None

    if cloud.cloud_configured():
        prod, _p, _e1 = cloud.get_production_curve_cached()
        cons, _c, _e2 = cloud.get_consumption_curve_cached()
        prod_points = [(datetime.fromtimestamp(p["end_at"]), p["kw"]) for p in (prod or [])]
        cons_points = [(datetime.fromtimestamp(p["end_at"]), p["kw"]) for p in (cons or [])]
        if prod_points:
            context["courbes"] = {
                "chart": affichage.production_vs_consumption_chart(
                    date.today(), prod_points, cons_points
                ),
                "prod_total_kwh": sum(kw for _t, kw in prod_points) * 0.25,
                "cons_total_kwh": (sum(kw for _t, kw in cons_points) * 0.25
                                   if cons_points else None),
            }
    return context
