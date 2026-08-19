# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

"""Onglet Capteurs (Tuya) : jauges + prises connectées (présentation v1)."""

from django.contrib import messages
from django.shortcuts import redirect, render

from core.services import get_setting, journal, set_setting

from ..fonctions import affichage, api, local


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


def _save_params_local(request):
    """Réglages du mode local, et import éventuel du devices.json.

    L'import est traité en premier : si le fichier collé est invalide, on
    refuse de basculer en local — sans quoi l'onglet se retrouverait dans
    un mode qui ne peut pas fonctionner.
    """
    contenu = request.POST.get("devices_json", "").strip()
    adresse = request.POST.get("local_gateway_ip", "").strip()
    if adresse:
        set_setting("local_gateway_ip", adresse, module=api.MODULE)

    if contenu:
        passerelle, appareils = local.configurer(contenu, ip=adresse)
        capteurs = sum(1 for a in appareils if a["genre"] == "sensor")
        prises = len(appareils) - capteurs
        journal(
            f"Configuration locale importée : passerelle « {passerelle['name'] } », "
            f"{capteurs} capteur(s) et {prises} prise(s).",
            module=api.MODULE,
        )
        messages.success(
            request,
            f"Configuration locale importée : {capteurs} capteur(s), {prises} prise(s).",
        )

    version = request.POST.get("local_version", "").strip()
    if version:
        set_setting("local_version", version, module=api.MODULE)

    mode = "local" if request.POST.get("mode") == "local" else "cloud"
    if mode == "local" and not local.configured():
        messages.warning(
            request,
            "Mode local non activé : il manque la configuration de la passerelle.",
        )
        mode = "cloud"
    set_setting("mode", mode, module=api.MODULE)
    set_setting("repli_cloud", "1" if request.POST.get("repli_cloud") else "0", module=api.MODULE)

    if not contenu:
        messages.success(request, f"Mode de lecture : {mode}.")


def onglet(request):
    if request.method == "POST":
        action = request.POST.get("action", "")
        try:
            if action == "params":
                _save_params(request)
            elif action == "params_local":
                _save_params_local(request)
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

    appareils_locaux = local.appareils()

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
                "mode": api.mode(),
                "repli_cloud": api.repli_cloud_actif(),
                "local_ok": local.configured(),
                "local_ip": get_setting("local_gateway_ip", module=api.MODULE, default=""),
                "local_version": local.version(),
                "local_capteurs": sum(1 for a in appareils_locaux if a.get("genre") == "sensor"),
                "local_prises": sum(1 for a in appareils_locaux if a.get("genre") != "sensor"),
            },
        },
    )
