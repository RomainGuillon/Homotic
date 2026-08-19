# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

"""Helpers du socle, utilisables par tout le code (core et modules).

    from core.services import journal, get_setting, set_setting

    journal("Chauffe-eau démarré", module="chauffe_eau")
    cle = get_setting("api_key", module="enphase")
    set_setting("api_key", "xxx", module="enphase", secret=True)
"""

from .models import LogEntry, Setting, Variable


def journal(message, module="core", level=LogEntry.INFO):
    """Écrit une ligne dans le journal (visible dans l'onglet Journal)."""
    return LogEntry.objects.create(module=module, level=level, message=str(message))


# Rétention du journal, en jours. Le journal reçoit une quarantaine de lignes
# par jour (battement, tâches en erreur, scénarios), soit ~15 000 par an : rien
# de dramatique, mais rien ne le bornait non plus. Trois mois suffisent pour
# comprendre un incident ; au-delà, personne ne relit.
JOURNAL_JOURS_DEFAUT = 90


def journal_jours_conserves():
    """Nombre de jours de journal conservés (0 = pas de purge)."""
    try:
        return max(0, int(get_setting("journal_jours_conserves",
                                      default=JOURNAL_JOURS_DEFAUT)))
    except (TypeError, ValueError):
        return JOURNAL_JOURS_DEFAUT


def purger_journal(jours=None):
    """Supprime les entrées de journal plus vieilles que ``jours``.

    Retourne le nombre de lignes supprimées. La suppression est elle-même
    journalisée — une ligne par purge, c'est le prix à payer pour savoir que
    le ménage a bien eu lieu, et ça reste négligeable.
    """
    from datetime import timedelta

    from django.utils import timezone

    if jours is None:
        jours = journal_jours_conserves()
    if jours <= 0:
        return 0

    limite = timezone.now() - timedelta(days=jours)
    supprimes, _ = LogEntry.objects.filter(created_at__lt=limite).delete()
    if supprimes:
        journal(f"{supprimes} entrée(s) de journal purgée(s) (au-delà de {jours} jours)")
    return supprimes


def supprimer_journal(ids=None, module="", level=""):
    """Supprime des entrées de journal à la demande (bouton de l'onglet Journal).

    - ``ids`` : suppression d'une sélection précise (les cases cochées) ;
    - sinon ``module`` / ``level`` : suppression de tout ce que le filtre
      courant affiche — sans filtre, c'est donc tout le journal.

    Contrairement à ``purger_journal``, on ne journalise pas ici : la ligne
    « X entrées supprimées » ressusciterait juste après un « tout purger »,
    et un journal qu'on vient de vider doit rester vide.
    """
    qs = LogEntry.objects.all()
    if ids is not None:
        qs = qs.filter(pk__in=ids)
    else:
        if module:
            qs = qs.filter(module=module)
        if level:
            qs = qs.filter(level=level)
    supprimes, _ = qs.delete()
    return supprimes


def get_setting(key, module="core", default=None):
    """Retourne la valeur d'un réglage, ou ``default`` s'il n'existe pas."""
    try:
        return Setting.objects.get(module=module, key=key).value
    except Setting.DoesNotExist:
        return default


def set_setting(key, value, module="core", secret=False):
    """Crée ou met à jour un réglage."""
    obj, _ = Setting.objects.update_or_create(
        module=module,
        key=key,
        defaults={"value": "" if value is None else str(value), "secret": secret},
    )
    return obj


def set_control_state(control, on):
    """Met un switch à ON/OFF en respectant son groupe exclusif.

    Passer un switch à ON éteint les autres switchs du même groupe (deux
    switchs d'un groupe peuvent être OFF ensemble, jamais ON ensemble).
    À utiliser partout : tableau de bord, scénarios, code des modules.
    Retourne la liste des switchs éteints par exclusivité.
    """
    from .models import Control

    control.is_on = bool(on)
    control.save(update_fields=["is_on"])

    eteints = []
    if control.is_on and control.group:
        autres = Control.objects.filter(
            type=Control.SWITCH, group=control.group, is_on=True
        ).exclude(pk=control.pk)
        for autre in autres:
            autre.is_on = False
            autre.save(update_fields=["is_on"])
            eteints.append(autre)
            journal(
                f"Switch « {autre.label} » désactivé "
                f"(exclusif avec « {control.label} »)"
            )
    return eteints


def get_variable(name, default=None):
    """Valeur d'une variable globale, ou ``default`` si elle n'existe pas."""
    try:
        return Variable.objects.get(name=name).value
    except Variable.DoesNotExist:
        return default


def set_variable(name, value):
    """Crée ou met à jour une variable globale (commune à tous les modules)."""
    obj, _ = Variable.objects.update_or_create(
        name=name, defaults={"value": "" if value is None else str(value)}
    )
    return obj
