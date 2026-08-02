"""Onglet Solaire (Solcast) : prévisions du jour et de demain (présentation v1)."""

from datetime import date, timedelta

from django.contrib import messages
from django.shortcuts import redirect, render

from core.services import get_setting, journal, set_setting

from ..fonctions import affichage, api


def _save_params(request):
    key = request.POST.get("api_key", "").strip()
    if key:
        set_setting("api_key", key, module=api.MODULE, secret=True)
    set_setting("resource_ids", request.POST.get("resource_ids", "").strip(), module=api.MODULE)
    set_setting("site_labels", request.POST.get("site_labels", "").strip(), module=api.MODULE)

    # Horaires des appels API : une ligne « HH:MM » par rafraîchissement
    # (aucune ligne = tâche désactivée). Le tri et les doublons sont gérés
    # par parse_horaires.
    from core.scheduler import horaires_texte, parse_horaires

    horaires = parse_horaires(request.POST.getlist("horaire"))
    set_setting("tache_previsions_heures", horaires_texte(horaires), module=api.MODULE)

    nb_sites = max(1, len(api.resource_ids()))
    cout = len(horaires) * nb_sites
    if cout > api.quota_jour():
        messages.warning(
            request,
            f"{len(horaires)} rafraîchissements × {nb_sites} site(s) = {cout} appels/jour, "
            f"au-dessus du quota de {api.quota_jour()} : les derniers passages de la "
            f"journée seront refusés.",
        )

    journal("Paramètres mis à jour", module=api.MODULE)
    messages.success(request, "Paramètres Solcast enregistrés.")


def _real_production_points():
    """Production réelle du jour : [(datetime, kW)] et « est-ce du mesuré ? ».

    Deux sources :

    1. le besoin ``production_reelle`` — une vraie mesure, quel que soit le
       module qui la fournit (comment il l'obtient ne regarde que lui) ;
    2. à défaut, l'estimation Solcast, qui coûte un appel par site et n'est
       rafraîchie qu'une fois par jour (la courbe s'arrête alors à l'heure
       de cet unique appel, typiquement le matin).

    Le booléen retourné dit laquelle des deux a servi : la page n'affiche
    pas « réel » devant une estimation.
    """
    from core.liaisons import lire_besoin

    points, _err = lire_besoin(api.MODULE, "production_reelle")
    if points:
        return points, True

    return [(p["time"], p["pv_kw"]) for p in api.get_estimated_actuals()], False


def _creneau_chauffe():
    """Zone de chauffe surlignée sur les courbes du jour.

    Elle est construite à partir de **l'heure effectivement retenue** et de
    la durée de chauffe, pas du créneau qu'avait trouvé le calcul : si
    l'heure est forcée à la main, le surlignage doit se déplacer avec elle,
    sinon la page continue d'afficher l'ancien créneau.

    La donnée vient du besoin ``creneau_chauffe``. Retourne
    ({start, end} ou None, libellé).
    """
    from datetime import datetime as _dt

    from core.liaisons import lire_besoin

    creneau, _err = lire_besoin(api.MODULE, "creneau_chauffe")
    if not creneau:
        return None, ""

    # Calcul d'un autre jour : l'heure retenue ne veut plus rien dire, on ne
    # surligne rien. Sauf heure forcée à la main, qui est une décision pour
    # aujourd'hui et non le reliquat d'un calcul de la veille.
    if creneau.get("perime") and not creneau.get("forcee"):
        return None, ""

    heure = str(creneau.get("heure") or "").strip()
    # Chauffe de nuit : rien à surligner sur la courbe du jour — sauf si
    # l'heure a été forcée à la main, auquel cas c'est elle qui décide.
    if not heure or (creneau.get("mode") != "solaire" and not creneau.get("forcee")):
        return None, ""

    try:
        h, m = (int(x) for x in heure.split(":"))
        duree = int(creneau.get("duree_min") or 60)
    except (ValueError, TypeError):
        return None, ""

    aujourdhui = date.today()
    debut = _dt(aujourdhui.year, aujourdhui.month, aujourdhui.day, h, m).astimezone()
    libelle = f"chauffe-eau {duree} min"
    if creneau.get("forcee"):
        libelle += " — heure forcée"
    return {"start": debut, "end": debut + timedelta(minutes=duree)}, libelle


def build_solar_context():
    """Contexte commun onglet / bloc dashboard (résumé + courbes + tableau)."""
    today = date.today()
    try:
        summary = api.get_summary()
        erreur = ""
    except Exception as exc:
        # L'origine est affichée même quand les prévisions sont indisponibles
        return {
            "erreur": str(exc),
            "source": api.source_donnees(),
            "quota": api.etat_quota(),
        }

    actuals, mesure_reelle = _real_production_points()
    forecast = [(p["time"], p["pv_kw"]) for p in summary["periods"]]
    creneau, creneau_label = _creneau_chauffe()

    # Détail par pan de toiture (Sud-Est, Nord-Ouest...)
    sites = []
    demain = today + timedelta(days=1)
    for label, data in (api.get_sites() or {}).items():
        points = [(p["time"], p["pv_kw"]) for p in data.get("periods", [])]
        # Cumuls calculés depuis les pas, comme le cumul global : le champ
        # « daily » du cache s'arrête à l'heure du dernier appel API et
        # sous-estime la journée en cours. Et rien n'est affiché tant que la
        # journée n'est pas couverte de bout en bout.
        pts_jour = [p for p in points if p[0].date() == today]
        pts_demain = [p for p in points if p[0].date() == demain]
        sites.append({
            "label": label,
            "chart": affichage.day_chart(today, points, zone=creneau,
                                         zone_label=creneau_label),
            "kwh_today": api.cumul_kwh(pts_jour) if api.journee_entiere(pts_jour) else None,
            # Même règle que le cumul global : tant que demain n'est pas
            # couvert en entier, le chiffre par pan de toiture est faux aussi.
            "kwh_tomorrow": (
                None
                if summary.get("tomorrow_partiel")
                else api.cumul_kwh(pts_demain)
            ),
        })

    return {
        "erreur": erreur,
        "source": api.source_donnees(),
        "quota": api.etat_quota(),
        "s": summary,
        "creneau_chauffe": creneau,
        "creneau_label": creneau_label,
        "chart_today": affichage.day_chart(
            today, forecast, zone=creneau, actual_points=actuals,
            zone_label=creneau_label,
        ),
        # Courbe de demain masquée tant que la journée est tronquée : une
        # production qui s'arrête net à 17h se lit comme une chute de
        # production, pas comme une absence de données.
        "chart_tomorrow": (
            None
            if summary.get("tomorrow_partiel")
            else affichage.day_chart(today + timedelta(days=1), forecast)
        ),
        "sites": sites,
        "hourly": affichage.hourly_rows(today, forecast, actuals),
        "has_actual": bool(actuals),
        "mesure_reelle": mesure_reelle,
    }


def onglet(request):
    if request.method == "POST":
        action = request.POST.get("action", "")
        try:
            if action == "params":
                _save_params(request)
            elif action == "refresh":
                avant = api.appels_du_jour()
                api.tache_actualiser()
                etat = api.etat_quota()
                consommes = etat["appels"] - avant
                if consommes:
                    messages.success(
                        request,
                        f"Prévisions actualisées ({consommes} appel(s) consommé(s), "
                        f"total {etat['appels']}/{etat['quota']} aujourd'hui).",
                    )
                elif etat["suspendu_jusqua"]:
                    messages.warning(
                        request,
                        f"Aucun appel : suspendus jusqu'à "
                        f"{etat['suspendu_jusqua']:%d/%m %H:%M} ({etat['raison']}). "
                        f"Affichage du dernier cache.",
                    )
                else:
                    messages.info(
                        request,
                        f"Aucun appel nécessaire : prévisions encore fraîches "
                        f"({etat['appels']}/{etat['quota']} appels aujourd'hui).",
                    )
        except Exception as exc:
            messages.error(request, f"Échec : {exc}")
        return redirect("core:module_tab", name="solcast")

    configured = api.configured()
    context = {
        "active_tab": "module:solcast",
        "configured": configured,
        "params": {
            "has_key": bool(api.api_key()),
            "resource_ids": get_setting("resource_ids", module=api.MODULE, default=""),
            "site_labels": get_setting("site_labels", module=api.MODULE, default=""),
            "horaires": [f"{h:02d}:{m:02d}" for h, m in api.horaires_planifies()],
            "nb_sites": max(1, len(api.resource_ids())),
        },
    }
    if configured:
        context.update(build_solar_context())

    return render(request, "solcast/onglet.html", context)
