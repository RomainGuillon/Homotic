"""Onglet Capteurs (Tuya) : jauges + prises connectées (présentation v1)."""

from django.contrib import messages
from django.shortcuts import redirect, render

from core.services import get_setting, journal, set_setting

from ..fonctions import affichage, api


def _save_params(request):
    cid = request.POST.get("access_id", "").strip()
    secret = request.POST.get("access_secret", "").strip()
    set_setting("access_id", cid, module=api.MODULE)
    if secret:  # champ vide = on conserve le secret existant
        set_setting("access_secret", secret, module=api.MODULE, secret=True)

    region = request.POST.get("region", "eu").strip().lower()
    if region in ("eu", "us", "cn", "in"):
        set_setting("region", region, module=api.MODULE)

    raw = request.POST.get("tache_actualiser_minutes", "").strip()
    try:
        set_setting("tache_actualiser_minutes", str(max(0, int(raw))), module=api.MODULE)
    except ValueError:
        pass

    journal("Paramètres mis à jour", module=api.MODULE)
    messages.success(request, "Paramètres Tuya enregistrés.")


def onglet(request):
    if request.method == "POST":
        action = request.POST.get("action", "")
        try:
            if action == "params":
                _save_params(request)
            elif action == "refresh":
                # Une seule collecte pour capteurs + prises. « complet »
                # recharge aussi la liste des appareils et les timers :
                # c'est un geste manuel et rare, donc on l'assume ici.
                erreur = api.rafraichir(complet=True)
                if erreur:
                    messages.error(request, f"Échec : {erreur}")
                else:
                    messages.success(request, "Capteurs et prises actualisés.")
            elif action == "plug":
                on = request.POST.get("on") == "1"
                api.set_plug(
                    request.POST.get("device_id", ""),
                    request.POST.get("switch_code", ""),
                    on,
                    name=request.POST.get("name", ""),
                )
                messages.success(
                    request,
                    f"Prise « {request.POST.get('name', '')} » "
                    f"{'allumée' if on else 'éteinte'}.",
                )
        except Exception as exc:
            messages.error(request, f"Échec : {exc}")
        return redirect("core:module_tab", name="tuya")

    configured = api.configured()
    sensors, plugs = [], []
    ts, erreur = None, ""
    if configured:
        sensors, ts, err1 = api.get_sensors_cached()
        plugs, _ts2, err2 = api.get_plugs_cached()
        erreur = err1 or err2

    sensor_cards = [
        {**s, "svg": affichage.sensor_gauges(s.get("temperature"), s.get("humidity"), key=s["id"])}
        for s in (sensors or [])
    ]
    plug_cards = [
        {
            **p,
            "svg": affichage.plug_svg(p.get("state")),
            "schedule_lines": affichage.schedule_lines(p.get("schedule_slots")),
        }
        for p in (plugs or [])
    ]

    return render(
        request,
        "tuya/onglet.html",
        {
            "active_tab": "module:tuya",
            "configured": configured,
            "sensors": sensor_cards,
            "plugs": plug_cards,
            "ts": ts,
            "erreur": erreur,
            "params": {
                "access_id": get_setting("access_id", module=api.MODULE, default=""),
                "has_secret": bool(get_setting("access_secret", module=api.MODULE, default="")),
                "region": get_setting("region", module=api.MODULE, default="eu"),
                "tache_minutes": get_setting("tache_actualiser_minutes", module=api.MODULE, default="10"),
            },
        },
    )
