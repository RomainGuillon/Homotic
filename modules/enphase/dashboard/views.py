"""Blocs du tableau de bord : « Énergie maintenant » + « La journée »
(présentation v1, deux cartes comme sur le tableau de bord d'origine)."""

from django.template.loader import render_to_string

from ..fonctions import api
from ..onglet.views import _display_context, build_bilan_context, build_journee_context


def blocs(request):
    if not api.configured():
        return [{
            "titre": "Énergie maintenant",
            "icone": "lightning-charge",
            "html": render_to_string("enphase/_bloc.html", {"non_configure": True}),
        }]

    result = []

    data, _ts, erreur = api.get_energy_cached()
    if data is None:
        html = render_to_string("enphase/_bloc.html", {"non_configure": False, "erreur": erreur})
    else:
        # « compact » resserre les métriques pour laisser la place au
        # diagramme de la journée, intégré dans ce même bloc.
        context = {
            "non_configure": False, "erreur": "", "stale": bool(erreur),
            "e": data, "compact": True,
        }
        context.update(_display_context(data, compact=True))
        context.update(build_bilan_context(compact=True))
        html = render_to_string("enphase/_bloc.html", context)
    result.append({"titre": "Énergie maintenant", "icone": "lightning-charge", "html": html})

    journee_ctx = build_journee_context()
    result.append({
        "titre": "La journée",
        "icone": "house",
        "html": render_to_string("enphase/_journee.html", journee_ctx),
    })

    return result
