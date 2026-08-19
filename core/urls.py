# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

from django.urls import path

from . import views, views_scenarios

app_name = "core"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("journal/", views.journal_view, name="journal"),
    path("journal/conservation/", views.journal_retention, name="journal_retention"),
    path("journal/purger/", views.journal_purge, name="journal_purge"),
    path("a-propos/", views.a_propos, name="a_propos"),
    path("tableau-de-bord/rafraichir/", views.dashboard_refresh, name="dashboard_refresh"),
    path("tableau-de-bord/disposition/", views.dashboard_layout, name="dashboard_layout"),
    path("tableau-de-bord/disposition/defaut/", views.dashboard_layout_reset, name="dashboard_layout_reset"),
    path("configuration/", views.configuration, name="configuration"),
    path("configuration/rattrapage/", views.scheduler_rattrapage, name="scheduler_rattrapage"),
    path("configuration/liaison/", views.liaison_save, name="liaison_save"),
    path("controle/creer/", views.control_create, name="control_create"),
    path("controle/<int:pk>/deplacer/", views.control_move, name="control_move"),
    path("controle/<int:pk>/supprimer/", views.control_delete, name="control_delete"),
    path("controle/<int:pk>/action/", views.control_action, name="control_action"),
    path("modules/valider/", views.modules_valider, name="modules_valider"),
    path("variables/enregistrer/", views.variable_save, name="variable_save"),
    path("variables/<int:pk>/supprimer/", views.variable_delete, name="variable_delete"),
    path("scenarios/nouveau/", views_scenarios.scenario_edit, name="scenario_new"),
    path("scenarios/<int:pk>/modifier/", views_scenarios.scenario_edit, name="scenario_edit"),
    path("scenarios/<int:pk>/supprimer/", views_scenarios.scenario_delete, name="scenario_delete"),
    path("scenarios/<int:pk>/basculer/", views_scenarios.scenario_toggle, name="scenario_toggle"),
    path("scenarios/<int:pk>/tester/", views_scenarios.scenario_run, name="scenario_run"),
    path("module/<str:name>/", views.module_tab, name="module_tab"),
]
