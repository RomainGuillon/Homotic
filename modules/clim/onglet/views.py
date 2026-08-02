"""Onglet Climatisation : pilotage des unités Hi-Kumo (présentation v1)."""

from django.contrib import messages
from django.shortcuts import redirect, render

from core.services import get_setting, journal, set_setting

from ..fonctions import api


def _save_params(request):
    user = request.POST.get("username", "").strip()
    pwd = request.POST.get("password", "").strip()
    set_setting("username", user, module=api.MODULE)
    if pwd:
        set_setting("password", pwd, module=api.MODULE, secret=True)

    raw = request.POST.get("tache_actualiser_minutes", "").strip()
    try:
        set_setting("tache_actualiser_minutes", str(max(0, int(raw))), module=api.MODULE)
    except ValueError:
        pass

    journal("Paramètres mis à jour", module=api.MODULE)
    messages.success(request, "Paramètres climatisation enregistrés.")


def onglet(request):
    if request.method == "POST":
        action = request.POST.get("action", "")
        try:
            if action == "params":
                _save_params(request)
            elif action == "refresh":
                api.get_units_cached(force=True)
                messages.success(request, "Climatisations actualisées.")
            elif action == "power":
                room = request.POST.get("room", "")
                on = request.POST.get("on") == "1"
                api.set_unit(room, power="on" if on else "off")
                messages.success(request, f"Climatisation {'allumée' if on else 'éteinte'}.")
            elif action == "set":
                room = request.POST.get("room", "")
                field = request.POST.get("field", "")
                value = request.POST.get("value", "")
                if field == "temperature":
                    api.set_unit(room, temperature=int(value))
                elif field == "mode" and value in dict(api.MODE_OPTIONS):
                    api.set_unit(room, mode=value)
                elif field == "fan_mode" and value in api.FAN_OPTIONS:
                    api.set_unit(room, fan_mode=value)
                elif field == "swing" and value in api.SWING_OPTIONS:
                    api.set_unit(room, swing=value)
                messages.success(request, "Réglage envoyé.")
        except Exception as exc:
            messages.error(request, f"Échec : {exc}")
        return redirect("core:module_tab", name="clim")

    configured = api.configured()
    units, ts, erreur = ([], None, "")
    if configured:
        units, ts, erreur = api.get_units_cached()
        units = [
            {**u, "mode_fr": api.MODES_FR.get(u.get("mode"), u.get("mode"))}
            for u in (units or [])
        ]

    return render(
        request,
        "clim/onglet.html",
        {
            "active_tab": "module:clim",
            "configured": configured,
            "units": units,
            "ts": ts,
            "erreur": erreur,
            "temperatures": range(api.TEMP_MIN, api.TEMP_MAX + 1),
            "modes": api.MODE_OPTIONS,
            "fans": api.FAN_OPTIONS,
            "swings": api.SWING_OPTIONS,
            "params": {
                "username": get_setting("username", module=api.MODULE, default=""),
                "has_password": bool(get_setting("password", module=api.MODULE, default="")),
                "tache_minutes": get_setting("tache_actualiser_minutes", module=api.MODULE, default="15"),
            },
        },
    )
