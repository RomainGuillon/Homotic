"""Onglet Tempo : affichage des couleurs + paramétrage (API RTE, tarifs)."""

from datetime import date, datetime, timedelta

from django.contrib import messages
from django.shortcuts import redirect, render

from core.services import get_setting, journal, set_setting

from ..fonctions import affichage, api

# Champs tarifs du formulaire : (clé base, libellé)
PRICE_FIELDS = [
    (f"prix_{color.lower()}_{per.lower()}", f"{api.COLORS[color]['name']} {per}")
    for color in ("BLUE", "WHITE", "RED")
    for per in ("HP", "HC")
]


def _save_params(request):
    cid = request.POST.get("client_id", "").strip()
    secret = request.POST.get("client_secret", "").strip()
    set_setting("client_id", cid, module=api.MODULE)
    if secret:  # champ vide = on conserve le secret existant
        set_setting("client_secret", secret, module=api.MODULE, secret=True)

    for key, _label in PRICE_FIELDS:
        raw = request.POST.get(key, "").strip().replace(",", ".")
        try:
            set_setting(key, f"{float(raw):.4f}", module=api.MODULE)
        except ValueError:
            pass  # champ vide/invalide : on garde la valeur en place

    for key in ("hc_debut", "hc_fin"):
        raw = request.POST.get(key, "").strip()
        try:
            set_setting(key, str(int(raw) % 24), module=api.MODULE)
        except ValueError:
            pass

    raw = request.POST.get("tache_actualiser_minutes", "").strip()
    try:
        set_setting("tache_actualiser_minutes", str(max(0, int(raw))), module=api.MODULE)
    except ValueError:
        pass

    for key in ("abonnement_mensuel", "prix_revente"):
        raw = request.POST.get(key, "").strip().replace(",", ".")
        try:
            set_setting(key, f"{float(raw):.4f}", module=api.MODULE)
        except ValueError:
            pass

    journal("Paramètres mis à jour", module=api.MODULE)
    messages.success(request, "Paramètres Tempo enregistrés.")


def onglet(request):
    if request.method == "POST":
        action = request.POST.get("action", "")
        if action == "params":
            _save_params(request)
        elif action == "refresh":
            api.get_colors_cached(force=True)
            api.get_season_cached(force=True)
            messages.success(request, "Données Tempo actualisées.")
        return redirect("core:module_tab", name="tempo")

    configured = api.configured()
    colors, colors_ts, colors_err = ({}, None, "")
    season, season_err = (None, "")
    if configured:
        colors, colors_ts, colors_err = api.get_colors_cached()
        season, _ts, season_err = api.get_season_cached()

    today = date.today()
    now = datetime.now()
    hc_debut, hc_fin = api.hc_bounds()
    current_color = api.color_of_moment(colors, now)
    current_price = api.get_prices().get(current_color, {}).get(api.current_period(now))

    defaults_flat = {
        f"prix_{c.lower()}_{p.lower()}": api.DEFAULT_PRICES[c][p]
        for c in ("BLUE", "WHITE", "RED")
        for p in ("HP", "HC")
    }
    params = {
        "client_id": get_setting("client_id", module=api.MODULE, default=""),
        "has_secret": bool(get_setting("client_secret", module=api.MODULE, default="")),
        "hc_debut": hc_debut,
        "hc_fin": hc_fin,
        "tache_minutes": api._int("tache_actualiser_minutes", 30),
        "abonnement_mensuel": f"{api.abonnement_mensuel():.2f}",
        "prix_revente": f"{api.prix_revente():.4f}",
        "prices": [
            (key, label, f"{api._float(key, defaults_flat[key]):.4f}")
            for key, label in PRICE_FIELDS
        ],
    }

    demain = today + timedelta(days=1)
    return render(
        request,
        "tempo/onglet.html",
        {
            "active_tab": "module:tempo",
            "configured": configured,
            "aujourdhui": {"date": today, "frise": affichage.frise(colors, today, with_marker=True)},
            "demain": {"date": demain, "frise": affichage.frise(colors, demain)},
            "colors_ts": colors_ts,
            "erreur": colors_err or season_err,
            "season": season,
            "current_period": api.current_period(now),
            "current_color_name": api.COLORS.get(current_color, api.COLORS[None])["name"],
            "current_price": current_price,
            "params": params,
        },
    )
