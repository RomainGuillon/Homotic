# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

"""Liaisons entre modules : besoins déclarés, branchés sur des infos.

Voir ``docs/09-liaisons-entre-modules.md``.

Le principe en une phrase : un module ne connaît jamais un autre module, il
connaît ses propres **besoins** ; l'utilisateur relie chaque besoin à une
**info** publiée par un autre module, et le socle résout la liaison au
moment de l'appel.

Contrat côté module consommateur, dans son ``conf.py`` ::

    BESOINS = [
        {"nom": "tarifs_jour",
         "libelle": "Tarifs électriques du jour",
         "type": "objet",
         "obligatoire": False,
         "sans": "le coût de la journée n'est pas affiché"},
    ]

Contrat côté fournisseur : une entrée ``INFOS`` avec un ``type`` autre que
« valeur » (``serie``, ``table``, ``objet``) — voir ``scenarios_engine``.

La liaison est stockée comme un réglage du module consommateur :
``besoin_<nom>`` = ``"tempo.tarifs_jour"``.
"""

import importlib

TYPES = {
    "valeur": "Valeur simple (nombre ou texte)",
    "serie": "Série de points (date, valeur)",
    "table": "Tableau de lignes",
    "objet": "Structure",
}


def _conf(module_name):
    """conf.py d'un module, ou None. Rechargé : les listes sont dynamiques."""
    try:
        conf = importlib.import_module(f"modules.{module_name}.conf")
        importlib.reload(conf)
        return conf
    except Exception:
        return None


def besoins_du_module(module_name):
    """Liste normalisée des BESOINS déclarés par un module."""
    conf = _conf(module_name)
    if conf is None:
        return []
    result = []
    for b in getattr(conf, "BESOINS", []):
        result.append({
            "nom": b.get("nom", "?"),
            "libelle": b.get("libelle") or b.get("nom", "?"),
            "type": b.get("type", "valeur"),
            "unite": b.get("unite", ""),
            "obligatoire": bool(b.get("obligatoire")),
            "sans": b.get("sans", ""),
        })
    return result


def cle_reglage(besoin_nom):
    """Nom du réglage qui mémorise la liaison d'un besoin."""
    return f"besoin_{besoin_nom}"


def liaison(module_name, besoin_nom):
    """Cible branchée sur un besoin (« tempo.tarifs_jour »), ou ""."""
    from .services import get_setting

    return str(get_setting(cle_reglage(besoin_nom), module=module_name, default="") or "")


def set_liaison(module_name, besoin_nom, cible):
    """Branche (ou débranche, si ``cible`` est vide) un besoin."""
    from .services import set_setting

    set_setting(cle_reglage(besoin_nom), str(cible or ""), module=module_name)


def fournisseurs_compatibles(besoin, consommateur=""):
    """Infos des modules actifs qui satisfont un besoin.

    Compatible = même ``type`` et même ``unite``. Un module ne se fournit pas
    lui-même : ce serait un appel direct déguisé.
    """
    from .scenarios_engine import available_infos

    return [
        i
        for i in available_infos(types=None)
        if i.get("type", "valeur") == besoin.get("type", "valeur")
        and (i.get("unite", "") or "") == (besoin.get("unite", "") or "")
        and i["module"] != consommateur
    ]


def lire_besoin(module_name, besoin_nom):
    """Lit la valeur branchée sur un besoin. Retourne ``(valeur, erreur)``.

    Ne lève jamais : le module consommateur ne doit voir qu'une raison
    lisible, jamais une trace Python. ``erreur`` est ``""`` quand tout va
    bien ; la valeur peut alors être ``None`` si le fournisseur n'a rien à
    répondre (Tempo sans couleur connue, calcul jamais lancé...) — c'est un
    cas normal, à distinguer d'une liaison cassée.
    """
    from .models import Module

    besoin = next(
        (b for b in besoins_du_module(module_name) if b["nom"] == besoin_nom), None
    )
    if besoin is None:
        return None, f"besoin « {besoin_nom} » non déclaré par le module"

    cible = liaison(module_name, besoin_nom)
    if not cible:
        return None, "besoin non branché (voir Configuration → Liaisons)"

    fournisseur, _, info_nom = cible.partition(".")
    if not fournisseur or not info_nom:
        return None, f"liaison illisible « {cible} »"

    if not Module.objects.filter(name=fournisseur, enabled=True).exists():
        return None, f"module fournisseur « {fournisseur} » absent ou désactivé"

    conf = _conf(fournisseur)
    entree = next(
        (i for i in getattr(conf, "INFOS", []) if i.get("nom") == info_nom), None
    ) if conf else None
    if entree is None:
        return None, f"l'info « {info_nom} » n'existe plus dans « {fournisseur} »"

    try:
        from .scenarios_engine import _call_module_function

        return _call_module_function(fournisseur, entree.get("fonction", "")), ""
    except Exception as exc:
        return None, f"lecture de « {cible} » en erreur : {exc}"


def etat_des_liaisons():
    """Toutes les liaisons des modules actifs, pour l'onglet Configuration.

    Une ligne par besoin : ce qui est branché, ce qui manque, et ce qu'on
    perd quand ce n'est pas branché.
    """
    from .models import Module

    modules = list(Module.objects.filter(enabled=True))
    labels = {m.name: m.label for m in modules}
    lignes = []

    for m in modules:
        for besoin in besoins_du_module(m.name):
            cible = liaison(m.name, besoin["nom"])
            choix = fournisseurs_compatibles(besoin, consommateur=m.name)
            valide = any(f"{c['module']}.{c['nom']}" == cible for c in choix)

            if not cible:
                etat = "manquant" if besoin["obligatoire"] else "libre"
                detail = besoin["sans"] or "non branché"
            elif not valide:
                etat = "casse"
                fournisseur = cible.split(".", 1)[0]
                detail = (
                    f"« {labels.get(fournisseur, fournisseur)} » ne fournit plus "
                    f"cette donnée"
                )
            else:
                etat = "ok"
                detail = ""

            lignes.append({
                "module": m.name,
                "module_label": m.label,
                "besoin": besoin,
                "cible": cible,
                "choix": choix,
                "etat": etat,
                "detail": detail,
            })

    lignes.sort(key=lambda x: (x["module_label"], x["besoin"]["libelle"]))
    return lignes
