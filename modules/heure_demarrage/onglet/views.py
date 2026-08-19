# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

"""Onglet Heure de démarrage : réglages + résultat du calcul."""

from django.contrib import messages
from django.shortcuts import redirect, render

from core.models import Control
from core.services import journal, set_setting

from ..fonctions import api, calcul


def _creer_switchs_saison():
    """Crée les switchs exclusifs « Été » et « Hiver » s'ils n'existent pas."""
    crees = []
    for name, label in (("ete", "Été"), ("hiver", "Hiver")):
        if not Control.objects.filter(name=name).exists():
            Control.objects.create(
                type=Control.SWITCH, name=name, label=label, group="saison"
            )
            crees.append(label)
    if crees:
        journal(
            "Switchs de saison créés (groupe exclusif « saison ») : " + ", ".join(crees),
            module=api.MODULE,
        )
    return crees


def _save_params(request):
    for key, _defaut, kind in api.REGLAGES:
        if kind == "bool":
            set_setting(key, "oui" if request.POST.get(key) else "non", module=api.MODULE)
            continue
        raw = request.POST.get(key, "").strip()
        if kind == "int":
            try:
                set_setting(key, str(max(1, int(float(raw.replace(",", "."))))), module=api.MODULE)
            except ValueError:
                pass
        elif kind == "float":
            try:
                set_setting(key, f"{max(0.0, float(raw.replace(',', '.'))):.2f}", module=api.MODULE)
            except ValueError:
                pass
        elif kind == "heure":
            parts = raw.split(":")
            if len(parts) == 2 and all(p.isdigit() for p in parts):
                set_setting(key, f"{int(parts[0]) % 24:02d}:{int(parts[1]) % 60:02d}", module=api.MODULE)
        elif kind == "choix":
            valeurs = [v for v, _libelle in api.CHOIX.get(key, [])]
            if raw in valeurs:
                set_setting(key, raw, module=api.MODULE)

    api.publier_variables()
    journal("Réglages mis à jour", module=api.MODULE)
    messages.success(
        request, "Réglages enregistrés et publiés comme variables globales."
    )


def onglet(request):
    crees = _creer_switchs_saison()

    if request.method == "POST":
        action = request.POST.get("action", "")
        try:
            if action == "params":
                _save_params(request)
            elif action == "recalculer":
                # Switch coché = arbitrage « nuit », décoché = « jour ».
                arbitrage = "nuit" if request.POST.get("arbitrage") else "jour"
                resultat = api.tache_actualiser(arbitrage=arbitrage)
                comment = ("comparé aux heures creuses" if arbitrage == "nuit"
                           else "solaire seul, sans les heures creuses")
                if resultat.get("heure"):
                    messages.success(
                        request,
                        f"Recalculé ({comment}) : démarrage conseillé à "
                        f"{resultat['heure']} ({resultat.get('mode')}).",
                    )
                else:
                    messages.warning(
                        request,
                        f"Recalculé ({comment}) : aucun créneau exploitable — "
                        f"{resultat.get('erreur') or 'plus de prévision pour la journée'}. "
                        f"L'heure précédente est conservée.",
                    )
        except Exception as exc:
            messages.error(request, f"Échec : {exc}")
        return redirect("core:module_tab", name="heure_demarrage")

    if crees:
        messages.info(
            request,
            "Switchs « Été » et « Hiver » créés (groupe exclusif) — visibles "
            "dans le bloc Scénarios du tableau de bord.",
        )

    # Affichage du dernier calcul mémorisé (le bouton Recalculer est le seul
    # moyen de le rafraîchir depuis cet onglet).
    resultat = calcul.dernier_resultat()

    return render(
        request,
        "heure_demarrage/onglet.html",
        {
            "active_tab": "module:heure_demarrage",
            "r": resultat,
            "params": {
                "temp_chauffe_ete": api.temp_chauffe_ete(),
                "temp_chauffe_hiver": api.temp_chauffe_hiver(),
                "optimiser": api.optimiser(),
                "conso_min_maison": f"{api.conso_min_maison():.2f}",
                "conso_chauffe_eau": f"{api.conso_chauffe_eau():.2f}",
                "heure_nuit": api.heure_nuit(),
                "ajustement": api.ajustement(),
            },
            "choix_ajustement": api.CHOIX["ajustement"],
            # Le switch reprend l'arbitrage du dernier calcul : on retrouve
            # l'état dans lequel on a laissé les choses.
            "arbitrage_defaut": resultat.get("arbitrage") or "nuit",
            "switchs_saison": Control.objects.filter(
                type=Control.SWITCH, group="saison"
            ),
        },
    )
