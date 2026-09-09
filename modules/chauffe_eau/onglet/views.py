# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

"""Onglet Chauffe-eau : jauge + état/réglages + paramétrage (présentation v1)."""

from datetime import datetime, timedelta

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

    # Valeur de « setAbsenceMode » qui déclenche l'absence : vide = on suit
    # ce que l'appareil déclare (voir api.mode_absence_actif).
    mode = request.POST.get("mode_absence", "").strip().lower()
    if mode in ("", "prog", "on"):
        set_setting("mode_absence", mode, module=api.MODULE)

    journal("Paramètres mis à jour", module=api.MODULE)
    messages.success(request, "Paramètres chauffe-eau enregistrés.")


def _contexte_absence(data):
    """État de l'absence + valeurs pré-remplies du formulaire.

    L'état lui-même vient de ``api.etat_absence``, qui croise le mode et
    les dates — aucun des deux ne suffisant seul. Ici on n'ajoute que les
    valeurs pré-remplies du formulaire : reproposer la dernière période
    encore à venir est commode, même si elle a été annulée.
    """
    maintenant = datetime.now().replace(second=0, microsecond=0)
    etat = api.etat_absence(data)
    debut, fin = etat["debut"], etat["fin"]
    return {
        "absence_on": etat["en_cours"],
        "absence_mode": etat["mode"],
        "absence_debut": debut,
        "absence_fin": fin,
        "absence_a_venir": etat["a_venir"],
        # Y a-t-il quelque chose à annuler ? (en cours, ou programmé)
        "absence_annulable": etat["retenue"],
        "form_depart": ((debut if fin and fin > maintenant else None) or maintenant).strftime("%Y-%m-%dT%H:%M"),
        "form_retour": ((fin if fin and fin > maintenant else None) or maintenant + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M"),
    }


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
            elif action == "absence":
                resume = api.set_absence(
                    request.POST.get("depart", ""), request.POST.get("retour", "")
                )
                messages.success(request, f"Absence programmée : {resume}.")
            elif action == "absence_off":
                api.arreter_absence()
                messages.success(request, "Absence annulée.")
            elif action == "capacites":
                caps = api.capacites(force=True)
                messages.success(
                    request,
                    f"{len(caps.get('commandes') or [])} commandes relevées ; "
                    f"modes d'absence : "
                    f"{', '.join(caps.get('modes_absence') or []) or 'non déclarés'}.",
                )
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
            "mode_absence": get_setting("mode_absence", module=api.MODULE, default=""),
            "capacites": get_setting("capacites", module=api.MODULE, default=""),
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
        context.update(_contexte_absence(data))

    # Suivi des chauffes : ne bloque jamais l'onglet si les tables du module
    # ne sont pas encore migrées.
    try:
        from ..fonctions import suivi

        context["suivi"] = suivi.resume()
    except Exception as exc:
        context["suivi_erreur"] = str(exc)

    return render(request, "chauffe_eau/onglet.html", context)
