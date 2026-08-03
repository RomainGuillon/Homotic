"""Vues des 3 onglets du socle + gestion des contrôles (boutons/switchs)."""

import importlib

from django.contrib import messages
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from . import liaisons, scheduler
from .models import Control, DashboardBlock, LogEntry, Module, Scenario, Variable
from .modules_registry import scan_modules, trigger_restart
from .services import (
    JOURNAL_JOURS_DEFAUT,
    get_setting,
    journal,
    journal_jours_conserves,
    purger_journal,
    set_control_state,
    set_setting,
    supprimer_journal,
)

# Icônes proposées à la création d'un bouton poussoir (Bootstrap Icons)
BUTTON_ICONS = [
    ("", "Aucune"),
    ("lightbulb", "Ampoule"),
    ("power", "Marche/arrêt"),
    ("droplet", "Goutte d'eau"),
    ("fire", "Flamme"),
    ("thermometer-half", "Thermomètre"),
    ("sun", "Soleil"),
    ("moon", "Lune"),
    ("snow", "Flocon"),
    ("wind", "Vent"),
    ("fan", "Ventilateur"),
    ("plug", "Prise"),
    ("lightning-charge", "Éclair"),
    ("house", "Maison"),
    ("tv", "Télévision"),
    ("alarm", "Réveil"),
    ("arrow-clockwise", "Refresh"),
]


def grouper_controls(controls):
    """Regroupe les contrôles par groupe, dans l'ordre d'affichage.

    Retourne ``[{"nom", "controls"}]`` : les contrôles sans groupe d'abord,
    puis chaque groupe dans l'ordre où son premier contrôle apparaît. Les
    contrôles étant déjà triés par ``order``, déplacer un contrôle déplace
    aussi son groupe.
    """
    groupes = {}
    for c in controls:
        groupes.setdefault(c.group or "", []).append(c)

    noms = ([""] if "" in groupes else []) + [g for g in groupes if g]
    return [{"nom": nom, "controls": groupes[nom]} for nom in noms]


def dashboard(request):
    """Tableau de bord.

    - Bloc Scénarios : apparaît dès qu'au moins un contrôle existe.
    - Blocs des modules : chaque module actif peut fournir
      ``dashboard/views.py`` avec ``bloc(request)`` (un bloc) ou
      ``blocs(request)`` (plusieurs).

    L'ordre et la largeur des blocs sont ceux enregistrés par le mode
    « Organiser » (modèle DashboardBlock) ; un bloc inconnu passe en fin.
    """
    controls = Control.objects.all()

    blocs = []
    if controls:
        blocs.append({
            "key": "scenarios",
            "titre": "Scénarios",
            "icone": "diagram-3",
            "html": None,  # rendu par le template (boutons interactifs)
        })
    groupes_controls = grouper_controls(controls)

    for m in Module.objects.filter(enabled=True):
        try:
            dash = importlib.import_module(f"modules.{m.name}.dashboard.views")
        except ImportError:
            continue  # le module n'a pas de bloc dashboard : normal
        try:
            if hasattr(dash, "blocs"):
                # Contrat étendu : plusieurs blocs [{titre, icone, html}]
                for i, b in enumerate(dash.blocs(request) or []):
                    if b.get("html"):
                        blocs.append({
                            "key": f"{m.name}.{i}",
                            "titre": b.get("titre") or m.label,
                            "icone": b.get("icone") or m.icon,
                            "html": b["html"],
                        })
            elif hasattr(dash, "bloc"):
                html = dash.bloc(request)
                if html:
                    blocs.append({
                        "key": f"{m.name}.0",
                        "titre": m.label,
                        "icone": m.icon,
                        "html": html,
                    })
        except Exception as exc:
            journal(f"Erreur du bloc dashboard : {exc}", module=m.name, level="ERROR")
            continue

    # Mise en page enregistrée (ordre + largeur)
    layout = {b.key: b for b in DashboardBlock.objects.all()}
    for b in blocs:
        conf = layout.get(b["key"])
        b["order"] = conf.order if conf else 999
        b["width"] = conf.width if conf else 6
        b["height"] = conf.height if conf else 0
    blocs.sort(key=lambda b: (b["order"], b["key"]))

    # Rafraîchissement automatique de la page (0 = désactivé). Réglage en
    # base plutôt que dans le navigateur : le tableau de bord se comporte
    # pareil depuis le PC ou le téléphone.
    try:
        refresh = int(get_setting("dashboard_refresh_minutes", default=5))
    except (TypeError, ValueError):
        refresh = 5

    return render(
        request,
        "core/dashboard.html",
        {
            "active_tab": "dashboard",
            "controls": controls,
            "groupes_controls": groupes_controls,
            "blocs": blocs,
            "organiser": request.GET.get("organiser") == "1",
            "largeurs": DashboardBlock.LARGEURS,
            "refresh_minutes": max(0, refresh),
            "refresh_choix": (0, 1, 2, 5, 10, 15, 30),
        },
    )


@require_POST
def control_move(request, pk):
    """Déplace un contrôle d'un cran dans la liste (mode ↑ / ↓).

    On échange l'ordre avec le voisin plutôt que de renuméroter toute la
    liste : c'est une seule écriture, et l'ordre des autres ne bouge pas.
    Les ordres des contrôles existants sont normalisés d'abord, sinon deux
    contrôles créés avec le même ordre par défaut ne s'échangeraient jamais.
    """
    sens = -1 if request.POST.get("sens") == "haut" else 1

    controls = list(Control.objects.all())
    for position, c in enumerate(controls):
        if c.order != position:
            c.order = position
            c.save(update_fields=["order"])

    index = next((i for i, c in enumerate(controls) if c.pk == pk), None)
    voisin = None if index is None else index + sens
    if index is not None and voisin is not None and 0 <= voisin < len(controls):
        a, b = controls[index], controls[voisin]
        a.order, b.order = b.order, a.order
        a.save(update_fields=["order"])
        b.save(update_fields=["order"])
        journal(f"Contrôle « {a.label} » déplacé")

    return redirect("core:configuration")


@require_POST
def dashboard_refresh(request):
    """Change la périodicité du rafraîchissement automatique du tableau de bord."""
    try:
        minutes = max(0, min(120, int(request.POST.get("minutes", 5))))
    except (TypeError, ValueError):
        minutes = 5
    set_setting("dashboard_refresh_minutes", str(minutes))
    return redirect("core:dashboard")


def a_propos(request):
    """Contenu de la fenêtre « À propos » (fragment HTML, chargé à la demande).

    Volontairement séparé du gabarit de base : l'état du système coûte
    quelques requêtes, inutile de les payer sur chaque page pour une fenêtre
    rarement ouverte.
    """
    import platform
    from datetime import date

    import django

    from django.conf import settings as conf

    debut = getattr(conf, "APP_ANNEE_DEBUT", date.today().year)
    annee = date.today().year
    annees = f"{debut}" if annee <= debut else f"{debut}–{annee}"

    lignes = liaisons.etat_des_liaisons()
    scenarios = Scenario.objects.all()
    return render(
        request,
        "core/_a_propos.html",
        {
            "modules": Module.objects.filter(enabled=True),
            "scheduler": scheduler.etat(),
            "nb_scenarios": len(scenarios),
            "nb_scenarios_actifs": sum(1 for s in scenarios if s.enabled),
            "nb_liaisons": len(lignes),
            "nb_liaisons_ok": sum(1 for l in lignes if l["etat"] == "ok"),
            "version_python": platform.python_version(),
            "version_django": django.get_version(),
            "auteur": getattr(conf, "APP_AUTEUR", ""),
            "annees": annees,
        },
    )


@require_POST
def liaison_save(request):
    """Branche un besoin d'un module sur l'info d'un autre module."""
    module = request.POST.get("module", "").strip()
    besoin = request.POST.get("besoin", "").strip()
    cible = request.POST.get("cible", "").strip()

    besoins = {b["nom"]: b for b in liaisons.besoins_du_module(module)}
    if module not in {m.name for m in Module.objects.all()} or besoin not in besoins:
        messages.error(request, "Liaison inconnue.")
        return redirect("core:configuration")

    # On n'accepte que les cibles réellement compatibles : un besoin branché
    # sur n'importe quoi échouerait plus tard, loin d'ici.
    if cible:
        choix = liaisons.fournisseurs_compatibles(besoins[besoin], consommateur=module)
        if cible not in {f"{c['module']}.{c['nom']}" for c in choix}:
            messages.error(request, "Cette source ne correspond pas au besoin.")
            return redirect("core:configuration")

    liaisons.set_liaison(module, besoin, cible)
    quoi = besoins[besoin]["libelle"]
    journal(
        f"Liaison « {quoi} » → {cible or 'aucune'}", module=module
    )
    messages.success(
        request,
        f"« {quoi} » branché sur {cible}." if cible else f"« {quoi} » débranché.",
    )
    return redirect("core:configuration")


@require_POST
def scheduler_rattrapage(request):
    """Règle la tolérance de rattrapage des déclencheurs horaires.

    Un scénario à heure fixe dont l'heure tombe pendant une veille de la
    machine était perdu pour la journée : ce réglage dit combien de minutes
    de retard restent acceptables pour un rattrapage au réveil (0 = aucun).
    """
    defaut = scheduler.RATTRAPAGE_DEFAUT
    try:
        minutes = max(0, min(120, int(request.POST.get("minutes", defaut))))
    except (TypeError, ValueError):
        minutes = defaut
    set_setting("rattrapage_min", str(minutes), module="scenarios")
    # La tolérance est portée par les jobs eux-mêmes : il faut les réinscrire.
    scheduler.refresh_scenarios()
    journal(f"Rattrapage des déclencheurs horaires : {minutes} min", module="scenarios")
    messages.success(
        request,
        f"Rattrapage réglé à {minutes} min."
        if minutes
        else "Rattrapage désactivé : un déclenchement manqué sera perdu.",
    )
    return redirect("core:configuration")


@require_POST
def dashboard_layout(request):
    """Enregistre l'ordre, les largeurs et les hauteurs (mode « Organiser »)."""
    ordre = request.POST.getlist("ordre")  # clés dans l'ordre d'affichage
    for position, key in enumerate(ordre):
        try:
            width = int(request.POST.get(f"largeur_{key}", 6))
        except ValueError:
            width = 6
        if width not in dict(DashboardBlock.LARGEURS):
            width = 6
        try:
            height = int(request.POST.get(f"hauteur_{key}", 0) or 0)
        except ValueError:
            height = 0
        if height:  # 0 = automatique ; sinon on borne à des valeurs utilisables
            height = max(DashboardBlock.HAUTEUR_MIN,
                         min(DashboardBlock.HAUTEUR_MAX, height))
        DashboardBlock.objects.update_or_create(
            key=key, defaults={"order": position, "width": width, "height": height}
        )
    journal("Disposition du tableau de bord enregistrée")
    messages.success(request, "Disposition du tableau de bord enregistrée.")
    return redirect("core:dashboard")


@require_POST
def dashboard_layout_reset(request):
    """Revient à la disposition par défaut."""
    DashboardBlock.objects.all().delete()
    journal("Disposition du tableau de bord réinitialisée")
    messages.success(request, "Disposition réinitialisée.")
    return redirect("core:dashboard")


def journal_view(request):
    """Onglet Journal : liste des logs avec filtres module et niveau."""
    logs = LogEntry.objects.all()

    module = request.GET.get("module", "")
    level = request.GET.get("level", "")
    if module:
        logs = logs.filter(module=module)
    if level:
        logs = logs.filter(level=level)

    # Modules proposés au filtre : ceux qui ont écrit dans le journal, plus
    # ceux qui sont installés. Se limiter aux premiers rendait un module
    # invisible dès qu'on venait de purger ses lignes — soit exactement quand
    # on cherche à savoir s'il en réécrit.
    noms = set(LogEntry.objects.values_list("module", flat=True).distinct())
    noms.update(Module.objects.filter(enabled=True).values_list("name", flat=True))
    noms.add("core")
    labels = dict(Module.objects.values_list("name", "label"))
    modules = [
        # « enphase (Énergie) » : le journal range sous le nom technique,
        # mais c'est le nom de l'onglet qu'on a en tête en le cherchant.
        {"nom": n, "libelle": f"{n} ({labels[n]})" if labels.get(n) else n}
        for n in sorted(noms)
    ]

    paginator = Paginator(logs, 50)
    page = paginator.get_page(request.GET.get("page"))

    # Rétention : affichée ici et pas dans la Configuration, parce que c'est
    # en regardant le journal qu'on se demande combien de temps il est gardé.
    jours = journal_jours_conserves()

    return render(
        request,
        "core/journal.html",
        {
            "active_tab": "journal",
            "page": page,
            "modules": modules,
            "levels": LogEntry.LEVEL_CHOICES,
            "current_module": module,
            "current_level": level,
            "total": LogEntry.objects.count(),
            # Nombre de lignes que le filtre courant affiche : c'est ce que
            # supprimerait « Purger le filtre », autant l'annoncer.
            "total_filtre": paginator.count,
            "filtre_actif": bool(module or level),
            "retention": jours,
            "retention_choix": sorted({0, 7, 30, 90, 180, 365, jours}),
        },
    )


@require_POST
def journal_purge(request):
    """Vide le journal : la sélection cochée, le filtre courant, ou tout.

    Trois portées plutôt qu'un seul bouton « tout effacer » : quand un module
    part en boucle d'erreur (une API injoignable qui écrit mille lignes), on
    veut pouvoir nettoyer ce module-là sans perdre l'historique du reste.
    """
    portee = request.POST.get("portee", "")
    module = request.POST.get("module", "")
    level = request.POST.get("level", "")

    if portee == "selection":
        ids = [int(i) for i in request.POST.getlist("ids") if i.isdigit()]
        if not ids:
            messages.info(request, "Aucune ligne sélectionnée.")
            return redirect(f"{reverse('core:journal')}?module={module}&level={level}")
        supprimes = supprimer_journal(ids=ids)
    elif portee == "filtre":
        supprimes = supprimer_journal(module=module, level=level)
    elif portee == "tout":
        supprimes = supprimer_journal()
        module = level = ""  # le filtre n'a plus rien à filtrer
    else:
        messages.error(request, "Portée de purge inconnue.")
        return redirect("core:journal")

    messages.success(
        request,
        f"{supprimes} entrée{'s' if supprimes > 1 else ''} supprimée"
        f"{'s' if supprimes > 1 else ''} du journal.",
    )
    return redirect(f"{reverse('core:journal')}?module={module}&level={level}")


@require_POST
def journal_retention(request):
    """Règle la durée de conservation du journal, et purge dans la foulée.

    Purger tout de suite plutôt qu'à la prochaine nuit : quelqu'un qui vient
    de ramener la rétention de 90 à 7 jours veut voir l'effet maintenant, pas
    demain matin.
    """
    try:
        jours = max(0, min(3650, int(request.POST.get("jours", JOURNAL_JOURS_DEFAUT))))
    except (TypeError, ValueError):
        jours = JOURNAL_JOURS_DEFAUT

    set_setting("journal_jours_conserves", str(jours))
    if jours:
        supprimes = purger_journal(jours)
        journal(f"Conservation du journal réglée à {jours} jours")
        messages.success(
            request,
            f"Journal conservé {jours} jours"
            + (f" — {supprimes} entrée(s) supprimée(s)." if supprimes else "."),
        )
    else:
        journal("Purge du journal désactivée : aucune entrée ne sera supprimée")
        messages.warning(
            request, "Purge désactivée : le journal grossira sans limite."
        )
    return redirect("core:journal")


def configuration(request):
    """Onglet Configuration : sections repliables (contrôles, modules,
    variables, liaisons, scheduler, scénarios).

    Chaque section porte un compteur dans son en-tête : la page repliée doit
    rester lisible, sinon replier revient à cacher l'information.
    """
    # Modules : fusion de ce qui est sur le disque et de l'état en base
    en_base = {m.name: m for m in Module.objects.all()}
    modules_detectes = []
    for info in scan_modules():
        db = en_base.pop(info["name"], None)
        info["enabled"] = db.enabled if db else False
        modules_detectes.append(info)
    # Modules en base mais disparus du disque
    modules_disparus = [m for m in en_base.values()]

    controls = Control.objects.all()
    scenarios = Scenario.objects.all()
    lignes_liaisons = liaisons.etat_des_liaisons()

    return render(
        request,
        "core/configuration.html",
        {
            "active_tab": "configuration",
            "controls": controls,
            "button_icons": BUTTON_ICONS,
            "modules_detectes": modules_detectes,
            "modules_disparus": modules_disparus,
            "scenarios": scenarios,
            # Résumés affichés dans les en-têtes repliés
            "resume": {
                "controles": len(controls),
                "modules_actifs": sum(1 for m in modules_detectes if m["enabled"]),
                "modules_total": len(modules_detectes),
                "variables": Variable.objects.count(),
                "liaisons_total": len(lignes_liaisons),
                "liaisons_attention": sum(
                    1 for l in lignes_liaisons if l["etat"] in ("manquant", "casse")
                ),
                "scenarios_actifs": sum(1 for s in scenarios if s.enabled),
                "scenarios_total": len(scenarios),
            },
            "variables": Variable.objects.all(),
            "scheduler": scheduler.etat(),
            "liaisons": lignes_liaisons,
        },
    )


@require_POST
def control_create(request):
    """Création d'un bouton poussoir ou d'un switch (popup de l'onglet Config)."""
    ctype = request.POST.get("type", "")
    name = request.POST.get("name", "").strip()
    label = request.POST.get("label", "").strip()
    icon = request.POST.get("icon", "").strip()
    group = request.POST.get("group", "").strip()

    if ctype not in (Control.BUTTON, Control.SWITCH):
        messages.error(request, "Type de contrôle inconnu.")
        return redirect("core:configuration")
    if not name or not label:
        messages.error(request, "Le nom et le label sont obligatoires.")
        return redirect("core:configuration")
    if Control.objects.filter(name__iexact=name).exists():
        messages.error(request, f"Un contrôle nommé « {name} » existe déjà.")
        return redirect("core:configuration")

    if ctype == Control.SWITCH:
        icon = ""  # pas d'icône pour les switchs
    else:
        group = ""  # groupe exclusif réservé aux switchs

    control = Control.objects.create(
        type=ctype, name=name, label=label, icon=icon, group=group
    )
    journal(f"Contrôle créé : {control}")
    messages.success(
        request,
        f"{control.get_type_display()} « {label} » créé. "
        "Il est visible dans le bloc Scénarios du tableau de bord.",
    )
    return redirect("core:configuration")


@require_POST
def control_delete(request, pk):
    """Suppression d'un contrôle depuis la liste de l'onglet Config."""
    control = get_object_or_404(Control, pk=pk)
    journal(f"Contrôle supprimé : {control}")
    messages.success(request, f"{control.get_type_display()} « {control.label} » supprimé.")
    control.delete()
    return redirect("core:configuration")


@require_POST
def control_action(request, pk):
    """Action depuis le tableau de bord : appui bouton ou bascule switch.

    Pour le moment l'action est simplement journalisée. À l'étape 6, elle
    déclenchera les scénarios liés au contrôle.
    """
    from .scenarios_engine import on_control_event

    control = get_object_or_404(Control, pk=pk)

    if control.type == Control.SWITCH:
        # set_control_state applique le groupe exclusif
        set_control_state(control, not control.is_on)
        etat = "activé" if control.is_on else "désactivé"
        journal(f"Switch « {control.label} » {etat}")
    else:
        journal(f"Bouton « {control.label} » pressé")

    lances = on_control_event(control)
    if lances:
        messages.success(
            request,
            f"{lances} scénario(s) lancé(s) — voir le Journal pour le détail.",
        )

    return redirect("core:dashboard")


@require_POST
def variable_save(request):
    """Création ou mise à jour d'une variable globale (carte Variables)."""
    name = request.POST.get("name", "").strip()
    value = request.POST.get("value", "").strip()
    description = request.POST.get("description", "").strip()

    if not name:
        messages.error(request, "Le nom de la variable est obligatoire.")
        return redirect("core:configuration")

    variable, created = Variable.objects.update_or_create(
        name=name, defaults={"value": value}
    )
    if description or created:
        variable.description = description or variable.description
        variable.save(update_fields=["description"])

    journal(f"Variable {'créée' if created else 'modifiée'} : {variable}")
    messages.success(request, f"Variable « {name} » = « {value} » enregistrée.")
    return redirect("core:configuration")


@require_POST
def variable_delete(request, pk):
    variable = get_object_or_404(Variable, pk=pk)
    journal(f"Variable supprimée : {variable.name}")
    messages.success(request, f"Variable « {variable.name} » supprimée.")
    variable.delete()
    return redirect("core:configuration")


@require_POST
def modules_valider(request):
    """Validation des modules cochés dans l'onglet Configuration.

    Met la base à jour puis déclenche le redémarrage du serveur pour que
    Django charge/décharge les apps correspondantes.
    """
    coches = set(request.POST.getlist("modules"))
    changements = []

    for info in scan_modules():
        actif = info["name"] in coches
        module, created = Module.objects.get_or_create(
            name=info["name"],
            defaults={
                "label": info["onglet"],
                "icon": info["icone"],
                "description": info["description"],
                "enabled": actif,
            },
        )
        if created:
            if actif:
                changements.append(f"« {module.label} » installé")
                journal(f"Module installé : {module.name}")
        else:
            # Rafraîchit le manifest (le conf.py a pu changer)
            module.label = info["onglet"]
            module.icon = info["icone"]
            module.description = info["description"]
            if module.enabled != actif:
                module.enabled = actif
                changements.append(
                    f"« {module.label} » {'activé' if actif else 'désactivé'}"
                )
                journal(f"Module {'activé' if actif else 'désactivé'} : {module.name}")
            module.save()

    if changements:
        messages.success(
            request,
            "Modules : " + ", ".join(changements) + ". "
            "Le serveur redémarre pour appliquer les changements — "
            "recharger la page dans quelques secondes.",
        )
        trigger_restart()
    else:
        messages.info(request, "Aucun changement dans les modules.")

    return redirect("core:configuration")


def module_tab(request, name):
    """Onglet d'un module actif : délègue au code du module.

    Contrat : le module fournit ``onglet/views.py`` avec une fonction
    ``onglet(request)`` qui retourne la page (template étendant
    ``core/base.html``). À défaut, une page « en construction » s'affiche.
    """
    module = get_object_or_404(Module, name=name, enabled=True)
    try:
        views_mod = importlib.import_module(f"modules.{name}.onglet.views")
    except (ImportError, AttributeError):
        # Pas d'onglet, ou module disparu du disque : page d'attente.
        return render(
            request,
            "core/module_placeholder.html",
            {"active_tab": f"module:{name}", "module": module},
        )

    try:
        return views_mod.onglet(request)
    except Exception as exc:
        # Un onglet qui explose (API injoignable, réglage aberrant) ne doit
        # pas rendre l'application inutilisable : on isole la panne dans son
        # onglet, le reste du tableau de bord continue de vivre.
        journal(f"Onglet en erreur : {exc}", module=name, level=LogEntry.ERROR)
        return render(
            request,
            "core/module_placeholder.html",
            {
                "active_tab": f"module:{name}",
                "module": module,
                "erreur": f"{type(exc).__name__} : {exc}",
            },
        )
