# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

"""Onglet Chauffe-eau : jauge + état/réglages + paramétrage (présentation v1)."""

from django.contrib import messages
from django.shortcuts import redirect, render

from core.services import get_setting, journal, set_setting

from ..fonctions import affichage, api


def _save_params(request):
    user = request.POST.get("username", "").strip()
    pwd = request.POST.get("password", "").strip()
    set_setting("username", user, module=api.MODULE)
    if pwd:  # champ vide = on conserve le mot de passe existant
        set_setting("password", pwd, module=api.MODULE, secret=True)

    raw = request.POST.get("v40_max", "").strip().replace(",", ".")
    try:
        set_setting("v40_max", f"{float(raw):.0f}", module=api.MODULE)
    except ValueError:
        pass

    raw = request.POST.get("tache_actualiser_minutes", "").strip()
    try:
        set_setting("tache_actualiser_minutes", str(max(0, int(raw))), module=api.MODULE)
    except ValueError:
        pass

    for champ in ("douches_chauffe", "douches_veille"):
        raw = request.POST.get(champ, "").strip()
        try:
            set_setting(champ, str(max(1, min(5, int(raw)))), module=api.MODULE)
        except ValueError:
            pass

    journal("Paramètres mis à jour", module=api.MODULE)
    messages.success(request, "Paramètres chauffe-eau enregistrés.")


def onglet(request):
    if request.method == "POST":
        action = request.POST.get("action", "")
        try:
            if action == "params":
                _save_params(request)
            elif action == "refresh":
                api.get_status_cached(force=True)
                messages.success(request, "Chauffe-eau actualisé.")
            elif action == "showers":
                n = api.set_showers(request.POST.get("showers", 1))
                messages.success(request, f"{n} douche(s) demandée(s).")
            elif action == "boost":
                mode = api.set_boost_mode(request.POST.get("mode", "off"))
                messages.success(request, f"Boost : {mode}.")
        except Exception as exc:
            messages.error(request, f"Échec : {exc}")
        return redirect("core:module_tab", name="chauffe_eau")

    configured = api.configured()
    data, ts, erreur = (None, None, "")
    if configured:
        data, ts, erreur = api.get_status_cached()

    context = {
        "active_tab": "module:chauffe_eau",
        "configured": configured,
        "h": data,
        "ts": ts,
        "erreur": erreur,
        "params": {
            "username": get_setting("username", module=api.MODULE, default=""),
            "has_password": bool(get_setting("password", module=api.MODULE, default="")),
            "v40_max": int(api.v40_max()),
            "tache_minutes": get_setting("tache_actualiser_minutes", module=api.MODULE, default="15"),
            "douches_chauffe": api.douches_chauffe(),
            "douches_veille": api.douches_veille(),
        },
    }

    if data:
        min_sh = int(data.get("min_showers") or 1)
        max_sh = int(data.get("max_showers") or 5)
        try:
            default_showers = int(float(data.get("showers_expected") or min_sh))
        except (TypeError, ValueError):
            default_showers = min_sh
        context.update(
            {
                "tank": affichage.tank_svg(data.get("hot_water_pct")),
                "heating_on": api.is_heating(data.get("heating")),
                "boost_on": str(data.get("boost", "")).lower() == "on",
                "shower_range": range(min_sh, max_sh + 1),
                "default_showers": max(min_sh, min(max_sh, default_showers)),
            }
        )

    # Suivi des chauffes : ne bloque jamais l'onglet si les tables du module
    # ne sont pas encore migrées.
    try:
        from ..fonctions import suivi

        context["suivi"] = suivi.resume()
    except Exception as exc:
        context["suivi_erreur"] = str(exc)

    return render(request, "chauffe_eau/onglet.html", context)
