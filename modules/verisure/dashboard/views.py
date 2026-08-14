"""Bloc Alarme du tableau de bord : bouclier, état, date du changement.

Le bloc passe par le même cache que l'onglet : afficher le tableau de bord ne
déclenche donc pas de requête tant que l'état a moins de trois minutes. C'est
ce qui permet de laisser le rafraîchissement automatique du tableau de bord
actif sans réveiller le pare-feu de Verisure.
"""

from django.template.loader import render_to_string

from ..fonctions import affichage, api


def bloc(request):
    if not api.configured():
        return render_to_string("verisure/_bloc.html", {"non_configure": True})

    etat, ts, erreur = api.etat_cached()
    return render_to_string(
        "verisure/_bloc.html",
        {
            "non_configure": False,
            "erreur": erreur if etat is None else "",
            # Une valeur ancienne vaut mieux que pas de valeur, mais il faut
            # que ça se voie : sur une alarme, croire un état périmé serait
            # pire que de n'en afficher aucun.
            "stale": bool(erreur) and etat is not None,
            "etat": etat,
            "ts": ts,
            "svg": affichage.bouclier_svg(etat["cle"], width=110) if etat else "",
            "badge": affichage.badge_classe(etat["cle"]) if etat else "",
            "change_le_texte": affichage.date_courte(etat.get("change_le")) if etat else "",
        },
    )
