"""Modèles du socle : journal (logs) et configuration clé/valeur.

Une seule table de logs pour toute l'application, avec une colonne
``module`` — l'onglet Journal filtre dessus. Idem pour la configuration :
une seule table clé/valeur, chaque module range ses réglages sous son nom.
"""

from django.db import models


class LogEntry(models.Model):
    """Une ligne de journal, tous modules confondus."""

    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    LEVEL_CHOICES = [
        (INFO, "Info"),
        (WARNING, "Avertissement"),
        (ERROR, "Erreur"),
    ]

    created_at = models.DateTimeField("date", auto_now_add=True, db_index=True)
    module = models.CharField("module", max_length=50, default="core", db_index=True)
    level = models.CharField("niveau", max_length=10, choices=LEVEL_CHOICES, default=INFO)
    message = models.TextField("message")

    class Meta:
        verbose_name = "entrée de journal"
        verbose_name_plural = "journal"
        ordering = ["-created_at"]

    def __str__(self):
        return f"[{self.created_at:%d/%m %H:%M:%S}] {self.module} {self.level} : {self.message[:60]}"


class Control(models.Model):
    """Un contrôle du bloc Scénarios : bouton poussoir ou switch.

    - ``name`` : identifiant technique unique, utilisé par les scénarios
    - ``label`` : texte affiché sur le tableau de bord
    - ``icon`` : icône Bootstrap Icons (boutons poussoirs uniquement)
    - ``is_on`` : état courant (switchs uniquement)
    """

    BUTTON = "BUTTON"
    SWITCH = "SWITCH"
    TYPE_CHOICES = [
        (BUTTON, "Bouton poussoir"),
        (SWITCH, "Switch"),
    ]

    type = models.CharField("type", max_length=10, choices=TYPE_CHOICES)
    name = models.CharField("nom", max_length=50, unique=True)
    label = models.CharField("label", max_length=100)
    icon = models.CharField("icône", max_length=50, blank=True, default="")
    is_on = models.BooleanField("état", default=False)
    group = models.CharField(
        "groupe", max_length=50, blank=True, default="",
        help_text="Les contrôles d'un même groupe sont affichés ensemble sous "
                  "son nom. Pour des switchs, un seul du groupe peut être ON.",
    )
    order = models.IntegerField("ordre", default=0)
    created_at = models.DateTimeField("créé le", auto_now_add=True)

    class Meta:
        verbose_name = "contrôle"
        verbose_name_plural = "contrôles"
        # L'ordre est réglable dans l'onglet Configuration ; à égalité, on
        # garde l'ordre de création pour rester stable.
        ordering = ["order", "created_at"]

    def __str__(self):
        return f"{self.get_type_display()} « {self.label} » ({self.name})"


class Module(models.Model):
    """Un module « plugin » détecté dans le répertoire ``modules/``.

    - ``name`` : nom du répertoire (identifiant technique)
    - ``label`` : nom de l'onglet, lu dans le ``conf.py`` du module
    - ``enabled`` : coché/validé dans l'onglet Configuration
    """

    name = models.CharField("nom", max_length=50, unique=True)
    label = models.CharField("onglet", max_length=100)
    icon = models.CharField("icône", max_length=50, blank=True, default="puzzle")
    description = models.TextField("description", blank=True, default="")
    enabled = models.BooleanField("activé", default=False)
    installed_at = models.DateTimeField("installé le", auto_now_add=True)

    class Meta:
        verbose_name = "module"
        verbose_name_plural = "modules"
        ordering = ["name"]

    def __str__(self):
        return f"{self.label} ({self.name}){'' if self.enabled else ' [inactif]'}"


class DashboardBlock(models.Model):
    """Position et largeur d'un bloc sur le tableau de bord.

    ``key`` identifie le bloc de façon stable : « scenarios » pour le bloc
    des boutons/switchs, « <module>.<index> » pour les blocs des modules.
    Les blocs inconnus (nouveau module) s'ajoutent en fin avec la largeur
    par défaut.
    """

    LARGEURS = [
        (3, "Un quart"),
        (4, "Un tiers"),
        (6, "Moitié"),
        (8, "Deux tiers"),
        (12, "Pleine largeur"),
    ]

    # Hauteur du bloc en pixels. 0 = automatique (le bloc prend la hauteur de
    # son contenu, comportement d'origine). Sinon c'est une hauteur réelle,
    # réglée à la poignée en mode « Organiser » : le bloc fait exactement
    # cette taille, et son contenu défile s'il dépasse.
    HAUTEUR_MIN, HAUTEUR_MAX = 120, 1400

    key = models.CharField("bloc", max_length=100, unique=True)
    order = models.IntegerField("ordre", default=0)
    width = models.IntegerField("largeur", choices=LARGEURS, default=6)
    height = models.IntegerField(
        "hauteur (px, 0 = automatique)", default=0,
        help_text="0 = hauteur du contenu ; sinon hauteur fixe du bloc.",
    )

    class Meta:
        verbose_name = "bloc du tableau de bord"
        verbose_name_plural = "blocs du tableau de bord"
        ordering = ["order", "id"]

    def __str__(self):
        return f"{self.key} (ordre {self.order}, largeur {self.width}/12)"


class Variable(models.Model):
    """Variable globale, commune à tous les modules et aux scénarios.

    Exemples : saison=hiver, nb_personnes=4, vacances=oui.
    Lisible/modifiable partout via ``core.services.get_variable`` /
    ``set_variable``, testable en condition et modifiable en action dans
    l'éditeur de scénarios.
    """

    name = models.CharField("nom", max_length=50, unique=True)
    value = models.CharField("valeur", max_length=200, blank=True, default="")
    description = models.CharField("description", max_length=200, blank=True, default="")
    updated_at = models.DateTimeField("modifiée le", auto_now=True)

    class Meta:
        verbose_name = "variable"
        verbose_name_plural = "variables"
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} = {self.value}"


class Scenario(models.Model):
    """Un scénario : déclencheur → conditions → actions (JSON).

    Structure de ``definition`` ::

        {
          "trigger": {"type": "heure", "heure": "06:30"}
                     | {"type": "bouton", "controle": "hiver"}
                     | {"type": "switch", "controle": "mode_absence", "etat": "on"}
                     | {"type": "manuel"},
          "conditions": [
              {"type": "switch", "controle": "hiver", "etat": "on"},
              {"type": "plage", "debut": "22:00", "fin": "06:00"}
          ],
          "actions": [
              {"type": "fonction", "module": "chauffe_eau",
               "fonction": "fonctions.scenario.chauffer", "nom": "chauffer"},
              {"type": "switch", "controle": "x", "etat": "on"},
              {"type": "scenario", "nom": "autre scénario"},
              {"type": "journal", "message": "..."}
          ]
        }
    """

    name = models.CharField("nom", max_length=100, unique=True)
    description = models.TextField("description", blank=True, default="")
    enabled = models.BooleanField("activé", default=True)
    definition = models.JSONField("définition", default=dict)
    last_run = models.DateTimeField("dernière exécution", null=True, blank=True)
    last_status = models.CharField("dernier statut", max_length=200, blank=True, default="")
    created_at = models.DateTimeField("créé le", auto_now_add=True)
    updated_at = models.DateTimeField("modifié le", auto_now=True)

    class Meta:
        verbose_name = "scénario"
        verbose_name_plural = "scénarios"
        ordering = ["name"]

    def __str__(self):
        return f"{self.name}{'' if self.enabled else ' [inactif]'}"

    @property
    def trigger_summary(self):
        t = (self.definition or {}).get("trigger", {})
        ttype = t.get("type")
        if ttype == "heure":
            return f"Tous les jours à {t.get('heure', '?')}"
        if ttype == "heure_calculee":
            if t.get("source") == "info":
                return f"Chaque jour à l'heure donnée par l'info « {t.get('nom', '?')} »"
            return f"Chaque jour à l'heure de la variable « {t.get('variable', '?')} »"
        if ttype == "changement":
            if t.get("source") == "info":
                cible = f"l'info « {t.get('nom', '?')} »"
            else:
                cible = f"la variable « {t.get('variable', '?')} »"
            if t.get("vers"):
                return f"Quand {cible} passe à « {t['vers']} »"
            return f"À chaque changement de {cible}"
        if ttype == "periodique":
            base = f"Toutes les {t.get('minutes', '?')} min"
            if t.get("debut") and t.get("fin"):
                base += f" entre {t['debut']} et {t['fin']}"
            return base
        if ttype == "bouton":
            return f"Bouton « {t.get('controle', '?')} »"
        if ttype == "switch":
            etat = "activé" if t.get("etat") == "on" else "désactivé"
            return f"Switch « {t.get('controle', '?')} » {etat}"
        return "Manuel / autre scénario"


class Setting(models.Model):
    """Configuration clé/valeur.

    ``module`` vaut "core" pour les réglages généraux de l'appli, ou le nom
    d'un module pour ses réglages propres (clé API, login, périodicité...).
    ``secret`` indique une valeur sensible à masquer dans l'interface.
    """

    module = models.CharField("module", max_length=50, default="core", db_index=True)
    key = models.CharField("clé", max_length=100)
    value = models.TextField("valeur", blank=True, default="")
    secret = models.BooleanField("valeur sensible", default=False)
    updated_at = models.DateTimeField("modifié le", auto_now=True)

    class Meta:
        verbose_name = "réglage"
        verbose_name_plural = "configuration"
        constraints = [
            models.UniqueConstraint(fields=["module", "key"], name="unique_setting_module_key"),
        ]

    def __str__(self):
        shown = "••••••" if self.secret else self.value[:60]
        return f"{self.module}.{self.key} = {shown}"
