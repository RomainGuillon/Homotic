# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

"""Onglet Alarme (Verisure) : état de l'alarme et paramétrage.

L'enrôlement 2FA se fait ici, en trois temps — demander le défi, choisir le
numéro et envoyer le SMS, saisir le code. L'étape courante n'est pas gardée
en session mais déduite des réglages (`otp_phones`, `otp_envoye`) : un
rafraîchissement de page ou un aller-retour ne perd donc pas le fil.
"""

from django.contrib import messages
from django.shortcuts import redirect, render

from core.services import get_setting, journal, set_setting

from ..fonctions import affichage, api


def _enregistrer_params(request):
    identifiant = request.POST.get("identifiant", "").strip()
    ancien = get_setting("identifiant", module=api.MODULE, default="")
    set_setting("identifiant", identifiant, module=api.MODULE)

    mot_de_passe = request.POST.get("mot_de_passe", "").strip()
    if mot_de_passe:  # champ vide = on conserve le mot de passe existant
        set_setting("mot_de_passe", mot_de_passe, module=api.MODULE, secret=True)

    pays = request.POST.get("pays", "FR").strip().upper()
    if pays in api.DOMAINES:
        set_setting("pays", pays, module=api.MODULE)

    brut = request.POST.get("tache_actualiser_minutes", "").strip()
    try:
        set_setting("tache_actualiser_minutes", str(max(0, int(brut))), module=api.MODULE)
    except ValueError:
        pass

    # Changer de compte invalide l'appareil de confiance et les jetons :
    # les garder ferait échouer la lecture avec un message incompréhensible.
    if identifiant and ancien and identifiant != ancien:
        api.reinitialiser_appareil()
        set_setting("numinst", "", module=api.MODULE)
        set_setting("panel", "", module=api.MODULE)
        set_setting("alias", "", module=api.MODULE)
        set_setting("otp_envoye", "", module=api.MODULE)
        messages.warning(
            request,
            "Changement d'identifiant : l'appareil devra être revalidé par SMS.",
        )

    journal("Paramètres mis à jour", module=api.MODULE)
    messages.success(request, "Paramètres Verisure enregistrés.")


def _traiter_post(request, action):
    if action == "params":
        _enregistrer_params(request)

    elif action == "refresh":
        _etat, _ts, erreur = api.etat_cached(force=True)
        if erreur:
            messages.warning(request, erreur)
        else:
            messages.success(request, "État de l'alarme actualisé.")

    elif action == "2fa_defi":
        telephones = api.demarrer_2fa()
        set_setting("otp_envoye", "", module=api.MODULE)
        messages.info(
            request,
            f"{len(telephones)} numéro(s) proposé(s) par Verisure — choisir "
            "lequel doit recevoir le code.",
        )

    elif action == "2fa_sms":
        api.envoyer_sms(request.POST.get("telephone", ""))
        set_setting("otp_envoye", "1", module=api.MODULE)
        messages.success(request, "SMS envoyé. Saisir le code reçu.")

    elif action == "2fa_code":
        api.valider_code(request.POST.get("code", ""))
        set_setting("otp_envoye", "", module=api.MODULE)
        messages.success(request, "Appareil validé. Plus de SMS à l'avenir.")
        api.etat_cached(force=True)

    elif action == "reset":
        api.reinitialiser_appareil()
        set_setting("otp_envoye", "", module=api.MODULE)
        journal("Identité d'appareil réinitialisée", module=api.MODULE)
        messages.warning(
            request,
            "Appareil oublié. La prochaine lecture demandera une validation par SMS.",
        )


def _etape_2fa():
    """« aucune », « numero » ou « code » — déduite des réglages, pas d'un
    état de session : un F5 ne casse rien."""
    if get_setting("otp_envoye", module=api.MODULE, default="") == "1":
        return "code"
    if api.telephones_2fa():
        return "numero"
    return "aucune"


def onglet(request):
    if request.method == "POST":
        action = request.POST.get("action", "")
        try:
            _traiter_post(request, action)
        except api.BloqueParWAF as exc:
            messages.error(request, f"{exc}")
        except api.ErreurVerisure as exc:
            messages.error(request, f"Échec : {exc}")
        except Exception as exc:  # garde-fou : l'onglet ne doit jamais planter
            messages.error(request, f"Échec inattendu : {exc}")
        return redirect("core:module_tab", name="verisure")

    configure = api.configured()
    etat, ts, erreur = (None, None, "")
    if configure:
        etat, ts, erreur = api.etat_cached()

    numinst = get_setting("numinst", module=api.MODULE, default="")
    capacites = api.capacites_lisibles(
        get_setting("capabilities", module=api.MODULE, default=""), numinst
    )

    carte = None
    if etat:
        carte = {
            **etat,
            # Pas de libellé dans le bouclier : le badge du bandeau le dit déjà.
            "svg": affichage.bouclier_svg(etat["cle"]),
            "badge": affichage.badge_classe(etat["cle"]),
            "change_le_texte": affichage.date_courte(etat.get("change_le")),
        }

    return render(
        request,
        "verisure/onglet.html",
        {
            "active_tab": "module:verisure",
            "configure": configure,
            "etat": carte,
            "ts": ts,
            "erreur": erreur,
            "etape_2fa": _etape_2fa(),
            "telephones": api.telephones_2fa(),
            "valide": bool(get_setting("refresh_token", module=api.MODULE, default="")),
            "capacites": capacites,
            "peut_desarmer": "DARM" in capacites,
            "params": {
                "identifiant": get_setting("identifiant", module=api.MODULE, default=""),
                "a_mot_de_passe": bool(get_setting("mot_de_passe", module=api.MODULE, default="")),
                "pays": api.pays(),
                "pays_possibles": sorted(api.DOMAINES),
                "minutes": get_setting("tache_actualiser_minutes", module=api.MODULE, default="3"),
                "numinst": numinst,
                "panel": get_setting("panel", module=api.MODULE, default=""),
                "alias": get_setting("alias", module=api.MODULE, default=""),
                "role": get_setting("role", module=api.MODULE, default=""),
            },
        },
    )
